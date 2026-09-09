"""Two trusted installations of one participant, using local MinIO and Hub clients.

The Hub's FastAPI app holds one active backend. Switch devices with _reattach;
these scenarios run sequentially within a process.
"""

import shutil

from fastapi.testclient import TestClient
import small_sea_hub.backend as SmallSea
from small_sea_hub.server import app
from small_sea_manager import provisioning
from small_sea_manager.manager import (
    TeamManager, bootstrap_existing_identity, create_identity_join_request,
)

TEAM = "ProjectX"


def _open_session(http, nickname, team, mode="encrypted"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "issue-238 probe",
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

def _copy_team_clone(src_root, dst_root, participant_hex, team_name):
    src = provisioning._team_sync_dir(src_root, participant_hex, team_name).parent
    dst = provisioning._team_sync_dir(dst_root, participant_hex, team_name).parent
    shutil.copytree(src, dst)

def _core_allocation(root, participant_hex):
    state = provisioning.derive_team_join_state(root, participant_hex, TEAM)
    return state["core_berth_id"], state["allocation"]

def _two_installations(workspace, minio, http_a_holder):
    """Alice on two installations, both trusted device keys, one Core berth."""
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    alice_hex = provisioning.create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(
        root_dir=str(root_a), auto_approve_sessions=True
    )
    app.state.backend = backend_a
    http_a = TestClient(app)
    http_a_holder["backend_a"] = backend_a
    http_a_holder["http_a"] = http_a

    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = provisioning.add_cloud_storage(
        root_a,
        alice_hex,
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a,
        alice_hex,
        backend_a._lookup_session(nts_token_a).berth_id,
        cloud_storage_id,
    )

    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    team_result = manager_a.create_team(TEAM)
    teammate_id = bytes.fromhex(team_result["teammate_id_hex"])
    manager_a.push_note_to_self()

    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root_b, welcome["welcome_bundle"], _http_client=http_a)

    manager_b_local = TeamManager(root_b, alice_hex)
    accounts = manager_b_local.list_cloud_storage()
    assert accounts, "device B inherited no cloud_storage row"
    manager_b_local.connect_cloud_storage_credentials(
        accounts[0]["id"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    _copy_team_clone(root_a, root_b, alice_hex, TEAM)

    manager_b = TeamManager(root_b, alice_hex, _http_client=http_a)
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(
        TEAM, prepared["join_request_bundle"]
    )
    manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])

    state_a = provisioning.derive_team_join_state(root_a, alice_hex, TEAM)
    state_b = provisioning.derive_team_join_state(root_b, alice_hex, TEAM)
    assert state_a["admission"] == state_b["admission"] == "finalized"
    assert state_a["core_berth_id"] == state_b["core_berth_id"]
    assert state_a["device_key_id"] != state_b["device_key_id"]

    return {
        "alice_hex": alice_hex,
        "root_a": root_a,
        "root_b": root_b,
        "manager_a": manager_a,
        "teammate_id": teammate_id,
        "berth_id": state_a["core_berth_id"],
    }

def _reattach(root, alice_hex):
    """Point the shared app back at `root`'s backend and hand back a Manager."""
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)
    return TeamManager(root, alice_hex, _http_client=http)

def _redistribute_b_sender_key(root_a, root_b, alice_hex):
    """Hand device B's team sender key to device A."""
    state_a = provisioning.derive_team_join_state(root_a, alice_hex, TEAM)
    redistribution = provisioning.redistribute_sender_key(
        root_b, alice_hex, TEAM, target_device_key_ids=[state_a["device_key_id"]]
    )
    assert redistribution["skipped_device_key_ids_hex"] == []
    provisioning.receive_sender_key_distribution(
        root_a, alice_hex, TEAM, redistribution["artifacts"][0]["distribution_payload"]
    )
    return redistribution
