import base64
import json
import os
import pathlib
import sqlite3
import subprocess

import cod_sync.protocol as CS
import pytest
from cod_sync.testing import S3Remote
from small_sea_manager.provisioning import (
    accept_invitation, complete_invitation_acceptance, create_invitation,
    create_new_participant, create_team, list_invitations)


def _make_cod_sync(repo_dir, remote_name):
    """Create a CodSync wired to a specific repo directory."""
    os.chdir(repo_dir)
    cod = CS.CodSync(remote_name)
    return cod


def test_create_invitation(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")

    alice_cloud = {
        "protocol": "s3",
        "url": "http://localhost:9000",
        "access_key": "alice-key",
        "secret_key": "alice-secret",
    }
    token = create_invitation(
        root, alice_hex, "ProjectX", alice_cloud, invitee_label="Bob"
    )
    assert isinstance(token, str)
    assert len(token) > 0

    # Verify invitation row exists
    invitations = list_invitations(root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "pending"
    assert invitations[0]["invitee_label"] == "Bob"


def test_create_invitation_includes_bucket(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")

    alice_cloud = {
        "protocol": "s3",
        "url": "http://localhost:9000",
        "access_key": "alice-key",
        "secret_key": "alice-secret",
    }
    token_b64 = create_invitation(root, alice_hex, "ProjectX", alice_cloud)
    token_json = base64.b64decode(token_b64).decode()
    token = json.loads(token_json)

    assert "inviter_bucket" in token
    assert token["inviter_bucket"].startswith("ss-")
    assert len(token["inviter_bucket"]) == 3 + 16  # "ss-" + 16 hex chars


def test_full_invitation_flow(playground_dir, minio_server_gen):
    """Full decentralized invitation flow with separate root dirs and MinIO."""
    minio = minio_server_gen(port=19100)

    alice_root = pathlib.Path(playground_dir) / "alice-root"
    bob_root = pathlib.Path(playground_dir) / "bob-root"
    alice_root.mkdir()
    bob_root.mkdir()

    # Alice creates participant + team
    alice_hex = create_new_participant(alice_root, "Alice")
    team_result = create_team(alice_root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]

    # Alice's cloud info
    alice_bucket = "alice-team-bucket"
    alice_cloud = {
        "protocol": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    }

    # Alice pushes team repo to MinIO
    alice_team_sync = alice_root / "Participants" / alice_hex / "ProjectX" / "Sync"
    alice_remote = S3Remote(
        minio["endpoint"], alice_bucket, minio["access_key"], minio["secret_key"]
    )
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    # Alice creates invitation (token includes bucket info)
    token = create_invitation(
        alice_root, alice_hex, "ProjectX", alice_cloud, invitee_label="Bob"
    )

    # Verify token has inviter_bucket
    token_data = json.loads(base64.b64decode(token).decode())
    assert "inviter_bucket" in token_data

    # Patch the token to use alice_bucket (since the auto-derived bucket differs)
    token_data["inviter_bucket"] = alice_bucket
    token = base64.b64encode(json.dumps(token_data).encode()).decode()

    # Re-push after invitation commit
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    # Bob creates participant
    bob_hex = create_new_participant(bob_root, "Bob")
    bob_bucket = "bob-team-bucket"
    bob_cloud = {
        "protocol": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    }

    # Bob accepts invitation (clones from Alice's MinIO, pushes to his bucket)
    bob_remote = S3Remote(
        minio["endpoint"], bob_bucket, minio["access_key"], minio["secret_key"]
    )
    acceptance_b64 = accept_invitation(
        bob_root, bob_hex, token, bob_cloud, bob_bucket,
        inviter_remote=alice_remote, acceptor_remote=bob_remote,
    )
    assert isinstance(acceptance_b64, str)

    # Decode acceptance to get Bob's member ID
    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    bob_member_id_hex = acceptance["acceptor_member_id"]

    # Bob's member ID should be a fresh UUIDv7
    assert bob_member_id_hex != bob_hex
    assert len(bob_member_id_hex) == 32

    # Alice completes the acceptance
    complete_invitation_acceptance(alice_root, alice_hex, "ProjectX", acceptance_b64)

    # --- Verify Alice's invitation is accepted ---
    invitations = list_invitations(alice_root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "accepted"

    # --- Verify Alice's team DB has 2 members, a peer (Bob), and 2 station_roles ---
    alice_team_db = (
        alice_root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    )
    aconn = sqlite3.connect(str(alice_team_db))
    members = aconn.execute("SELECT id FROM member").fetchall()
    assert len(members) == 2
    member_ids = {row[0].hex() for row in members}
    assert alice_member_id_hex in member_ids
    assert bob_member_id_hex in member_ids

    peers = aconn.execute("SELECT member_id, protocol, url FROM peer").fetchall()
    assert len(peers) == 1
    assert peers[0][0] == bytes.fromhex(bob_member_id_hex)
    assert peers[0][1] == "s3"
    assert peers[0][2] == bob_cloud["url"]

    # Both Alice and Bob should have read-write roles
    roles = aconn.execute("SELECT member_id, role FROM station_role").fetchall()
    assert len(roles) == 2
    role_map = {row[0].hex(): row[1] for row in roles}
    assert role_map[alice_member_id_hex] == "read-write"
    assert role_map[bob_member_id_hex] == "read-write"
    aconn.close()

    # --- Verify Bob's team DB has 2 members and a peer (Alice) ---
    bob_team_db = bob_root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    bconn = sqlite3.connect(str(bob_team_db))
    members = bconn.execute("SELECT id FROM member").fetchall()
    assert len(members) == 2
    member_ids = {row[0].hex() for row in members}
    assert alice_member_id_hex in member_ids
    assert bob_member_id_hex in member_ids

    peers = bconn.execute("SELECT member_id, protocol, url FROM peer").fetchall()
    assert len(peers) == 1
    assert peers[0][0] == bytes.fromhex(alice_member_id_hex)
    assert peers[0][1] == "s3"
    assert peers[0][2] == alice_cloud["url"]
    bconn.close()

    # --- Verify Bob's NoteToSelf has the team pointer but NOT a TeamAppStation for ProjectX ---
    bob_user_db = (
        bob_root / "Participants" / bob_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    buconn = sqlite3.connect(str(bob_user_db))
    buconn.row_factory = sqlite3.Row
    teams = buconn.execute("SELECT * FROM team WHERE name = 'ProjectX'").fetchall()
    assert len(teams) == 1
    assert teams[0]["self_in_team"] == bytes.fromhex(bob_member_id_hex)

    # TeamAppStation for ProjectX must NOT be in NoteToSelf
    other_stations = buconn.execute(
        "SELECT tas.* FROM team_app_station tas "
        "JOIN team t ON tas.team_id = t.id "
        "WHERE t.name = 'ProjectX'"
    ).fetchall()
    assert len(other_stations) == 0
    buconn.close()

    # --- Verify Bob's team dir has a git repo with correct commit ---
    bob_sync = bob_root / "Participants" / bob_hex / "ProjectX" / "Sync"
    result = subprocess.run(
        ["git", "-C", str(bob_sync), "log", "--oneline"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Joined team: ProjectX" in result.stdout


def test_double_accept_rejected(playground_dir, minio_server_gen):
    """Second acceptance of the same invitation should fail."""
    minio = minio_server_gen(port=19200)

    alice_root = pathlib.Path(playground_dir) / "alice-root"
    bob_root = pathlib.Path(playground_dir) / "bob-root"
    carol_root = pathlib.Path(playground_dir) / "carol-root"
    alice_root.mkdir()
    bob_root.mkdir()
    carol_root.mkdir()

    alice_hex = create_new_participant(alice_root, "Alice")
    create_team(alice_root, alice_hex, "ProjectX")

    alice_bucket = "alice-double-bucket"
    alice_cloud = {
        "protocol": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    }

    # Push team repo to MinIO
    alice_team_sync = alice_root / "Participants" / alice_hex / "ProjectX" / "Sync"
    alice_remote = S3Remote(
        minio["endpoint"], alice_bucket, minio["access_key"], minio["secret_key"]
    )
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    token = create_invitation(alice_root, alice_hex, "ProjectX", alice_cloud)

    # Patch token with correct bucket
    token_data = json.loads(base64.b64decode(token).decode())
    token_data["inviter_bucket"] = alice_bucket
    token = base64.b64encode(json.dumps(token_data).encode()).decode()

    # Re-push after invitation commit
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    # Bob accepts
    bob_hex = create_new_participant(bob_root, "Bob")
    bob_bucket = "bob-double-bucket"
    bob_cloud = {
        "protocol": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    }
    bob_remote = S3Remote(
        minio["endpoint"], bob_bucket, minio["access_key"], minio["secret_key"]
    )
    acceptance_b64 = accept_invitation(
        bob_root, bob_hex, token, bob_cloud, bob_bucket,
        inviter_remote=alice_remote, acceptor_remote=bob_remote,
    )

    # Alice completes Bob's acceptance
    complete_invitation_acceptance(alice_root, alice_hex, "ProjectX", acceptance_b64)

    # Carol tries to accept the same invitation — but complete should fail
    carol_hex = create_new_participant(carol_root, "Carol")
    carol_bucket = "carol-double-bucket"
    carol_cloud = {
        "protocol": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    }

    # Re-push so Carol can clone the latest
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    carol_remote = S3Remote(
        minio["endpoint"], carol_bucket, minio["access_key"], minio["secret_key"]
    )
    carol_acceptance_b64 = accept_invitation(
        carol_root, carol_hex, token, carol_cloud, carol_bucket,
        inviter_remote=alice_remote, acceptor_remote=carol_remote,
    )

    with pytest.raises(ValueError, match="not pending"):
        complete_invitation_acceptance(
            alice_root, alice_hex, "ProjectX", carol_acceptance_b64
        )
