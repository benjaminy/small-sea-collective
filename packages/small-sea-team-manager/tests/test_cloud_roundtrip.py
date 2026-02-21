# Micro integration test: provision a user locally, then exercise
# cloud upload/download through the Hub HTTP API.
#
# The team manager only handles provisioning — all cloud storage
# interactions go through the hub.

import base64
import pathlib

import boto3
from botocore.config import Config as BotoConfig
from fastapi.testclient import TestClient

import small_sea_team_manager.provisioning as Provisioning
import small_sea_hub.backend as SmallSea
from small_sea_hub.server import app


MINIO_PORT = 9300


def test_local_provision_then_hub_roundtrip(playground_dir, minio_server_gen):
    minio = minio_server_gen(port=MINIO_PORT)
    root = pathlib.Path(playground_dir)

    # ---- 1. Provision a participant purely locally ----
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    core_db = root / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    assert core_db.exists()

    # ---- 2. Start the hub (in-process via TestClient) ----
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    app.state.backend = backend
    client = TestClient(app)

    # ---- 3. Open a session for NoteToSelf / core app ----
    resp = client.post("/sessions", json={
        "participant": "alice",
        "app": "SmallSeaCollectiveCore",
        "team": "NoteToSelf",
        "client": "Smoke Tests",
    })
    assert resp.status_code == 200
    session_hex = resp.json()

    # ---- 4. Register cloud location through the hub ----
    resp = client.post("/cloud_locations", json={
        "session": session_hex,
        "backend": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    })
    assert resp.status_code == 200

    # Pre-create the bucket in MinIO (test infrastructure — the hub
    # derives the same name internally but doesn't create buckets)
    ss_session = backend._lookup_session(session_hex)
    adapter = backend._make_s3_adapter(ss_session)
    s3 = boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=adapter.zone)

    # ---- 5. Upload a file through the hub ----
    content = b"hello from the team manager test"
    resp = client.post("/cloud_file", json={
        "session": session_hex,
        "path": "greeting.txt",
        "data": base64.b64encode(content).decode(),
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # ---- 6. Download and verify round-trip ----
    resp = client.get("/cloud_file", params={
        "session": session_hex,
        "path": "greeting.txt",
    })
    assert resp.status_code == 200
    downloaded = base64.b64decode(resp.json()["data"])
    assert downloaded == content
