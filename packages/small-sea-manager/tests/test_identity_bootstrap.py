import dataclasses
import pathlib
import sqlite3
import time
import json
import base64

import cod_sync.protocol as CodSync
from cod_sync.store import LocalFolderStore, StoreProviderError
from cod_sync.git import gitCmd
import pytest
import small_sea_hub.backend as SmallSea
from cryptography.exceptions import InvalidTag
from cuttlefish import generate_bootstrap_signing_keypair, open_welcome_bundle, seal_welcome_bundle
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager, bootstrap_existing_identity, create_identity_join_request
from small_sea_manager.provisioning import (
    _push_note_to_self_to_local_remote,
    _single_note_to_self_remote_descriptor,
    add_berth_cloud_allocation_by_berth_id,
    add_cloud_storage,
    create_new_participant,
)
from small_sea_note_to_self.bootstrap import (
    JOIN_REQUEST_ARTIFACT_VERSION,
    SIGNED_WELCOME_BUNDLE_VERSION,
    SignedWelcomeBundle,
    WELCOME_BUNDLE_VERSION,
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


def _rewrite_welcome_bundle(root_dir, welcome_bundle_b64, mutate, *, reseal_aad_version=None):
    state = _pending_join_state(root_dir)
    artifact = deserialize_join_request_artifact(state["join_request_artifact"])
    private_key = pathlib.Path(state["encryption_private_key_ref"]).read_bytes()
    aad = welcome_bundle_aad(
        joining_device_id_hex=artifact.device_id_hex,
        version=WELCOME_BUNDLE_VERSION,
    )
    plaintext = open_welcome_bundle(
        private_key,
        base64.b64decode(welcome_bundle_b64.encode("ascii")),
        associated_data=aad,
    )
    signed_bundle = deserialize_signed_welcome_bundle_plaintext(plaintext)
    mutated = mutate(signed_bundle)
    if reseal_aad_version is not None:
        aad = welcome_bundle_aad(
            joining_device_id_hex=artifact.device_id_hex,
            version=reseal_aad_version,
        )
    sealed = seal_welcome_bundle(
        bytes.fromhex(artifact.device_encryption_public_key_hex),
        serialize_signed_welcome_bundle_plaintext(mutated),
        associated_data=aad,
    )
    return base64.b64encode(sealed).decode("ascii")


def _rewrite_join_request_version(join_request_artifact_b64, version):
    payload = json.loads(base64.b64decode(join_request_artifact_b64.encode("ascii")).decode("utf-8"))
    payload["version"] = version
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _open_signed_welcome_bundle(root_dir, welcome_bundle_b64):
    state = _pending_join_state(root_dir)
    artifact = deserialize_join_request_artifact(state["join_request_artifact"])
    private_key = pathlib.Path(state["encryption_private_key_ref"]).read_bytes()
    aad = welcome_bundle_aad(
        joining_device_id_hex=artifact.device_id_hex,
        version=WELCOME_BUNDLE_VERSION,
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


def test_localfolder_identity_bootstrap_preserves_an_unresolved_push(
    playground_dir, monkeypatch
):
    workspace = pathlib.Path(playground_dir)
    root = workspace / "install"
    cloud_dir = workspace / "cloud"
    root.mkdir()
    cloud_dir.mkdir()

    participant_hex = create_new_participant(root, "Alice")
    descriptor = {
        "protocol": "localfolder",
        "url": str(cloud_dir),
    }
    inner = LocalFolderStore(str(cloud_dir))

    class InconclusiveHeadStore:
        def __getattr__(self, name):
            return getattr(inner, name)

        def put_latest_link(self, data, expected_etag, link_uid=None):
            raise StoreProviderError("the head write may still take effect")

    monkeypatch.setattr(
        "small_sea_manager.provisioning._store_from_descriptor",
        lambda _descriptor: InconclusiveHeadStore(),
    )

    with pytest.raises(CodSync.PublicationOutcomeUnresolvedError) as exc:
        _push_note_to_self_to_local_remote(root, participant_hex, descriptor)

    assert isinstance(exc.value.cause, StoreProviderError)
    assert exc.value.write_phase == "head"
    assert exc.value.observed_absent is True


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


def _localfolder_participant(workspace):
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))
    return root1, root2, alice_hex


def test_authorize_rejects_unsupported_join_request_version_before_admitting(playground_dir):
    """An unsupported join request is refused with no device admitted and no commit."""
    root1, root2, alice_hex = _localfolder_participant(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2)
    bumped = _rewrite_join_request_version(
        join_request["join_request_artifact"],
        JOIN_REQUEST_ARTIFACT_VERSION + 1,
    )

    shared1 = note_to_self_sync_db_path(root1, alice_hex)
    sync_dir = root1 / "Participants" / alice_hex / "NoteToSelf" / "Sync"
    head_before = gitCmd(["-C", str(sync_dir), "rev-parse", "HEAD"]).stdout.strip()

    alice_manager = TeamManager(root1, alice_hex)
    try:
        alice_manager.authorize_identity_join(bumped)
        assert False, "Expected unsupported join request version to be rejected"
    except ValueError as exn:
        assert "join request artifact" in str(exn)

    assert _count_rows(shared1, "SELECT COUNT(*) FROM user_device") == 1
    head_after = gitCmd(["-C", str(sync_dir), "rev-parse", "HEAD"]).stdout.strip()
    assert head_after == head_before


def test_bootstrap_rejects_unsupported_payload_version_after_decryption(playground_dir):
    """A well-sealed bundle claiming an unsupported payload version is still refused.

    The AAD binds the expected version, so this reseals under the expected AAD and
    lies inside the ciphertext. Only the post-decrypt check catches that.
    """
    root1, root2, alice_hex = _localfolder_participant(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    bumped_payload = _rewrite_welcome_bundle(
        root2,
        welcome["welcome_bundle"],
        lambda signed: SignedWelcomeBundle(
            version=signed.version,
            bundle=dataclasses.replace(signed.bundle, version=WELCOME_BUNDLE_VERSION + 1),
            authorizing_device_id_hex=signed.authorizing_device_id_hex,
            signature_hex=signed.signature_hex,
        ),
    )

    try:
        bootstrap_existing_identity(root2, bumped_payload)
        assert False, "Expected unsupported welcome bundle version to be rejected"
    except ValueError as exn:
        assert "welcome bundle" in str(exn)

    assert not (root2 / "Participants" / alice_hex).exists()


def test_bootstrap_rejects_unsupported_signed_wrapper_version(playground_dir):
    root1, root2, alice_hex = _localfolder_participant(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    bumped_wrapper = _rewrite_welcome_bundle(
        root2,
        welcome["welcome_bundle"],
        lambda signed: SignedWelcomeBundle(
            version=SIGNED_WELCOME_BUNDLE_VERSION + 1,
            bundle=signed.bundle,
            authorizing_device_id_hex=signed.authorizing_device_id_hex,
            signature_hex=signed.signature_hex,
        ),
    )

    try:
        bootstrap_existing_identity(root2, bumped_wrapper)
        assert False, "Expected unsupported signed wrapper version to be rejected"
    except ValueError as exn:
        assert "signed welcome bundle" in str(exn)

    assert not (root2 / "Participants" / alice_hex).exists()


def test_welcome_bundle_aad_binds_the_expected_payload_version(playground_dir):
    """A bundle sealed under a different payload version will not open at all.

    This is what makes the expected version legitimate associated data: the receiver
    supplies it from a constant rather than learning it from the ciphertext.
    """
    root1, root2, alice_hex = _localfolder_participant(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    wrong_aad = _rewrite_welcome_bundle(
        root2,
        welcome["welcome_bundle"],
        lambda signed: signed,
        reseal_aad_version=WELCOME_BUNDLE_VERSION + 1,
    )

    with pytest.raises(InvalidTag):
        bootstrap_existing_identity(root2, wrong_aad)

    assert not (root2 / "Participants" / alice_hex).exists()


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
    gitCmd(["-C", str(repo_dir), "add", "core.db"])
    gitCmd(["-C", str(repo_dir), "commit", "-m", "Rotate signer for test"])
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
    cloud_storage_id = backend_a.add_cloud_location(
        alice_nts_token,
        "s3",
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    nts_session = backend_a._lookup_session(alice_nts_token)
    add_berth_cloud_allocation_by_berth_id(
        root1,
        alice_hex,
        nts_session.berth_id,
        cloud_storage_id,
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
    head = gitCmd(["-C", str(sync_dir), "rev-parse", "HEAD"]).stdout.strip()
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
