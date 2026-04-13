# Sync roundtrip: push via Hub, pull from peer via Hub proxy.
#
# Uses a real MinIO subprocess for S3 and a real subprocess Hub with
# SMALL_SEA_AUTO_APPROVE_SESSIONS=1 so no PIN is needed.
#
# Scenario:
#   Alice creates a team, pushes an app git repo to her cloud bucket.
#   Bob is set up as a peer (manually, bypassing the invitation flow).
#   Bob opens a session on the same team and pulls Alice's repo via the
#   Hub proxy endpoint (GET /peer_cloud_file).

import os
import pathlib
import shutil
import tempfile

import pytest
import requests

_REPO_ROOT = str(pathlib.Path(__file__).parent.parent)
from botocore.config import Config as BotoConfig
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import small_sea_manager.provisioning as Provisioning
import cod_sync.protocol as CS


MINIO_PORT = 9600
HUB_PORT = 11600


def _git(args, repo_dir=None):
    """Run a git command, optionally scoped to repo_dir."""
    prefix = ["-C", str(repo_dir)] if repo_dir else []
    return CS.gitCmd(prefix + args)


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=MINIO_PORT)


@pytest.fixture(scope="module")
def hub(hub_server_gen, tmp_path_factory, minio):
    root_dir = str(tmp_path_factory.mktemp("hub_root"))
    import os as _os
    env = _os.environ.copy()
    env["SMALL_SEA_ROOT_DIR"] = root_dir
    env["SMALL_SEA_AUTO_APPROVE_SESSIONS"] = "1"

    import subprocess, time
    cmd = [
        "uv", "run", "fastapi", "dev",
        "packages/small-sea-hub/small_sea_hub/server.py",
        "--port", str(HUB_PORT),
    ]
    proc = subprocess.Popen(cmd, env=env, cwd=_REPO_ROOT)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(f"Hub exited early (code {proc.returncode})")

    yield {"root_dir": root_dir, "endpoint": f"http://localhost:{HUB_PORT}", "proc": proc}

    proc.terminate()
    proc.wait()


@pytest.fixture()
def sync_env(playground_dir, minio, hub):
    """Two participants (Alice, Bob) wired to a shared MinIO and Hub."""
    root_dir = hub["root_dir"]
    hub_endpoint = hub["endpoint"]
    minio_endpoint = minio["endpoint"]

    # Provision participants
    alice_hex = Provisioning.create_new_participant(root_dir, "alice_sync")
    bob_hex = Provisioning.create_new_participant(root_dir, "bob_sync")

    def _add_cloud(participant_hex):
        Provisioning.add_cloud_storage(
            root_dir, participant_hex, "s3", minio_endpoint,
            access_key=minio["access_key"],
            secret_key=minio["secret_key"],
        )

    _add_cloud(alice_hex)
    _add_cloud(bob_hex)

    # Alice creates the team; the team DB gets a fresh berth_id.
    alice_team = Provisioning.create_team(root_dir, alice_hex, "SyncTest")
    berth_id_hex = alice_team["berth_id_hex"]
    alice_member_id_hex = alice_team["member_id_hex"]
    berth_id = bytes.fromhex(berth_id_hex)

    # Replicate Alice's team DB for Bob (simulate the invitation/clone flow).
    alice_team_sync = (
        pathlib.Path(root_dir) / "Participants" / alice_hex / "SyncTest" / "Sync"
    )
    bob_team_sync = (
        pathlib.Path(root_dir) / "Participants" / bob_hex / "SyncTest" / "Sync"
    )
    shutil.copytree(str(alice_team_sync), str(bob_team_sync))

    # Add team row to Bob's NoteToSelf so Hub can resolve his berth.
    bob_member_id = os.urandom(16)
    bob_nts_db = (
        pathlib.Path(root_dir)
        / "Participants" / bob_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    team_id = bytes.fromhex(alice_team["team_id_hex"])
    engine_nts = create_engine(f"sqlite:///{bob_nts_db}")
    with engine_nts.begin() as conn:
        conn.execute(
            text("INSERT INTO team (id, name, self_in_team) VALUES (:id, :name, :sim)"),
            {"id": team_id, "name": "SyncTest", "sim": bob_member_id},
        )

    # Add Bob as a member + peer in his own team DB copy.
    bob_team_db = bob_team_sync / "core.db"
    engine_team = create_engine(f"sqlite:///{bob_team_db}")
    with engine_team.begin() as conn:
        conn.execute(
            text("INSERT INTO member (id) VALUES (:id)"),
            {"id": bob_member_id},
        )
        conn.execute(
            text("INSERT INTO berth_role (id, member_id, berth_id, role) VALUES (:id, :mid, :bid, :role)"),
            {"id": os.urandom(16), "mid": bob_member_id, "bid": berth_id, "role": "read-write"},
        )
        # Alice is the peer Bob will pull from.
        conn.execute(
            text("INSERT INTO peer (id, member_id, protocol, url) VALUES (:id, :mid, :proto, :url)"),
            {"id": os.urandom(16), "mid": bytes.fromhex(alice_member_id_hex), "proto": "s3", "url": minio_endpoint},
        )

    # Open sessions via auto-approve.
    def _open_session(nickname):
        resp = requests.post(f"{hub_endpoint}/sessions/request", json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": "SyncTest",
            "client": "test",
            "mode": "passthrough",
        })
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]

    alice_token = _open_session("alice_sync")
    bob_token = _open_session("bob_sync")

    yield {
        "root_dir": root_dir,
        "hub_endpoint": hub_endpoint,
        "minio": minio,
        "alice_hex": alice_hex,
        "bob_hex": bob_hex,
        "alice_token": alice_token,
        "bob_token": bob_token,
        "alice_member_id_hex": alice_member_id_hex,
        "berth_id_hex": berth_id_hex,
    }


def test_push_and_pull_via_hub(sync_env):
    """Alice pushes an app repo; Bob pulls it via the Hub peer proxy."""
    hub_endpoint = sync_env["hub_endpoint"]
    alice_token = sync_env["alice_token"]
    bob_token = sync_env["bob_token"]
    alice_member_id_hex = sync_env["alice_member_id_hex"]

    scratch = pathlib.Path(tempfile.mkdtemp())
    try:
        alice_repo = scratch / "alice-app"
        bob_repo = scratch / "bob-app"
        alice_repo.mkdir()
        bob_repo.mkdir()

        # ---- Alice: init repo and commit ----
        _git(["init", "-b", "main", str(alice_repo)])
        _git(["config", "user.email", "alice@test"], repo_dir=alice_repo)
        _git(["config", "user.name", "Alice"], repo_dir=alice_repo)
        (alice_repo / "data.txt").write_text("Hello from Alice!\n")
        _git(["add", "-A"], repo_dir=alice_repo)
        _git(["commit", "-m", "Alice's data"], repo_dir=alice_repo)

        # ---- Alice: ensure_cloud_ready (creates + publishes bucket) ----
        resp = requests.post(
            f"{hub_endpoint}/cloud/setup",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert resp.status_code == 200, resp.text

        # ---- Alice: push via SmallSeaRemote ----
        alice_remote = CS.SmallSeaRemote(alice_token, base_url=hub_endpoint)
        alice_cod = CS.CodSync("origin", repo_dir=alice_repo)
        alice_cod.remote = alice_remote
        alice_cod.push_to_remote(["main"])

        # ---- Bob: fetch via peer proxy ----
        bob_remote = CS.PeerSmallSeaRemote(
            bob_token, alice_member_id_hex, base_url=hub_endpoint
        )
        bob_cod = CS.CodSync("peer", repo_dir=bob_repo)
        bob_cod.remote = bob_remote

        # Init Bob's repo so fetch/merge has somewhere to land.
        _git(["init", "-b", "main", str(bob_repo)])
        _git(["config", "user.email", "bob@test"], repo_dir=bob_repo)
        _git(["config", "user.name", "Bob"], repo_dir=bob_repo)

        bob_cod.fetch_from_remote(["main"])
        exit_code = bob_cod.merge_from_remote(["main"])
        assert exit_code == 0, "Merge failed"

        # ---- Verify ----
        assert (bob_repo / "data.txt").exists()
        assert (bob_repo / "data.txt").read_text() == "Hello from Alice!\n"

    finally:
        shutil.rmtree(scratch, ignore_errors=True)
