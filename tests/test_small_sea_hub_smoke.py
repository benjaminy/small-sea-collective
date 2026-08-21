# End-to-end subprocess hub test.
#
# Starts a real hub process and MinIO server, then exercises the full
# push/clone roundtrip over HTTP — proving the deployment path works.

import pathlib
import shutil
import tempfile

import boto3
import requests
from botocore.config import Config as BotoConfig
import pytest

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
import cod_sync.protocol as CS
from cod_sync.format import decode_link
from cod_sync.repo import Repo
from cod_sync.store import SmallSeaStore
from cod_sync.git import gitCmd


MINIO_PORT = 9500
HUB_PORT = 11500


def working_tree_files(repo_dir):
    """Return {path: content} for all git-tracked files."""
    result = gitCmd(["-C", str(repo_dir), "ls-files"])
    files = {}
    for name in result.stdout.strip().splitlines():
        files[name] = (pathlib.Path(repo_dir) / name).read_text()
    return files


def make_repo(repo_dir):
    """Wrap a conventional repository directory."""
    repo_dir = pathlib.Path(repo_dir)
    return Repo(repo_dir / ".git", repo_dir)


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
    storage_id = Provisioning.add_cloud_storage(
        root_dir, alice_hex, "s3", minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )

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

    # Allocate a berth cloud for the session's berth, then pre-create the bucket.
    # The Hub resolves storage per berth via berth_cloud_allocation, so the account
    # config alone is insufficient — the session's berth needs an explicit allocation.
    ss = SmallSea.SmallSeaBackend(root_dir=root_dir)
    ss_session = ss._lookup_session(session_hex)
    allocation = Provisioning.add_berth_cloud_allocation_by_berth_id(
        root_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
    )
    bucket_name = allocation["location"]

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
        gitCmd(["init", "-b", "main", str(alice_repo)])
        gitCmd(["-C", str(alice_repo), "config", "user.email", "alice@test"])
        gitCmd(["-C", str(alice_repo), "config", "user.name", "Alice"])

        (alice_repo / "README.md").write_text("# Hub Roundtrip\n")
        (alice_repo / "notes.txt").write_text("testing through the hub\n")
        gitCmd(["-C", str(alice_repo), "add", "-A"])
        gitCmd(["-C", str(alice_repo), "commit", "-m", "initial commit"])

        # ---- Alice: publish via SmallSeaStore (real HTTP) ----
        alice_store = SmallSeaStore(session_hex, base_url=hub_endpoint)
        published = CS.CodSync(make_repo(alice_repo), alice_store).publish()
        assert published.disposition == "published"

        latest = decode_link(alice_store.get_latest_link()[0])
        assert latest.previous is None
        assert latest.head == published.attempted_head

        # ---- Bob: cold-start fetch via SmallSeaStore (real HTTP) ----
        bob_store = SmallSeaStore(session_hex, base_url=hub_endpoint)
        gitCmd(["init", "-b", "main", str(bob_repo)])
        bob = make_repo(bob_repo)
        result = CS.CodSync(bob, bob_store).fetch()
        bob.checkout_branch("main", result.observed_head)

        # ---- Verify working trees match ----
        alice_files = working_tree_files(alice_repo)
        bob_files = working_tree_files(bob_repo)

        assert alice_files == bob_files
        assert "README.md" in alice_files
        assert "notes.txt" in alice_files
        assert alice_files["README.md"] == "# Hub Roundtrip\n"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
