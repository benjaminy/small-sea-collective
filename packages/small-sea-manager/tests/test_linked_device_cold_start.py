"""Micro tests for materializing a discovered team on a sibling device (#235).

Device B is a second device of the same participant.
It learns about the team through NoteToSelf refresh and gets its Core baseline
only from the signed, encrypted linked-team bootstrap response.
No files move between the two installation roots.

These use MinIO because Hub-mediated NoteToSelf sync needs a real cloud backend.
"""

import base64
import json
import pathlib

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from cod_sync.repo import Repo
from wrasse_trust.keys import ProtectionLevel, generate_key_pair
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_note_to_self.db import device_local_db_path
from small_sea_note_to_self.sender_keys import load_peer_sender_key, load_team_sender_key

TEAM = "SharedProject"


def _open_session(http, nickname, team, mode="encrypted"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _team_sync_dir(root, participant_hex):
    return pathlib.Path(root) / "Participants" / participant_hex / TEAM / "Sync"


def _discovered_team_on_device_b(workspace, minio):
    """A creates the participant, cloud, and team; B joins and discovers it."""
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    alice_hex = Provisioning.create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)
    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = Provisioning.add_cloud_storage(
        root_a, alice_hex, protocol="s3", url=minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a, alice_hex, backend_a._lookup_session(nts_token_a).berth_id, cloud_storage_id,
    )
    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.push_note_to_self()

    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root_b, welcome["welcome_bundle"], _http_client=http_a)

    # B enrolls its own credentials for the inherited account (#237).
    manager_b_local = TeamManager(root_b, alice_hex)
    accounts = manager_b_local.list_cloud_storage()
    manager_b_local.connect_cloud_storage_credentials(
        accounts[0]["id"], access_key=minio["access_key"], secret_key=minio["secret_key"],
    )

    # The team is created after B joined, so only refresh can reveal it.
    manager_a.create_team(TEAM)
    manager_a.push_note_to_self()

    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)
    manager_b.refresh_note_to_self()
    discovered = next(t for t in manager_b.list_known_teams() if t["name"] == TEAM)
    assert discovered["joined_locally"] is False
    return root_a, root_b, alice_hex, manager_a, manager_b


def _resign(root_a, alice_hex, bundle, **changes):
    """Re-sign a bootstrap response as device A after changing fields."""
    response = Provisioning._untokenize(bundle)
    response.update(changes)
    signature = response.pop("team_device_signature")
    private_key, _public = Provisioning.get_current_team_device_key(root_a, alice_hex, TEAM)
    signing_key = changes.pop("_signing_private_key", None) or private_key
    response.pop("_signing_private_key", None)
    response["team_device_signature"] = Provisioning._sign_bytes(
        signing_key, Provisioning._json_bytes(response)
    ).hex()
    assert response["team_device_signature"] != signature
    return Provisioning._tokenize(response)


def test_sibling_device_materializes_discovered_team_through_bootstrap(
    playground_dir, minio_server_gen
):
    root_a, root_b, alice_hex, manager_a, manager_b = _discovered_team_on_device_b(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    assert not _team_sync_dir(root_b, alice_hex).exists()

    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])
    manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])

    signed_head = Provisioning._untokenize(created["bootstrap_bundle"])["core_head"]
    sync_a, sync_b = _team_sync_dir(root_a, alice_hex), _team_sync_dir(root_b, alice_hex)
    assert Repo(sync_a / ".git", sync_a).head() == signed_head
    assert Repo(sync_b / ".git", sync_b).head() == signed_head
    detail = manager_b.get_team(TEAM)
    assert detail["joined_locally"] is True
    assert detail["self_in_team"] == manager_a.get_team(TEAM)["self_in_team"]

    # The two devices can now exchange sender keys the supported way.
    team_id = Provisioning._team_row(root_b, alice_hex, TEAM)[0]
    redistribution = Provisioning.redistribute_sender_key(root_b, alice_hex, TEAM)
    assert len(redistribution["artifacts"]) == 1
    Provisioning.receive_sender_key_distribution(
        root_a, alice_hex, TEAM, redistribution["artifacts"][0]["distribution_payload"],
    )
    b_sender = load_team_sender_key(device_local_db_path(root_b, alice_hex), team_id)
    assert load_peer_sender_key(
        device_local_db_path(root_a, alice_hex), team_id, b_sender.sender_device_key_id
    ) is not None


def test_baseline_with_unsigned_head_is_rejected_without_a_clone(
    playground_dir, minio_server_gen
):
    root_a, root_b, alice_hex, manager_a, manager_b = _discovered_team_on_device_b(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])

    # A correctly signed response whose head is not what the bundle carries.
    lying = _resign(root_a, alice_hex, created["bootstrap_bundle"], core_head="0" * 40)
    with pytest.raises(ValueError, match="signed Core head"):
        manager_b.finalize_linked_device_bootstrap(TEAM, lying)
    assert not Provisioning.has_local_team_clone(root_b, alice_hex, TEAM)
    assert not _team_sync_dir(root_b, alice_hex).exists()

    # The genuine response still works afterwards.
    manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])
    assert Provisioning.has_local_team_clone(root_b, alice_hex, TEAM)


def test_baseline_from_untrusted_signer_is_rejected_without_a_clone(
    playground_dir, minio_server_gen
):
    root_a, root_b, alice_hex, manager_a, manager_b = _discovered_team_on_device_b(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])

    stranger_key, stranger_private_key = generate_key_pair(ProtectionLevel.DAILY)
    forged = _resign(
        root_a, alice_hex, created["bootstrap_bundle"],
        authorizing_team_device_public_key=stranger_key.public_key.hex(),
        _signing_private_key=stranger_private_key,
    )
    with pytest.raises(ValueError, match="not trusted"):
        manager_b.finalize_linked_device_bootstrap(TEAM, forged)
    assert not _team_sync_dir(root_b, alice_hex).exists()
