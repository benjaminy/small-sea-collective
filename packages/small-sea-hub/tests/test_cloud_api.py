# Integration test for cloud file upload/download through the HTTP API.
#
# Exercises POST /cloud_file and GET /cloud_file against a local MinIO server,
# using FastAPI's TestClient (in-process, no subprocess needed for the hub).

import base64
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.cloud_errors import MaterializationOutcome
from botocore.config import Config as BotoConfig
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_note_to_self.db import note_to_self_sync_db_path
from small_sea_note_to_self.ids import uuid7
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_support import publish_storage_announcement_for_session

MINIO_PORT = 9200


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=MINIO_PORT)


@pytest.fixture()
def test_env(playground_dir, minio):
    """Set up backend, participant, and test client."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")

    app.state.backend = backend
    client = TestClient(app)

    return {
        "backend": backend,
        "client": client,
        "playground_dir": playground_dir,
        "minio": minio,
    }


def _open_session(client, mode="passthrough", team="NoteToSelf"):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    pending_id = result["pending_id"]
    pin = result["pin"]

    resp = client.post("/sessions/confirm", json={"pending_id": pending_id, "pin": pin})
    assert resp.status_code == 200
    session_hex = resp.json()
    assert isinstance(session_hex, str)
    return session_hex


def _participant_hex(backend):
    return backend._find_participant("alice")[0][0].name


def _register_cloud(backend, session_hex, minio):
    return backend.add_cloud_location(
        session_hex,
        "s3",
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )


def _derive_bucket_name(playground_dir, session_hex):
    """Derive the bucket name using the same logic as the backend."""
    ss = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    ss_session = ss._lookup_session(session_hex)
    allocation = Provisioning.get_berth_cloud_allocation_for_berth(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
    )
    assert allocation is not None
    return allocation["location"]


def _create_bucket(minio, bucket_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=bucket_name)


def _read_bucket_object(minio, bucket_name, key):
    s3 = boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    return s3.get_object(Bucket=bucket_name, Key=key)["Body"].read()


def _assert_cloud_storage_required(resp, reason):
    assert resp.status_code == 409
    payload = resp.json()
    assert payload == {"error": "cloud_storage_required", "reason": reason}


def test_upload_and_download(test_env):
    client = test_env["client"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    # 1. Open session
    session_hex = _open_session(client)

    # 2. Register MinIO cloud location with credentials
    storage_id = _register_cloud(test_env["backend"], session_hex, minio)
    ss_session = test_env["backend"]._lookup_session(session_hex)
    allocation = Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
    )
    bucket_name = allocation["location"]

    auth = {"Authorization": f"Bearer {session_hex}"}

    # 4. Upload a file
    content = b"hello from alice"
    resp = client.post(
        "/cloud_file",
        json={
            "path": "greeting.txt",
            "data": base64.b64encode(content).decode(),
        },
        headers=auth,
    )
    assert resp.status_code == 200
    upload_result = resp.json()
    assert upload_result["ok"] is True
    assert upload_result["etag"] is not None

    # 5. Download the file
    resp = client.get("/cloud_file", params={"path": "greeting.txt"}, headers=auth)
    assert resp.status_code == 200
    dl_result = resp.json()
    assert dl_result["ok"] is True
    downloaded = base64.b64decode(dl_result["data"])
    assert downloaded == content

    # 6. Upload a second file, download both
    content2 = b"second file contents"
    resp = client.post(
        "/cloud_file",
        json={
            "path": "notes/todo.txt",
            "data": base64.b64encode(content2).decode(),
        },
        headers=auth,
    )
    assert resp.status_code == 200

    resp = client.get("/cloud_file", params={"path": "greeting.txt"}, headers=auth)
    assert base64.b64decode(resp.json()["data"]) == content

    resp = client.get("/cloud_file", params={"path": "notes/todo.txt"}, headers=auth)
    assert base64.b64decode(resp.json()["data"]) == content2

    # 7. Overwrite first file, verify new content
    new_content = b"updated greeting"
    resp = client.post(
        "/cloud_file",
        json={
            "path": "greeting.txt",
            "data": base64.b64encode(new_content).decode(),
        },
        headers=auth,
    )
    assert resp.status_code == 200

    resp = client.get("/cloud_file", params={"path": "greeting.txt"}, headers=auth)
    assert base64.b64decode(resp.json()["data"]) == new_content

    raw = _read_bucket_object(minio, bucket_name, "greeting.txt")
    assert raw == new_content


def test_non_vault_team_path_uses_encryption(test_env):
    client = test_env["client"]
    backend = test_env["backend"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    alice_hex = backend._find_participant("alice")[0][0].name
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")

    nts_session = _open_session(client, mode="passthrough")
    storage_id = _register_cloud(backend, nts_session, minio)

    team_resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "ProjectX",
            "client": "Smoke Tests",
        },
    )
    assert team_resp.status_code == 200
    team_pending = team_resp.json()
    team_confirm = client.post(
        "/sessions/confirm",
        json={"pending_id": team_pending["pending_id"], "pin": team_pending["pin"]},
    )
    team_session_hex = team_confirm.json()
    team_session = backend._lookup_session(team_session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        team_session.participant_id.hex(),
        team_session.berth_id,
        storage_id,
    )
    bucket_name = _derive_bucket_name(playground_dir, team_session_hex)
    setup_resp = client.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {team_session_hex}"},
    )
    assert setup_resp.status_code == 200
    publish_storage_announcement_for_session(backend, team_session_hex)

    auth = {"Authorization": f"Bearer {team_session_hex}"}
    plaintext = b"team data that should be encrypted"
    resp = client.post(
        "/cloud_file",
        json={"path": "greeting.txt", "data": base64.b64encode(plaintext).decode()},
        headers=auth,
    )
    assert resp.status_code == 200

    raw = _read_bucket_object(minio, bucket_name, "greeting.txt")
    assert raw != plaintext
    payload = raw.decode("utf-8")
    assert "\"ciphertext\"" in payload
    assert "\"signature\"" in payload


def test_team_cloud_file_requires_storage_announcement(test_env):
    client = test_env["client"]
    backend = test_env["backend"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    alice_hex = backend._find_participant("alice")[0][0].name
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    nts_session = _open_session(client, mode="passthrough")
    storage_id = _register_cloud(backend, nts_session, minio)
    team_session_hex = _open_session(client, team="ProjectX")
    team_session = backend._lookup_session(team_session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        team_session.participant_id.hex(),
        team_session.berth_id,
        storage_id,
    )
    auth = {"Authorization": f"Bearer {team_session_hex}"}

    blocked = client.post(
        "/cloud_file",
        json={"path": "team.txt", "data": base64.b64encode(b"hello").decode()},
        headers=auth,
    )
    _assert_cloud_storage_required(blocked, "announcement_missing")

    setup = client.post("/cloud/setup", headers=auth)
    assert setup.status_code == 200
    published = publish_storage_announcement_for_session(backend, team_session_hex)
    assert published["wrote"] is True

    retry = client.post(
        "/cloud_file",
        json={"path": "team.txt", "data": base64.b64encode(b"hello").decode()},
        headers=auth,
    )
    assert retry.status_code == 200

    team_db_path = (
        pathlib.Path(playground_dir)
        / "Participants"
        / team_session.participant_id.hex()
        / "ProjectX"
        / "Sync"
        / "core.db"
    )
    with sqlite3.connect(str(team_db_path)) as conn:
        conn.execute("DELETE FROM member_berth_storage_announcement")
        conn.commit()
    blocked_download = client.get(
        "/cloud_file",
        params={"path": "team.txt"},
        headers=auth,
    )
    _assert_cloud_storage_required(blocked_download, "announcement_missing")


def test_team_cloud_file_allows_current_device_bootstrap_announcement(test_env):
    client = test_env["client"]
    backend = test_env["backend"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    alice_hex = backend._find_participant("alice")[0][0].name
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    nts_session = _open_session(client, mode="passthrough")
    storage_id = _register_cloud(backend, nts_session, minio)
    team_session_hex = _open_session(client, team="ProjectX")
    team_session = backend._lookup_session(team_session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        team_session.participant_id.hex(),
        team_session.berth_id,
        storage_id,
    )
    auth = {"Authorization": f"Bearer {team_session_hex}"}
    setup = client.post("/cloud/setup", headers=auth)
    assert setup.status_code == 200
    publish_storage_announcement_for_session(backend, team_session_hex)

    team_db = (
        pathlib.Path(playground_dir)
        / "Participants"
        / team_session.participant_id.hex()
        / "ProjectX"
        / "Sync"
        / "core.db"
    )
    with sqlite3.connect(str(team_db)) as conn:
        conn.execute("DELETE FROM key_certificate")
        conn.commit()

    resp = client.post(
        "/cloud_file",
        json={
            "path": "bootstrap.txt",
            "data": base64.b64encode(b"accepted-before-trust").decode(),
        },
        headers=auth,
    )

    assert resp.status_code == 200


def _insert_synthetic_rotated_team_device_key(
    playground_dir, participant_hex, team_id
):
    # Synthetic stand-in for a real device-key rotation: insert a second
    # team_device_key row whose created_at is strictly later than every
    # existing row's, so _current_team_device_public_key picks it as the
    # "current" key. No real Manager rotation primitive exists yet
    # (see PLAN.md follow-up); a direct connection is used so foreign
    # keys stay off and the synthetic device_id does not need a
    # matching user_device row.
    db_path = note_to_self_sync_db_path(playground_dir, participant_hex)
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """
            SELECT created_at FROM team_device_key
            WHERE team_id = ?
            ORDER BY created_at DESC, device_id DESC
            LIMIT 1
            """,
            (team_id,),
        ).fetchone()
        assert row is not None, "expected an initial team_device_key row (K1)"
        previous_created_at = datetime.fromisoformat(row[0])
        rotated_created_at = (
            previous_created_at + timedelta(seconds=1)
        ).isoformat()
        conn.execute(
            """
            INSERT INTO team_device_key (
                team_id, device_id, public_key, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (team_id, uuid7(), b"rotated-public-key-stub", rotated_created_at),
        )
        conn.commit()


def test_team_cloud_file_bootstrap_allowance_rejects_rotated_signer(test_env):
    # Micro test pinning the rotation discriminant of the own-storage
    # bootstrap allowance: an announcement signed by a no-longer-current
    # team-device key must NOT satisfy _has_current_device_storage_announcement,
    # even when the trusted selection is "missing".
    #
    # Sanity-check expectation (per PLAN.md): removing the synthetic K2
    # insert below should make this test fail with 200 instead of 409,
    # confirming the rotation step is what discriminates this case from
    # the positive-allowance test above.
    client = test_env["client"]
    backend = test_env["backend"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    alice_hex = backend._find_participant("alice")[0][0].name
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    nts_session = _open_session(client, mode="passthrough")
    storage_id = _register_cloud(backend, nts_session, minio)
    team_session_hex = _open_session(client, team="ProjectX")
    team_session = backend._lookup_session(team_session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        team_session.participant_id.hex(),
        team_session.berth_id,
        storage_id,
    )
    auth = {"Authorization": f"Bearer {team_session_hex}"}
    setup = client.post("/cloud/setup", headers=auth)
    assert setup.status_code == 200

    # Publish the announcement first — it is signed by K1, the initial
    # team_device_key generated for this team.
    publish_storage_announcement_for_session(backend, team_session_hex)

    # Synthetic rotation: insert K2 with a strictly later created_at, so
    # the existing K1-signed announcement no longer matches the current
    # key returned by _current_team_device_public_key.
    _insert_synthetic_rotated_team_device_key(
        playground_dir,
        team_session.participant_id.hex(),
        team_session.team_id,
    )

    # Drop trusted selection so the bootstrap allowance is the only
    # remaining path that could accept the K1-signed announcement.
    team_db = (
        pathlib.Path(playground_dir)
        / "Participants"
        / team_session.participant_id.hex()
        / "ProjectX"
        / "Sync"
        / "core.db"
    )
    with sqlite3.connect(str(team_db)) as conn:
        conn.execute("DELETE FROM key_certificate")
        conn.commit()

    resp = client.post(
        "/cloud_file",
        json={
            "path": "bootstrap.txt",
            "data": base64.b64encode(b"should-be-rejected-after-rotation").decode(),
        },
        headers=auth,
    )

    _assert_cloud_storage_required(resp, "announcement_missing")


def test_cloud_setup_is_not_blocked_by_missing_announcement(test_env):
    client = test_env["client"]
    backend = test_env["backend"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    alice_hex = backend._find_participant("alice")[0][0].name
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    nts_session = _open_session(client, mode="passthrough")
    storage_id = _register_cloud(backend, nts_session, minio)
    team_session_hex = _open_session(client, team="ProjectX")
    team_session = backend._lookup_session(team_session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        team_session.participant_id.hex(),
        team_session.berth_id,
        storage_id,
    )

    resp = client.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {team_session_hex}"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "materialized"


def test_no_allocation_session_returns_cloud_location_missing(test_env):
    client = test_env["client"]
    minio = test_env["minio"]

    # The test fixture creates the participant's NoteToSelf team; this session
    # intentionally has cloud storage configured but no berth allocation.
    session_hex = _open_session(client)
    _register_cloud(test_env["backend"], session_hex, minio)

    resp = client.get(
        "/cloud_file",
        params={"path": "missing.txt"},
        headers={"Authorization": f"Bearer {session_hex}"},
    )

    _assert_cloud_storage_required(resp, "cloud_location_missing")


def test_missing_credentials_return_cloud_credentials_missing(test_env):
    client = test_env["client"]
    backend = test_env["backend"]
    playground_dir = test_env["playground_dir"]

    session_hex = _open_session(client)
    ss_session = backend._lookup_session(session_hex)
    storage_id = Provisioning.add_cloud_storage(
        playground_dir,
        ss_session.participant_id.hex(),
        protocol="s3",
        url="http://example.invalid",
    )
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
    )

    resp = client.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {session_hex}"},
    )

    _assert_cloud_storage_required(resp, "cloud_credentials_missing")


def test_gdrive_pending_location_returns_cloud_user_action_required(test_env):
    client = test_env["client"]
    backend = test_env["backend"]
    playground_dir = test_env["playground_dir"]

    session_hex = _open_session(client)
    ss_session = backend._lookup_session(session_hex)
    storage_id = Provisioning.add_cloud_storage(
        playground_dir,
        ss_session.participant_id.hex(),
        protocol="gdrive",
        url="appDataFolder",
        access_token="already-fresh",
        token_expiry=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
        # GDrive treats any pending-* location as a not-yet-provider-issued
        # locator that requires user action before materialization can proceed.
        location="pending-gdrive-folder",
    )

    resp = client.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {session_hex}"},
    )

    _assert_cloud_storage_required(resp, "cloud_user_action_required")


def test_materialization_failure_returns_cloud_materialization_failed(
    test_env, monkeypatch
):
    class FailedMaterializationAdapter:
        def materialize(self):
            return MaterializationOutcome("failed")

    client = test_env["client"]
    backend = test_env["backend"]
    playground_dir = test_env["playground_dir"]

    session_hex = _open_session(client)
    storage_id = _register_cloud(backend, session_hex, test_env["minio"])
    ss_session = backend._lookup_session(session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
    )
    monkeypatch.setattr(
        backend,
        "_make_storage_adapter_from_record",
        lambda _session, _cloud: FailedMaterializationAdapter(),
    )

    resp = client.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {session_hex}"},
    )

    _assert_cloud_storage_required(resp, "cloud_materialization_failed")


def test_materialized_with_locator_rebuilds_before_storage_op(test_env, monkeypatch):
    uploads = []

    class LocatorAdapter:
        def __init__(self, location):
            self.location = location

        def materialize(self):
            if self.location == "pending-provider-locator":
                return MaterializationOutcome(
                    "materialized_with_locator",
                    "provider-final-locator",
                )
            return MaterializationOutcome("materialized")

        def upload_overwrite(self, path, data):
            uploads.append((self.location, path, data))
            return True, "fake-etag", "Object updated successfully"

    client = test_env["client"]
    backend = test_env["backend"]
    playground_dir = test_env["playground_dir"]

    session_hex = _open_session(client)
    storage_id = _register_cloud(backend, session_hex, test_env["minio"])
    ss_session = backend._lookup_session(session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
        location="pending-provider-locator",
    )
    monkeypatch.setattr(
        backend,
        "_make_storage_adapter_from_record",
        lambda _session, cloud: LocatorAdapter(cloud.location),
    )

    resp = client.post(
        "/cloud_file",
        json={"path": "locator.txt", "data": base64.b64encode(b"hello").decode()},
        headers={"Authorization": f"Bearer {session_hex}"},
    )

    assert resp.status_code == 200
    assert uploads == [("provider-final-locator", "locator.txt", b"hello")]
    allocation = Provisioning.get_berth_cloud_allocation_for_berth(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
    )
    assert allocation["location"] == "provider-final-locator"


def test_locator_writeback_race_returns_cloud_allocation_conflict(
    test_env, monkeypatch
):
    class RacingLocatorAdapter:
        def materialize(self):
            return MaterializationOutcome(
                "materialized_with_locator",
                "provider-final-locator",
            )

        def upload_overwrite(self, path, data):
            raise AssertionError("race test should stop at /cloud/setup")

    client = test_env["client"]
    backend = test_env["backend"]
    playground_dir = test_env["playground_dir"]

    session_hex = _open_session(client)
    storage_id = _register_cloud(backend, session_hex, test_env["minio"])
    ss_session = backend._lookup_session(session_hex)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
        location="pending-provider-locator",
    )

    def write_conflicting_location(participant_hex, allocation_id, expected, new):
        db_path = note_to_self_sync_db_path(playground_dir, participant_hex)
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "UPDATE berth_cloud_allocation SET location = ? WHERE id = ?",
                ("different-provider-locator", allocation_id),
            )
            assert cur.rowcount == 1
            conn.commit()
            row = conn.execute(
                "SELECT location FROM berth_cloud_allocation WHERE id = ?",
                (allocation_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "different-provider-locator"
        return False

    monkeypatch.setattr(
        backend,
        "_make_storage_adapter_from_record",
        lambda _session, _cloud: RacingLocatorAdapter(),
    )
    monkeypatch.setattr(backend, "_writeback_locator", write_conflicting_location)

    resp = client.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {session_hex}"},
    )

    _assert_cloud_storage_required(resp, "cloud_allocation_conflict")


def test_no_cloud_team_creation_leaves_storage_missing(test_env):
    client = test_env["client"]
    backend = test_env["backend"]
    playground_dir = test_env["playground_dir"]
    alice_hex = _participant_hex(backend)

    team_result = Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    assert (
        Provisioning.get_berth_cloud_allocation_for_berth(
            playground_dir,
            alice_hex,
            team_result["berth_id_hex"],
        )
        is None
    )

    session_hex = _open_session(client, team="ProjectX")
    resp = client.post(
        "/cloud_file",
        json={"path": "team.txt", "data": base64.b64encode(b"hello").decode()},
        headers={"Authorization": f"Bearer {session_hex}"},
    )

    _assert_cloud_storage_required(resp, "cloud_location_missing")
