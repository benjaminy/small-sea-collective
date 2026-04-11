import pathlib
import sqlite3
import time
import json
import base64

import cod_sync.protocol as CodSync
import small_sea_hub.backend as SmallSea
from cuttlefish import generate_bootstrap_signing_keypair, open_welcome_bundle, seal_welcome_bundle
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager, bootstrap_existing_identity, create_identity_join_request
from small_sea_manager.provisioning import (
    _push_note_to_self_to_local_remote,
    _single_note_to_self_remote_descriptor,
    add_cloud_storage,
    create_new_participant,
)
from small_sea_note_to_self.bootstrap import (
    SignedWelcomeBundle,
    deserialize_join_request_artifact,
    deserialize_signed_welcome_bundle_plaintext,
    serialize_signed_welcome_bundle_plaintext,
    welcome_bundle_aad,
)
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path


def _count_rows(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def _pending_join_state(root_dir):
    return json.loads((pathlib.Path(root_dir) / ".small-sea-manager" / "pending_identity_join.json").read_text())


def _rewrite_welcome_bundle(root_dir, welcome_bundle_b64, mutate):
    state = _pending_join_state(root_dir)
    artifact = deserialize_join_request_artifact(state["join_request_artifact"])
    private_key = pathlib.Path(state["encryption_private_key_ref"]).read_bytes()
    aad = welcome_bundle_aad(
        joining_device_id_hex=artifact.device_id_hex,
        version=1,
    )
    plaintext = open_welcome_bundle(
        private_key,
        base64.b64decode(welcome_bundle_b64.encode("ascii")),
        associated_data=aad,
    )
    signed_bundle = deserialize_signed_welcome_bundle_plaintext(plaintext)
    mutated = mutate(signed_bundle)
    sealed = seal_welcome_bundle(
        bytes.fromhex(artifact.device_encryption_public_key_hex),
        serialize_signed_welcome_bundle_plaintext(mutated),
        associated_data=aad,
    )
    return base64.b64encode(sealed).decode("ascii")


def _open_signed_welcome_bundle(root_dir, welcome_bundle_b64):
    state = _pending_join_state(root_dir)
    artifact = deserialize_join_request_artifact(state["join_request_artifact"])
    private_key = pathlib.Path(state["encryption_private_key_ref"]).read_bytes()
    aad = welcome_bundle_aad(
        joining_device_id_hex=artifact.device_id_hex,
        version=1,
    )
    plaintext = open_welcome_bundle(
        private_key,
        base64.b64decode(welcome_bundle_b64.encode("ascii")),
        associated_data=aad,
    )
    return deserialize_signed_welcome_bundle_plaintext(plaintext)


def _open_session(http, participant, team, mode="passthrough"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": participant,
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


def test_localfolder_identity_bootstrap_roundtrip(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    assert welcome["auth_string"] == join_request["auth_string"]

    bootstrap = bootstrap_existing_identity(root2, welcome["welcome_bundle"])
    assert bootstrap["participant_hex"] == alice_hex
    assert bootstrap["second_confirmation_string"] == welcome["second_confirmation_string"]

    shared1 = note_to_self_sync_db_path(root1, alice_hex)
    shared2 = note_to_self_sync_db_path(root2, alice_hex)
    local2 = device_local_db_path(root2, alice_hex)
    assert shared1.exists()
    assert shared2.exists()
    assert local2.exists()

    assert _count_rows(shared1, "SELECT COUNT(*) FROM user_device") == 2
    assert _count_rows(shared2, "SELECT COUNT(*) FROM user_device") == 2
    assert _count_rows(local2, "SELECT COUNT(*) FROM cloud_storage_credential") == 0
    assert _count_rows(local2, "SELECT COUNT(*) FROM note_to_self_device_key_secret") == 1

    joined_device_id = bytes.fromhex(bootstrap["joining_device_id_hex"])
    with sqlite3.connect(local2) as conn:
        row = conn.execute(
            """
            SELECT encryption_private_key_ref, signing_private_key_ref
            FROM note_to_self_device_key_secret
            WHERE device_id = ?
            """,
            (joined_device_id,),
        ).fetchone()
    assert row is not None
    assert pathlib.Path(row[0]).exists()
    assert pathlib.Path(row[1]).exists()

    manager2 = TeamManager(root2, alice_hex)
    create_team_result = manager2.create_team("JoinedDeviceTeam")
    team_id = bytes.fromhex(create_team_result["team_id_hex"])
    with sqlite3.connect(shared2) as conn:
        team_device_row = conn.execute(
            "SELECT device_id FROM team_device_key WHERE team_id = ?",
            (team_id,),
        ).fetchone()
    assert team_device_row is not None
    assert team_device_row[0] == joined_device_id


def test_identity_bootstrap_bundle_expiry_and_reissue(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    expired = alice_manager.authorize_identity_join(
        join_request["join_request_artifact"],
        expires_in_seconds=1,
    )
    time.sleep(1.2)

    try:
        bootstrap_existing_identity(root2, expired["welcome_bundle"])
        assert False, "Expected expired welcome bundle to fail"
    except ValueError as exn:
        assert "expired" in str(exn).lower()

    fresh = alice_manager.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap = bootstrap_existing_identity(root2, fresh["welcome_bundle"])
    assert bootstrap["participant_hex"] == alice_hex


def test_identity_bootstrap_rejects_unknown_signer_and_blocks_installation(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    tampered_bundle = _rewrite_welcome_bundle(
        root2,
        welcome["welcome_bundle"],
        lambda signed: SignedWelcomeBundle(
            version=signed.version,
            bundle=signed.bundle,
            authorizing_device_id_hex="ff" * 16,
            signature_hex=signed.signature_hex,
        ),
    )

    try:
        bootstrap_existing_identity(root2, tampered_bundle)
        assert False, "Expected unknown signer bootstrap to fail"
    except ValueError as exn:
        assert "signature verification failed" in str(exn).lower()

    try:
        TeamManager(root2, alice_hex)
        assert False, "Expected blocked install to refuse TeamManager initialization"
    except ValueError as exn:
        assert "blocked" in str(exn).lower()


def test_identity_bootstrap_rejects_wrong_known_signing_key(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    shared1 = note_to_self_sync_db_path(root1, alice_hex)
    with sqlite3.connect(shared1) as conn:
        signer_id = conn.execute("SELECT id FROM user_device ORDER BY id LIMIT 1").fetchone()[0]
        _, wrong_public = generate_bootstrap_signing_keypair()
        conn.execute(
            "UPDATE user_device SET signing_key = ? WHERE id = ?",
            (wrong_public, signer_id),
        )
        conn.commit()

    repo_dir = root1 / "Participants" / alice_hex / "NoteToSelf" / "Sync"
    CodSync.gitCmd(["-C", str(repo_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(repo_dir), "commit", "-m", "Rotate signer for test"])
    _push_note_to_self_to_local_remote(
        root1,
        alice_hex,
        _single_note_to_self_remote_descriptor(root1, alice_hex),
    )

    try:
        bootstrap_existing_identity(root2, welcome["welcome_bundle"])
        assert False, "Expected wrong known signer bootstrap to fail"
    except ValueError as exn:
        assert "signature verification failed" in str(exn).lower()


def test_identity_bootstrap_via_hub_bootstrap_transport(playground_dir, minio_server_gen):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    root1.mkdir()
    root2.mkdir()

    minio = minio_server_gen(port=19660)

    alice_hex = create_new_participant(root1, "Alice")

    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root1), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)

    alice_nts_token = _open_session(http_a, "Alice", "NoteToSelf")
    backend_a.add_cloud_location(
        alice_nts_token,
        "s3",
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex, _http_client=http_a)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])
    signed = _open_signed_welcome_bundle(root2, welcome["welcome_bundle"])
    assert signed.bundle.remote_descriptor["protocol"] == "s3"
    assert signed.bundle.remote_descriptor["url"] == minio["endpoint"]
    assert signed.bundle.remote_descriptor["bucket"].startswith("ss-")

    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root2), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)

    bootstrap = bootstrap_existing_identity(
        root2,
        welcome["welcome_bundle"],
        _http_client=http_b,
    )
    assert bootstrap["participant_hex"] == alice_hex
    assert bootstrap["second_confirmation_string"] == welcome["second_confirmation_string"]

    shared2 = note_to_self_sync_db_path(root2, alice_hex)
    assert shared2.exists()
    assert _count_rows(shared2, "SELECT COUNT(*) FROM user_device") == 2

    sync_dir = root2 / "Participants" / alice_hex / "NoteToSelf" / "Sync"
    head = CodSync.gitCmd(["-C", str(sync_dir), "rev-parse", "HEAD"]).stdout.strip()
    assert head


def test_bootstrap_transport_token_is_rejected_by_normal_routes(playground_dir, minio_server_gen):
    workspace = pathlib.Path(playground_dir)
    root = workspace / "install-a"
    root.mkdir()
    minio = minio_server_gen(port=19680)

    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    resp = http.post(
        "/bootstrap/sessions",
        json={
            "protocol": "s3",
            "url": minio["endpoint"],
            "bucket": "bootstrap-bucket",
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    info_resp = http.get("/session/info", headers=auth)
    assert info_resp.status_code >= 400

    upload_resp = http.post(
        "/cloud_file",
        json={"path": "hello.txt", "data": base64.b64encode(b"hello").decode()},
        headers=auth,
    )
    assert upload_resp.status_code >= 400
