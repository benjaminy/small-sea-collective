# Test push/clone roundtrip through SmallSeaRemote backed by the hub.
#
# Uses FastAPI TestClient (in-process) with a real MinIO server for S3 storage.
# Exercises: SmallSeaRemote.upload_latest_link, get_latest_link, get_link,
#            download_bundle via hub's /cloud_file endpoints.

import pathlib

import boto3
from botocore.config import Config as BotoConfig
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import small_sea_hub.backend as SmallSea
from small_sea_hub.server import app
import small_sea_team_manager.provisioning as Provisioning

import corncob.protocol as CC
from test_clone_from_local_bundle import make_corncob, working_tree_files


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

    # Open session
    resp = client.post("/sessions", json={
        "participant": "alice",
        "app": "SmallSeaCollectiveCore",
        "team": "NoteToSelf",
        "client": "Smoke Tests",
    })
    assert resp.status_code == 200
    session_hex = resp.json()

    # Register MinIO cloud location
    resp = client.post("/cloud_locations", json={
        "session": session_hex,
        "backend": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    })
    assert resp.status_code == 200

    # Derive and pre-create bucket
    ss = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    ss_session = ss._lookup_session(session_hex)
    core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{core_path}")
    with Session(engine) as db_session:
        zone = db_session.query(SmallSea.TeamAppZone).filter(
            SmallSea.TeamAppZone.id == ss_session.zone_id).first()
    bucket_name = f"ss-{zone.id.hex()[:16]}"

    s3 = boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=bucket_name)

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
    CC.gitCmd(["init", "-b", "main", str(alice_repo)])
    CC.gitCmd(["-C", str(alice_repo), "config", "user.email", "alice@test"])
    CC.gitCmd(["-C", str(alice_repo), "config", "user.name", "Alice"])

    (alice_repo / "README.md").write_text("# Hub Roundtrip\n")
    (alice_repo / "notes.txt").write_text("testing through the hub\n")
    CC.gitCmd(["-C", str(alice_repo), "add", "-A"])
    CC.gitCmd(["-C", str(alice_repo), "commit", "-m", "initial commit"])

    # ---- Alice: push via SmallSeaRemote ----
    alice_remote = CC.SmallSeaRemote(session_hex, client=client)
    alice_corn = make_corncob(alice_repo, "hub-pub")
    alice_corn.remote = alice_remote
    alice_corn.push_to_remote(["main"])

    # ---- Bob: clone via SmallSeaRemote ----
    bob_remote = CC.SmallSeaRemote(session_hex, client=client)
    bob_corn = make_corncob(bob_repo, "hub")
    bob_corn.remote = bob_remote
    # clone_from_remote calls CornCobRemote.init internally, so we
    # need to wire the remote manually and replicate clone logic
    latest = bob_remote.get_latest_link()
    assert latest is not None

    [link_ids, branches, bundles, supp] = latest
    assert link_ids[0] == "initial-snapshot"
    assert len(bundles) == 1

    import tempfile
    bundle_uid = bundles[0][0]
    with tempfile.TemporaryDirectory() as td:
        bundle_path = f"{td}/clone.bundle"
        bob_remote.download_bundle(bundle_uid, bundle_path)
        CC.gitCmd(["clone", bundle_path, str(bob_repo / "checkout")])

    # Move contents up (clone creates a subdir)
    import shutil
    import os
    checkout = bob_repo / "checkout"
    for item in checkout.iterdir():
        shutil.move(str(item), str(bob_repo / item.name))
    checkout.rmdir()

    CC.gitCmd(["-C", str(bob_repo), "checkout", "main"])

    # ---- Verify working trees match ----
    alice_files = working_tree_files(alice_repo)
    bob_files = working_tree_files(bob_repo)

    assert alice_files == bob_files
    assert "README.md" in alice_files
    assert "notes.txt" in alice_files
    assert alice_files["README.md"] == "# Hub Roundtrip\n"
