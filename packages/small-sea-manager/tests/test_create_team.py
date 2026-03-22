import pathlib
import sqlite3
import subprocess

from small_sea_manager.provisioning import (create_new_participant,
                                                 create_team)


def test_create_team(playground_dir):
    root = pathlib.Path(playground_dir)

    # Create Alice
    alice_hex = create_new_participant(root, "Alice")

    # Create a team
    result = create_team(root, alice_hex, "CoolProject")
    team_id_hex = result["team_id_hex"]
    member_id_hex = result["member_id_hex"]
    assert len(team_id_hex) == 32  # 16 bytes (UUIDv7) -> 32 hex chars
    assert len(member_id_hex) == 32
    # Member ID should be different from participant ID (fresh per-team ID)
    assert member_id_hex != alice_hex

    # --- Verify NoteToSelf core.db has only a lightweight team membership pointer ---
    user_db = root / "Participants" / alice_hex / "NoteToSelf" / "Sync" / "core.db"
    conn = sqlite3.connect(str(user_db))
    conn.row_factory = sqlite3.Row

    teams = conn.execute("SELECT * FROM team WHERE name = 'CoolProject'").fetchall()
    assert len(teams) == 1
    assert teams[0]["id"] == bytes.fromhex(team_id_hex)
    assert teams[0]["self_in_team"] == bytes.fromhex(member_id_hex)

    # TeamAppStation for CoolProject must NOT be in NoteToSelf — it belongs in the team DB.
    other_team_stations = conn.execute(
        "SELECT tas.* FROM team_app_station tas "
        "JOIN team t ON tas.team_id = t.id "
        "WHERE t.name = 'CoolProject'"
    ).fetchall()
    assert len(other_team_stations) == 0
    conn.close()

    # --- Verify team directory and its core.db ---
    team_db = root / "Participants" / alice_hex / "CoolProject" / "Sync" / "core.db"
    assert team_db.exists()

    tconn = sqlite3.connect(str(team_db))

    # member: Alice as first member (fresh per-team ID)
    members = tconn.execute("SELECT * FROM member").fetchall()
    assert len(members) == 1
    assert members[0][0] == bytes.fromhex(member_id_hex)

    # app + team_app_station live here now
    apps = tconn.execute("SELECT * FROM app").fetchall()
    assert len(apps) == 1
    assert apps[0][1] == "SmallSeaCollectiveCore"

    stations = tconn.execute("SELECT * FROM team_app_station").fetchall()
    assert len(stations) == 1
    station_id_hex = result["station_id_hex"]
    assert stations[0][0] == bytes.fromhex(station_id_hex)

    # Alice has read-write on the station
    roles = tconn.execute(
        "SELECT member_id, station_id, role FROM station_role"
    ).fetchall()
    assert len(roles) == 1
    assert roles[0][0] == bytes.fromhex(member_id_hex)
    assert roles[0][1] == bytes.fromhex(station_id_hex)
    assert roles[0][2] == "read-write"

    tconn.close()

    # --- Verify git repo ---
    team_sync = root / "Participants" / alice_hex / "CoolProject" / "Sync"
    result = subprocess.run(
        ["git", "-C", str(team_sync), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "New team: CoolProject" in result.stdout
