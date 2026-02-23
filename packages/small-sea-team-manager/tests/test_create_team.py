import sqlite3
import pathlib
import subprocess

from small_sea_team_manager.provisioning import create_new_participant, create_team


def test_create_team(playground_dir):
    root = pathlib.Path(playground_dir)

    # Create Alice
    alice_hex = create_new_participant(root, "Alice")

    # Create a team
    team_id_hex = create_team(root, alice_hex, "CoolProject")
    assert len(team_id_hex) == 32  # 16 bytes (UUIDv7) -> 32 hex chars

    # --- Verify NoteToSelf core.db has team + team_app_zone rows ---
    user_db = root / "Participants" / alice_hex / "NoteToSelf" / "Sync" / "core.db"
    conn = sqlite3.connect(str(user_db))
    conn.row_factory = sqlite3.Row

    teams = conn.execute("SELECT * FROM team WHERE name = 'CoolProject'").fetchall()
    assert len(teams) == 1
    assert teams[0]["id"] == bytes.fromhex(team_id_hex)

    zones = conn.execute(
        "SELECT taz.* FROM team_app_zone taz "
        "JOIN team t ON taz.team_id = t.id "
        "WHERE t.name = 'CoolProject'"
    ).fetchall()
    assert len(zones) == 1
    conn.close()

    # --- Verify team directory and its core.db ---
    team_db = root / "Participants" / alice_hex / "CoolProject" / "Sync" / "core.db"
    assert team_db.exists()

    tconn = sqlite3.connect(str(team_db))
    # member table should exist with Alice as first member
    members = tconn.execute("SELECT * FROM member").fetchall()
    assert len(members) == 1
    assert members[0][0] == bytes.fromhex(alice_hex)  # id column
    tconn.close()

    # --- Verify git repo ---
    team_sync = root / "Participants" / alice_hex / "CoolProject" / "Sync"
    result = subprocess.run(
        ["git", "-C", str(team_sync), "log", "--oneline"],
        capture_output=True, text=True)
    assert result.returncode == 0
    assert "New team: CoolProject" in result.stdout
