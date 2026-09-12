"""Characterization micro tests: PASS can mean an attack succeeded.

These are disposable research probes, not assertions of desired behavior.
All participant state and cloud objects live in pytest temporary directories.
"""

import base64
from dataclasses import replace
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
from cod_sync.protocol import CodSync
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore
from fastapi.testclient import TestClient

from cuttlefish.group import group_encrypt
from small_sea_hub.backend import SmallSeaBackend
from small_sea_hub.crypto import (
    SenderKeyUnavailableExn,
    decrypt_group_payload,
    prepare_encrypted_upload,
    serialize_group_message,
)
from small_sea_hub.server import app
from small_sea_manager import provisioning as p
from small_sea_manager.manager import TeamManager, bootstrap_existing_identity, create_identity_join_request
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path
from small_sea_note_to_self.sender_keys import load_peer_sender_key, load_team_sender_key


def session(backend, nickname, team="ProjectX", app_name="SmallSeaCollectiveCore"):
    pending, pin = backend.request_session(nickname, app_name, team, "Smoke Tests")
    return backend.confirm_session(pending, pin).hex()


def owners(db):
    with sqlite3.connect(db) as conn:
        return dict(conn.execute("SELECT device_key_id, teammate_id FROM team_device"))


@pytest.fixture()
def admitted(tmp_path):
    """Real team creation and quorum-one admission; local transport only.

    The invitee has fetched the proposal snapshot, not the finalization yet.
    No ownership or sender-key rows are inserted by this fixture.
    """
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    alice = p.create_new_participant(tmp_path, "Alice")
    bob = p.create_new_participant(tmp_path, "Bob")
    p.add_cloud_storage(tmp_path, alice, protocol="localfolder", url=str(cloud))
    team = p.create_team(tmp_path, alice, "ProjectX")
    p.register_app_for_participant(tmp_path, alice, "OtherApp")
    p.activate_app_for_team(tmp_path, alice, "ProjectX", "OtherApp")
    token = p.create_invitation(
        tmp_path, alice, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)}, invitee_label="Bob",
    )
    alice_sync = tmp_path / "Participants" / alice / "ProjectX" / "Sync"
    CodSync(Repo(alice_sync / ".git", alice_sync), LocalFolderStore(str(cloud))).publish()
    acceptance = p.accept_invitation(tmp_path, bob, token, inviter_store=LocalFolderStore(str(cloud)))
    p.complete_invitation_acceptance(tmp_path, alice, "ProjectX", acceptance)
    backend = SmallSeaBackend(root_dir=tmp_path)
    team_id = bytes.fromhex(team["team_id_hex"])
    return SimpleNamespace(
        root=tmp_path, alice=alice, bob=bob, backend=backend, team_id=team_id,
        alice_owner=bytes.fromhex(team["teammate_id_hex"]),
        alice_db=alice_sync / "core.db",
        bob_db=tmp_path / "Participants" / bob / "ProjectX" / "Sync" / "core.db",
        alice_sender=load_team_sender_key(device_local_db_path(tmp_path, alice), team_id),
        bob_sender=load_team_sender_key(device_local_db_path(tmp_path, bob), team_id),
    )


def test_real_admission_projection_is_directional(admitted):
    env = admitted
    a_owners, b_owners = owners(env.alice_db), owners(env.bob_db)
    assert a_owners[env.alice_sender.sender_device_key_id] == env.alice_owner
    assert b_owners[env.alice_sender.sender_device_key_id] == env.alice_owner
    assert env.bob_sender.sender_device_key_id in a_owners
    # The invitee has a usable local sender key before its own accepted row arrives.
    assert env.bob_sender.sender_device_key_id not in b_owners
    assert load_peer_sender_key(
        device_local_db_path(env.root, env.bob), env.team_id,
        env.alice_sender.sender_device_key_id,
    ) is not None
    assert load_peer_sender_key(
        device_local_db_path(env.root, env.alice), env.team_id,
        env.bob_sender.sender_device_key_id,
    ) is None


def test_current_http_reads_accept_cross_path_berth_and_writer(admitted, monkeypatch):
    env = admitted
    backend = env.backend
    own = session(backend, "Alice")
    other = session(backend, "Alice", app_name="OtherApp")
    peer = session(backend, "Bob")
    own_session = backend._lookup_session(own)
    _, payload = prepare_encrypted_upload(own_session, b"Alice's object")
    requested = []

    def download(path):
        requested.append(path)
        return True, payload, "fixture-etag"

    # Only placement/provider I/O is stubbed. Session, HTTP, and crypto are real.
    monkeypatch.setattr(backend, "_resolve_berth_cloud_or_raise", lambda s: object())
    monkeypatch.setattr(backend, "_require_own_storage_announcement", lambda s, c: None)
    monkeypatch.setattr(backend, "_make_materialized_storage_adapter",
                        lambda s, c: SimpleNamespace(download=download))
    peer_requests = []

    def peer_download(token, teammate, path):
        peer_requests.append((teammate, path))
        return download(path)

    monkeypatch.setattr(backend, "_download_peer_file", peer_download)
    monkeypatch.setattr(app.state, "backend", backend, raising=False)
    http = TestClient(app)
    cases = [
        (own, "/cloud_file", {"path": "original"}),
        (own, "/cloud_file", {"path": "substituted"}),
        (other, "/cloud_file", {"path": "original"}),
        (peer, "/peer_cloud_file", {"path": "original", "teammate_id": env.alice_owner.hex()}),
        (peer, "/peer_cloud_file", {
            "path": "original", "teammate_id": owners(env.alice_db)[env.bob_sender.sender_device_key_id].hex(),
        }),
    ]
    for token, route, params in cases:
        response = http.get(route, params=params, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert base64.b64decode(response.json()["data"]) == b"Alice's object"
    assert requested == [case[2]["path"] for case in cases]
    assert peer_requests[0][0] != peer_requests[1][0]
    assert own_session.berth_id != backend._lookup_session(other).berth_id


def test_healthy_other_team_has_no_matching_sender_key(admitted):
    env = admitted
    p.create_team(env.root, env.alice, "ProjectY")
    original = env.backend._lookup_session(session(env.backend, "Alice"))
    other = env.backend._lookup_session(session(env.backend, "Alice", team="ProjectY"))
    _, payload = prepare_encrypted_upload(original, b"original")
    assert decrypt_group_payload(original, payload) == b"original"
    with pytest.raises(SenderKeyUnavailableExn):
        decrypt_group_payload(other, payload)


def test_current_hub_accepts_changed_chain_label(admitted):
    env = admitted
    reader = env.backend._lookup_session(session(env.backend, "Bob"))
    _, message = group_encrypt(env.team_id, env.alice_sender, b"original")
    assert decrypt_group_payload(reader, serialize_group_message(message)) == b"original"
    changed = replace(message, sender_chain_id=b"different chain")
    assert decrypt_group_payload(reader, serialize_group_message(changed)) == b"original"


def test_current_hub_derives_before_authentication(admitted, monkeypatch):
    import small_sea_hub.crypto as crypto

    env = admitted
    reader = env.backend._lookup_session(session(env.backend, "Bob"))
    _, message = group_encrypt(env.team_id, env.alice_sender, b"original")
    before = load_peer_sender_key(device_local_db_path(env.root, env.bob), env.team_id,
                                  message.sender_device_key_id)
    calls = []
    derive = crypto._message_key_for

    def observed(message, record):
        calls.append(message.iteration)
        return derive(message, record)

    monkeypatch.setattr(crypto, "_message_key_for", observed)
    from cryptography.exceptions import InvalidSignature
    with pytest.raises(InvalidSignature):
        decrypt_group_payload(reader, serialize_group_message(
            replace(message, iteration=8, signature=b"\0" * 64)))
    assert calls == [8]  # Bounded probe; no resource-exhaustion attempt.
    assert before == load_peer_sender_key(device_local_db_path(env.root, env.bob), env.team_id,
                                         message.sender_device_key_id)
    assert decrypt_group_payload(reader, serialize_group_message(message)) == b"original"


def test_actual_note_to_self_is_not_a_provisioned_group(tmp_path):
    alice = p.create_new_participant(tmp_path, "Alice")
    backend = SmallSeaBackend(root_dir=tmp_path)
    nts = backend._lookup_session(session(backend, "Alice", team="NoteToSelf"))
    with sqlite3.connect(note_to_self_sync_db_path(tmp_path, alice)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "team_device" not in tables
        assert conn.execute("SELECT COUNT(*) FROM user_device").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM team_device_key WHERE team_id=?", (nts.team_id,)).fetchone()[0] == 0
    assert load_team_sender_key(device_local_db_path(tmp_path, alice), nts.team_id) is None
    with pytest.raises(ValueError, match="No team sender key"):
        prepare_encrypted_upload(nts, b"NoteToSelf object")


def test_linked_devices_establish_real_sender_key_to_owner_chain(tmp_path):
    import publication_prototype as proto

    root1, root2, cloud = (tmp_path / name for name in ("first", "second", "cloud"))
    for path in (root1, root2, cloud):
        path.mkdir()
    alice = p.create_new_participant(root1, "Alice")
    p.add_cloud_storage(root1, alice, protocol="localfolder", url=str(cloud))
    manager1 = TeamManager(root1, alice)
    team = manager1.create_team("ProjectX")
    team_id = bytes.fromhex(team["team_id_hex"])
    owner = bytes.fromhex(team["teammate_id_hex"])
    join = create_identity_join_request(root2)
    welcome = manager1.authorize_identity_join(join["join_request_artifact"])
    bootstrap_existing_identity(root2, welcome["welcome_bundle"])
    # Simulate delivery of the existing Core snapshot, without manufacturing
    # device associations or copying the first installation's private state.
    src = root1 / "Participants" / alice / "ProjectX" / "Sync"
    dst = root2 / "Participants" / alice / "ProjectX" / "Sync"
    shutil.copytree(src, dst)
    manager2 = TeamManager(root2, alice)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")
    welcome = manager1.create_linked_device_bootstrap("ProjectX", prepared["join_request_bundle"])
    manager2.finalize_linked_device_bootstrap("ProjectX", welcome["bootstrap_bundle"])
    db1, db2 = device_local_db_path(root1, alice), device_local_db_path(root2, alice)
    sender1, sender2 = load_team_sender_key(db1, team_id), load_team_sender_key(db2, team_id)
    assert sender1.sender_device_key_id != sender2.sender_device_key_id
    assert owners(src / "core.db")[sender2.sender_device_key_id] == owner
    assert owners(dst / "core.db")[sender1.sender_device_key_id] == owner
    assert load_peer_sender_key(db1, team_id, sender2.sender_device_key_id) is None
    artifacts = p.redistribute_sender_key(root2, alice, "ProjectX")["artifacts"]
    assert len(artifacts) == 1
    p.receive_sender_key_distribution(root1, alice, "ProjectX", artifacts[0]["distribution_payload"])
    expected = proto.context(team_id, bytes.fromhex(team["berth_id_hex"]), "object")
    for sender, receiver_db, core_db in ((sender1, db2, dst / "core.db"), (sender2, db1, src / "core.db")):
        record = load_peer_sender_key(receiver_db, team_id, sender.sender_device_key_id)
        assert (record.signing_public_key, record.chain_id) == (sender.signing_public_key, sender.chain_id)
        mapping = {device: {teammate} for device, teammate in owners(core_db).items()}
        _, publication = proto.seal(sender, expected, b"sibling publication")
        assert proto.open_publication(publication, record, expected_context=expected,
                                      expected_publisher=owner, ownership=mapping)[1] == b"sibling publication"


def test_http_preserves_exact_logical_path_strings(admitted, monkeypatch):
    import publication_prototype as proto

    backend = admitted.backend
    token = session(backend, "Alice")
    uploads, downloads = [], []

    def upload(token, path, data, expected_etag=None):
        uploads.append(path)
        return True, "etag", ""

    def download(token, path):
        downloads.append(path)
        return True, b"fixture", "etag"

    # This probe checks only the real HTTP decoding boundary, not crypto wiring.
    monkeypatch.setattr(backend, "upload_to_cloud", upload)
    monkeypatch.setattr(backend, "download_from_cloud", download)
    monkeypatch.setattr(app.state, "backend", backend, raising=False)
    http = TestClient(app)
    paths = ["percent%2Fname", "percent/name", "plus+name", "plus name", "caf\u00e9", "cafe\u0301"]
    for path in paths:
        headers = {"Authorization": f"Bearer {token}"}
        assert http.post("/cloud_file", headers=headers, json={"path": path, "data": ""}).status_code == 200
        assert http.get("/cloud_file", headers=headers, params={"path": path}).status_code == 200
    assert uploads == downloads == paths
    assert len({proto.context(b"team", b"berth", path) for path in paths}) == len(paths)
