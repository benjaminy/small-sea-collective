import json
import pathlib
import sqlite3
import subprocess

import pytest

from small_sea_manager.provisioning import _deserialize_cert, _serialize_cert
from small_sea_manager.provisioning import (create_new_participant,
                                                 create_team)
from wrasse_trust.identity import CertType, issue_cert, verify_membership_cert
from wrasse_trust.keys import generate_hierarchy


ALICE_ID = b"alice-id-bytes00"


def test_serialize_deserialize_cert_round_trip():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    cert = issue_cert(
        guarded,
        buried,
        privates[buried.key_id],
        ALICE_ID,
        cert_type=CertType.SELF_BINDING,
        claims={"type": "hierarchy"},
    )

    assert _deserialize_cert(_serialize_cert(cert)) == cert


def test_deserialize_cert_requires_known_cert_type():
    with pytest.raises(KeyError):
        _deserialize_cert(
            {
                "cert_id": "00" * 16,
                "team_id": None,
                "subject_key_id": "11" * 16,
                "subject_public_key": "22" * 32,
                "issuer_key_id": "33" * 16,
                "issuer_participant_id": "44" * 16,
                "issued_at_iso": "2026-04-07T00:00:00+00:00",
                "claims": {},
                "signature": "55" * 64,
            }
        )

    with pytest.raises(ValueError):
        _deserialize_cert(
            {
                "cert_id": "00" * 16,
                "cert_type": "generic",
                "team_id": None,
                "subject_key_id": "11" * 16,
                "subject_public_key": "22" * 32,
                "issuer_key_id": "33" * 16,
                "issuer_participant_id": "44" * 16,
                "issued_at_iso": "2026-04-07T00:00:00+00:00",
                "claims": {},
                "signature": "55" * 64,
            }
        )


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

    sender_key = conn.execute(
        "SELECT team_id, sender_participant_id, signing_private_key "
        "FROM team_sender_key WHERE team_id = ?",
        (bytes.fromhex(team_id_hex),),
    ).fetchone()
    assert sender_key is not None
    assert sender_key[0] == bytes.fromhex(team_id_hex)
    assert sender_key[1] == bytes.fromhex(member_id_hex)
    assert sender_key[2] is not None

    self_receiver_key = conn.execute(
        "SELECT sender_participant_id, signing_private_key "
        "FROM peer_sender_key WHERE team_id = ? AND sender_participant_id = ?",
        (bytes.fromhex(team_id_hex), bytes.fromhex(member_id_hex)),
    ).fetchone()
    assert self_receiver_key is not None
    assert self_receiver_key[0] == bytes.fromhex(member_id_hex)
    assert self_receiver_key[1] is None

    with pytest.raises(sqlite3.OperationalError):
        conn.execute(
            "SELECT member_id, public_key FROM team_identity WHERE team_id = ?",
            (bytes.fromhex(team_id_hex),),
        ).fetchone()

    with pytest.raises(sqlite3.OperationalError):
        conn.execute(
            "SELECT wrapped_private_key, wrapper_version FROM wrapped_team_identity_key "
            "WHERE team_id = ?",
            (bytes.fromhex(team_id_hex),),
        ).fetchone()

    team_device_key = conn.execute(
        "SELECT public_key, private_key_ref FROM team_device_key WHERE team_id = ?",
        (bytes.fromhex(team_id_hex),),
    ).fetchone()
    assert team_device_key is not None
    assert len(team_device_key[0]) == 32
    assert pathlib.Path(team_device_key[1]).exists()

    # TeamAppBerth for CoolProject must NOT be in NoteToSelf — it belongs in the team DB.
    other_team_berths = conn.execute(
        "SELECT tab.* FROM team_app_berth tab "
        "JOIN team t ON tab.team_id = t.id "
        "WHERE t.name = 'CoolProject'"
    ).fetchall()
    assert len(other_team_berths) == 0
    conn.close()

    # --- Verify team directory and its core.db ---
    team_db = root / "Participants" / alice_hex / "CoolProject" / "Sync" / "core.db"
    assert team_db.exists()

    tconn = sqlite3.connect(str(team_db))

    # member: Alice as first member (fresh per-team ID)
    members = tconn.execute("SELECT * FROM member").fetchall()
    assert len(members) == 1
    assert members[0][0] == bytes.fromhex(member_id_hex)
    assert members[0][1] == team_device_key[0]

    cert_row = tconn.execute(
        "SELECT cert_id, cert_type, subject_key_id, subject_public_key, issuer_key_id, "
        "issuer_member_id, issued_at, claims, signature FROM key_certificate"
    ).fetchone()
    assert cert_row is not None
    cert = _deserialize_cert(
        {
            "cert_id": cert_row[0].hex(),
            "cert_type": cert_row[1],
            "team_id": team_id_hex,
            "subject_key_id": cert_row[2].hex(),
            "subject_public_key": cert_row[3].hex(),
            "issuer_key_id": cert_row[4].hex(),
            "issuer_participant_id": cert_row[5].hex(),
            "issued_at_iso": cert_row[6],
            "claims": json.loads(cert_row[7]),
            "signature": cert_row[8].hex(),
        }
    )
    assert verify_membership_cert(
        cert,
        issuer_public_key=team_device_key[0],
        team_id=bytes.fromhex(team_id_hex),
        issuer_member_id=bytes.fromhex(member_id_hex),
        admitted_member_id=bytes.fromhex(member_id_hex),
        subject_public_key=team_device_key[0],
    )

    # app + team_app_berth live here now
    apps = tconn.execute("SELECT * FROM app").fetchall()
    assert len(apps) == 1
    assert apps[0][1] == "SmallSeaCollectiveCore"

    berths = tconn.execute("SELECT * FROM team_app_berth").fetchall()
    assert len(berths) == 1
    berth_id_hex = result["berth_id_hex"]
    assert berths[0][0] == bytes.fromhex(berth_id_hex)

    # Alice has read-write on the berth
    roles = tconn.execute(
        "SELECT member_id, berth_id, role FROM berth_role"
    ).fetchall()
    assert len(roles) == 1
    assert roles[0][0] == bytes.fromhex(member_id_hex)
    assert roles[0][1] == bytes.fromhex(berth_id_hex)
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
