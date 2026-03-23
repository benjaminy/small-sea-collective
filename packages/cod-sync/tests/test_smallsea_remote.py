# Test push/clone roundtrip through SmallSeaRemote backed by the hub.
#
# Uses FastAPI TestClient (in-process) with a real MinIO server for S3 storage.
# Exercises: SmallSeaRemote.upload_latest_link, get_latest_link, get_link,
#            download_bundle via hub's /cloud_file endpoints.

import pathlib
import shutil
import tempfile

import boto3
import cod_sync.protocol as CS
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from test_clone_from_local_bundle import make_cod_sync, working_tree_files

MINIO_PORT = 9400


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=MINIO_PORT)


@pytest.fixture()
def hub_env(playground_dir, minio):
    """Backend, participant, TestClient, and session — ready to go."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")

    app.state.backend = backend
    client = TestClient(app)

    # Open session (two-step flow)
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "NoteToSelf",
            "client": "Smoke Tests",
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200
    session_hex = resp.json()

    # Register MinIO cloud location and pre-create bucket
    backend.add_cloud_location(
        session_hex,
        "s3",
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    ss_session = backend._lookup_session(session_hex)
    bucket_name = backend._make_storage_adapter(ss_session).bucket_name
    boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    ).create_bucket(Bucket=bucket_name)

    return {
        "client": client,
        "session_hex": session_hex,
        "playground_dir": playground_dir,
        "minio": minio,
    }


def test_push_clone_roundtrip_via_hub(hub_env, scratch_dir):
    client = hub_env["client"]
    session_hex = hub_env["session_hex"]

    scratch = pathlib.Path(scratch_dir)
    alice_repo = scratch / "alice-repo"
    bob_repo = scratch / "bob-repo"
    alice_repo.mkdir()
    bob_repo.mkdir()

    # ---- Alice: init repo, commit files ----
    CS.gitCmd(["init", "-b", "main", str(alice_repo)])
    CS.gitCmd(["-C", str(alice_repo), "config", "user.email", "alice@test"])
    CS.gitCmd(["-C", str(alice_repo), "config", "user.name", "Alice"])

    (alice_repo / "README.md").write_text("# Hub Roundtrip\n")
    (alice_repo / "notes.txt").write_text("testing through the hub\n")
    CS.gitCmd(["-C", str(alice_repo), "add", "-A"])
    CS.gitCmd(["-C", str(alice_repo), "commit", "-m", "initial commit"])

    # ---- Alice: push via SmallSeaRemote ----
    alice_remote = CS.SmallSeaRemote(session_hex, client=client)
    alice_cod = make_cod_sync(alice_repo, "hub-pub")
    alice_cod.remote = alice_remote
    alice_cod.push_to_remote(["main"])

    # ---- Bob: clone via SmallSeaRemote ----
    bob_remote = CS.SmallSeaRemote(session_hex, client=client)
    result = bob_remote.get_latest_link()
    assert result is not None
    latest, etag = result
    assert etag is not None

    [link_ids, branches, bundles, supp] = latest
    assert link_ids[0] == "initial-snapshot"
    assert len(bundles) == 1
    assert supp["cod_version"] == "1.0.0"

    bundle_uid = bundles[0][0]
    with tempfile.TemporaryDirectory() as td:
        bundle_path = f"{td}/clone.bundle"
        bob_remote.download_bundle(bundle_uid, bundle_path)
        CS.gitCmd(["clone", bundle_path, str(bob_repo / "checkout")])

    # Move contents up (clone creates a subdir)
    checkout = bob_repo / "checkout"
    for item in checkout.iterdir():
        shutil.move(str(item), str(bob_repo / item.name))
    checkout.rmdir()

    CS.gitCmd(["-C", str(bob_repo), "checkout", "main"])

    # ---- Verify working trees match ----
    alice_files = working_tree_files(alice_repo)
    bob_files = working_tree_files(bob_repo)

    assert alice_files == bob_files
    assert "README.md" in alice_files
    assert "notes.txt" in alice_files
    assert alice_files["README.md"] == "# Hub Roundtrip\n"
