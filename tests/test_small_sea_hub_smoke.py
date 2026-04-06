# End-to-end subprocess hub test.
#
# Starts a real hub process and MinIO server, then exercises the full
# push/clone roundtrip over HTTP — proving the deployment path works.

import os
import pathlib
import shutil
import tempfile

import boto3
import requests
from botocore.config import Config as BotoConfig
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
import cod_sync.protocol as CS


MINIO_PORT = 9500
HUB_PORT = 11500


def working_tree_files(repo_dir):
    """Return {path: content} for all git-tracked files."""
    result = CS.gitCmd(["-C", str(repo_dir), "ls-files"])
    files = {}
    for name in result.stdout.strip().splitlines():
        files[name] = (pathlib.Path(repo_dir) / name).read_text()
    return files


def make_cod_sync(repo_dir, remote_name):
    """Create a CodSync wired to a specific repo directory."""
    os.chdir(repo_dir)
    cod = CS.CodSync(remote_name)
    return cod


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=MINIO_PORT)


@pytest.fixture()
def hub_env(playground_dir, minio, hub_server_gen):
    """Real subprocess hub, participant, session, and S3 bucket — ready to go."""
    root_dir = playground_dir

    # Provision participant directly on disk
    alice_hex = Provisioning.create_new_participant(root_dir, "alice")

    # Write S3 cloud config directly to NoteToSelf DB before starting the Hub.
    # The Hub reads this at request time; no HTTP endpoint for cloud registration.
    note_to_self_db = (
        pathlib.Path(root_dir)
        / "Participants" / alice_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{note_to_self_db}")
    with Session(engine) as db_session:
        cloud = SmallSea.CloudStorage(
            id=os.urandom(16),
            protocol="s3",
            url=minio["endpoint"],
            access_key=minio["access_key"],
            secret_key=minio["secret_key"],
        )
        db_session.add(cloud)
        db_session.commit()

    # Start hub as a real subprocess
    hub = hub_server_gen(root_dir=root_dir, port=HUB_PORT)
    hub_endpoint = hub["endpoint"]

    # Open session via two-step HTTP flow.
    # client="Smoke Tests" causes the Hub to echo the PIN in the response.
    resp = requests.post(f"{hub_endpoint}/sessions/request", json={
        "participant": "alice",
        "app": "SmallSeaCollectiveCore",
        "team": "NoteToSelf",
        "client": "Smoke Tests",
        "mode": "passthrough",
    })
    assert resp.status_code == 200
    data = resp.json()
    pending_id = data["pending_id"]
    pin = data["pin"]

    resp = requests.post(f"{hub_endpoint}/sessions/confirm", json={
        "pending_id": pending_id,
        "pin": pin,
    })
    assert resp.status_code == 200
    session_hex = resp.json()

    # Derive bucket name from berth_id and pre-create it in MinIO.
    ss = SmallSea.SmallSeaBackend(root_dir=root_dir)
    ss_session = ss._lookup_session(session_hex)
    bucket_name = f"ss-{ss_session.berth_id.hex()[:16]}"

    s3 = boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=bucket_name)

    yield {
        "hub": hub,
        "hub_endpoint": hub_endpoint,
        "session_hex": session_hex,
        "playground_dir": playground_dir,
        "minio": minio,
    }


def test_push_clone_roundtrip_subprocess(hub_env):
    hub_endpoint = hub_env["hub_endpoint"]
    session_hex = hub_env["session_hex"]

    scratch = pathlib.Path(tempfile.mkdtemp())
    try:
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

        # ---- Alice: push via SmallSeaRemote (real HTTP) ----
        alice_remote = CS.SmallSeaRemote(session_hex, base_url=hub_endpoint)
        alice_cod = make_cod_sync(alice_repo, "hub-pub")
        alice_cod.remote = alice_remote
        alice_cod.push_to_remote(["main"])

        # ---- Bob: clone via SmallSeaRemote (real HTTP) ----
        bob_remote = CS.SmallSeaRemote(session_hex, base_url=hub_endpoint)
        latest = bob_remote.get_latest_link()
        assert latest is not None

        link, _etag = latest
        [link_ids, branches, bundles, supp] = link
        assert link_ids[0] == "initial-snapshot"
        assert len(bundles) == 1

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
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
