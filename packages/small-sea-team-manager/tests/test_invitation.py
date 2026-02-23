import sqlite3
import pathlib
import subprocess

import pytest

from small_sea_team_manager.provisioning import (
    create_new_participant, create_team,
    create_invitation, accept_invitation, list_invitations,
)


ALICE_CLOUD = {
    "protocol": "s3",
    "url": "http://localhost:9000/alice-bucket",
    "access_key": "alice-key",
    "secret_key": "alice-secret",
}

BOB_CLOUD = {
    "protocol": "s3",
    "url": "http://localhost:9000/bob-bucket",
    "access_key": "bob-key",
    "secret_key": "bob-secret",
}


def test_create_invitation(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")

    token = create_invitation(root, alice_hex, "ProjectX", ALICE_CLOUD, invitee_label="Bob")
    assert isinstance(token, str)
    assert len(token) > 0

    # Verify invitation row exists
    invitations = list_invitations(root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "pending"
    assert invitations[0]["invitee_label"] == "Bob"


def test_full_invitation_flow(playground_dir):
    root = pathlib.Path(playground_dir)

    # Alice creates a team
    alice_hex = create_new_participant(root, "Alice")
    team_result = create_team(root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]

    # Alice creates an invitation
    token = create_invitation(root, alice_hex, "ProjectX", ALICE_CLOUD, invitee_label="Bob")

    # Bob accepts the invitation
    bob_hex = create_new_participant(root, "Bob")
    result = accept_invitation(root, bob_hex, token, BOB_CLOUD)
    assert result["team_name"] == "ProjectX"
    bob_member_id_hex = result["member_id_hex"]

    # Bob's member ID should be a fresh UUIDv7 (not his participant ID)
    assert bob_member_id_hex != bob_hex
    assert len(bob_member_id_hex) == 32

    # --- Verify Alice's invitation is accepted ---
    invitations = list_invitations(root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "accepted"

    # --- Verify Alice's team DB has 2 members and a peer (Bob) ---
    alice_team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
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
    assert peers[0][2] == BOB_CLOUD["url"]
    aconn.close()

    # --- Verify Bob's team DB has 2 members and a peer (Alice) ---
    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
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
    assert peers[0][2] == ALICE_CLOUD["url"]
    bconn.close()

    # --- Verify Bob's NoteToSelf has the team with correct self_in_team ---
    bob_user_db = root / "Participants" / bob_hex / "NoteToSelf" / "Sync" / "core.db"
    buconn = sqlite3.connect(str(bob_user_db))
    buconn.row_factory = sqlite3.Row
    teams = buconn.execute("SELECT * FROM team WHERE name = 'ProjectX'").fetchall()
    assert len(teams) == 1
    assert teams[0]["self_in_team"] == bytes.fromhex(bob_member_id_hex)
    buconn.close()

    # --- Verify Bob's team dir has a git repo ---
    bob_sync = root / "Participants" / bob_hex / "ProjectX" / "Sync"
    result = subprocess.run(
        ["git", "-C", str(bob_sync), "log", "--oneline"],
        capture_output=True, text=True)
    assert result.returncode == 0
    assert "Joined team: ProjectX" in result.stdout


def test_double_accept_rejected(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")
    token = create_invitation(root, alice_hex, "ProjectX", ALICE_CLOUD)

    # First accept succeeds
    bob_hex = create_new_participant(root, "Bob")
    accept_invitation(root, bob_hex, token, BOB_CLOUD)

    # Second accept fails
    carol_hex = create_new_participant(root, "Carol")
    with pytest.raises(ValueError, match="not pending"):
        accept_invitation(root, carol_hex, token, {
            "protocol": "s3",
            "url": "http://localhost:9000/carol-bucket",
            "access_key": "carol-key",
            "secret_key": "carol-secret",
        })
