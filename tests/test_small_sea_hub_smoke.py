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
import small_sea_team_manager.provisioning as Provisioning
import corncob.protocol as CC


MINIO_PORT = 9500
HUB_PORT = 11500


def working_tree_files(repo_dir):
    """Return {path: content} for all git-tracked files."""
    result = CC.gitCmd(["-C", str(repo_dir), "ls-files"])
    files = {}
    for name in result.stdout.strip().splitlines():
        files[name] = (pathlib.Path(repo_dir) / name).read_text()
    return files


def make_corncob(repo_dir, remote_name):
    """Create a Corncob wired to a specific repo directory."""
    os.chdir(repo_dir)
    corn = CC.Corncob(remote_name)
    corn.gitCmd = CC.gitCmd
    return corn


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=MINIO_PORT)


@pytest.fixture()
def hub_env(playground_dir, minio):
    """Real subprocess hub, participant, session, and S3 bucket — ready to go."""
    root_dir = playground_dir

    # Provision participant directly on disk
    Provisioning.create_new_participant(root_dir, "alice")

    # Start hub as a real subprocess
    hub = hub_server_gen_inner(root_dir, HUB_PORT)
    hub_endpoint = hub["endpoint"]

    # Open session via real HTTP
    resp = requests.post(f"{hub_endpoint}/sessions", json={
        "participant": "alice",
        "app": "SmallSeaCollectiveCore",
        "team": "NoteToSelf",
        "client": "Smoke Tests",
    })
    assert resp.status_code == 200
    session_hex = resp.json()

    # Register MinIO cloud location via real HTTP
    resp = requests.post(f"{hub_endpoint}/cloud_locations", json={
        "session": session_hex,
        "backend": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    })
    assert resp.status_code == 200

    # Derive and pre-create bucket (same logic as in-process test)
    ss = SmallSea.SmallSeaBackend(root_dir=root_dir)
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

    yield {
        "hub": hub,
        "hub_endpoint": hub_endpoint,
        "session_hex": session_hex,
        "playground_dir": playground_dir,
        "minio": minio,
    }

    # Teardown: stop hub subprocess
    hub["proc"].terminate()
    hub["proc"].wait()


def hub_server_gen_inner(root_dir, port):
    """Start a hub subprocess (used by the fixture, not a pytest fixture itself)."""
    import subprocess
    import time

    env = os.environ.copy()
    env["SMALL_SEA_ROOT_DIR"] = root_dir

    cmd = ["uv", "run", "fastapi", "dev",
           "packages/small-sea-hub/small_sea_hub/server.py",
           "--port", str(port)]
    proc = subprocess.Popen(cmd, env=env)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(f"Small Sea Hub exited early (code {proc.returncode})")

    return {
        "proc": proc,
        "port": port,
        "root_dir": root_dir,
        "endpoint": f"http://localhost:{port}",
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
        CC.gitCmd(["init", "-b", "main", str(alice_repo)])
        CC.gitCmd(["-C", str(alice_repo), "config", "user.email", "alice@test"])
        CC.gitCmd(["-C", str(alice_repo), "config", "user.name", "Alice"])

        (alice_repo / "README.md").write_text("# Hub Roundtrip\n")
        (alice_repo / "notes.txt").write_text("testing through the hub\n")
        CC.gitCmd(["-C", str(alice_repo), "add", "-A"])
        CC.gitCmd(["-C", str(alice_repo), "commit", "-m", "initial commit"])

        # ---- Alice: push via SmallSeaRemote (real HTTP) ----
        alice_remote = CC.SmallSeaRemote(session_hex, base_url=hub_endpoint)
        alice_corn = make_corncob(alice_repo, "hub-pub")
        alice_corn.remote = alice_remote
        alice_corn.push_to_remote(["main"])

        # ---- Bob: clone via SmallSeaRemote (real HTTP) ----
        bob_remote = CC.SmallSeaRemote(session_hex, base_url=hub_endpoint)
        latest = bob_remote.get_latest_link()
        assert latest is not None

        [link_ids, branches, bundles, supp] = latest
        assert link_ids[0] == "initial-snapshot"
        assert len(bundles) == 1

        bundle_uid = bundles[0][0]
        with tempfile.TemporaryDirectory() as td:
            bundle_path = f"{td}/clone.bundle"
            bob_remote.download_bundle(bundle_uid, bundle_path)
            CC.gitCmd(["clone", bundle_path, str(bob_repo / "checkout")])

        # Move contents up (clone creates a subdir)
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
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
