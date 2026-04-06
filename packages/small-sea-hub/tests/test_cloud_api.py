# Integration test for cloud file upload/download through the HTTP API.
#
# Exercises POST /cloud_file and GET /cloud_file against a local MinIO server,
# using FastAPI's TestClient (in-process, no subprocess needed for the hub).

import base64

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

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


def _open_session(client, mode="passthrough"):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "NoteToSelf",
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


def _register_cloud(backend, session_hex, minio):
    backend.add_cloud_location(
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
    return f"ss-{ss_session.berth_id.hex()[:16]}"


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


def test_upload_and_download(test_env):
    client = test_env["client"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    # 1. Open session
    session_hex = _open_session(client)

    # 2. Register MinIO cloud location with credentials
    _register_cloud(test_env["backend"], session_hex, minio)

    # 3. Pre-create the bucket
    bucket_name = _derive_bucket_name(playground_dir, session_hex)
    _create_bucket(minio, bucket_name)

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
    _register_cloud(backend, nts_session, minio)

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
    bucket_name = _derive_bucket_name(playground_dir, team_session_hex)
    _create_bucket(minio, bucket_name)

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
