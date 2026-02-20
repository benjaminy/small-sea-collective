# Integration test for cloud file upload/download through the HTTP API.
#
# Exercises POST /cloud_file and GET /cloud_file against a local MinIO server,
# using FastAPI's TestClient (in-process, no subprocess needed for the hub).

import base64

import boto3
from botocore.config import Config as BotoConfig
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import small_sea_hub.backend as SmallSea
from small_sea_hub.server import app
import small_sea_team_manager.provisioning as Provisioning


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


def _open_session(client):
    resp = client.post("/sessions", json={
        "participant": "alice",
        "app": "SmallSeaCollectiveCore",
        "team": "NoteToSelf",
        "client": "Smoke Tests",
    })
    assert resp.status_code == 200
    session_hex = resp.json()
    assert isinstance(session_hex, str)
    return session_hex


def _register_cloud(client, session_hex, minio):
    resp = client.post("/cloud_locations", json={
        "session": session_hex,
        "backend": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    })
    assert resp.status_code == 200


def _derive_bucket_name(playground_dir, session_hex):
    """Derive the bucket name using the same logic as the backend."""
    ss = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    ss_session = ss._lookup_session(session_hex)
    core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{core_path}")
    with Session(engine) as session:
        zone = session.query(SmallSea.TeamAppZone).filter(
            SmallSea.TeamAppZone.lid == ss_session.zone_id).first()
    return f"ss-{zone.suid.hex()[:16]}"


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


def test_upload_and_download(test_env):
    client = test_env["client"]
    minio = test_env["minio"]
    playground_dir = test_env["playground_dir"]

    # 1. Open session
    session_hex = _open_session(client)

    # 2. Register MinIO cloud location with credentials
    _register_cloud(client, session_hex, minio)

    # 3. Pre-create the bucket
    bucket_name = _derive_bucket_name(playground_dir, session_hex)
    _create_bucket(minio, bucket_name)

    # 4. Upload a file
    content = b"hello from alice"
    resp = client.post("/cloud_file", json={
        "session": session_hex,
        "path": "greeting.txt",
        "data": base64.b64encode(content).decode(),
    })
    assert resp.status_code == 200
    upload_result = resp.json()
    assert upload_result["ok"] is True
    assert upload_result["etag"] is not None

    # 5. Download the file
    resp = client.get("/cloud_file", params={
        "session": session_hex,
        "path": "greeting.txt",
    })
    assert resp.status_code == 200
    dl_result = resp.json()
    assert dl_result["ok"] is True
    downloaded = base64.b64decode(dl_result["data"])
    assert downloaded == content

    # 6. Upload a second file, download both
    content2 = b"second file contents"
    resp = client.post("/cloud_file", json={
        "session": session_hex,
        "path": "notes/todo.txt",
        "data": base64.b64encode(content2).decode(),
    })
    assert resp.status_code == 200

    resp = client.get("/cloud_file", params={
        "session": session_hex,
        "path": "greeting.txt",
    })
    assert base64.b64decode(resp.json()["data"]) == content

    resp = client.get("/cloud_file", params={
        "session": session_hex,
        "path": "notes/todo.txt",
    })
    assert base64.b64decode(resp.json()["data"]) == content2

    # 7. Overwrite first file, verify new content
    new_content = b"updated greeting"
    resp = client.post("/cloud_file", json={
        "session": session_hex,
        "path": "greeting.txt",
        "data": base64.b64encode(new_content).decode(),
    })
    assert resp.status_code == 200

    resp = client.get("/cloud_file", params={
        "session": session_hex,
        "path": "greeting.txt",
    })
    assert base64.b64decode(resp.json()["data"]) == new_content
