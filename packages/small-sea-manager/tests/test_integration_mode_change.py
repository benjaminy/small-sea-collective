"""Micro tests for the Team Constitution's `integration_mode_change` record.

See Documentation/team-constitution.md and issue #163.
"""

import pathlib
import sqlite3

import pytest
from small_sea_note_to_self.ids import uuid7

from small_sea_manager.provisioning import (
    create_new_participant,
    create_team,
    get_current_team_device_key,
    set_teammate_integration_mode,
)
from wrasse_trust.constitution import canonical_constitution_bytes, verify_constitution_record
from wrasse_trust.keys import key_id_from_public


def _team_db(root, participant_hex, team_name):
    return root / "Participants" / participant_hex / team_name / "Sync" / "core.db"


def _note_to_self_db(root, participant_hex):
    return root / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"


def _core_berth_id(db_path) -> bytes:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT tab.id FROM team_app_berth tab "
            "JOIN app a ON a.id = tab.app_id WHERE a.name = 'SmallSeaCollectiveCore'"
        ).fetchone()
    return row[0]


def _self_teammate_id(root, participant_hex, team_name) -> bytes:
    with sqlite3.connect(str(_note_to_self_db(root, participant_hex))) as conn:
        return conn.execute(
            "SELECT self_in_team FROM team WHERE name = ?", (team_name,)
        ).fetchone()[0]


def _insert_bare_teammate(db_path, teammate_id: bytes, berth_id: bytes | None = None, role: str | None = None):
    """Insert a teammate row directly, bypassing the admission ceremony -- this
    branch is only exercising integration-mode changes, not admission."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO teammate (id, display_name) VALUES (?, ?)", (teammate_id, "Bob"))
        if berth_id is not None:
            conn.execute(
                "INSERT INTO berth_role (id, teammate_id, berth_id, role) VALUES (?, ?, ?, ?)",
                (uuid7(), teammate_id, berth_id, role),
            )
        conn.commit()


def _berth_role_row(db_path, teammate_id: bytes, berth_id: bytes) -> str | None:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT role FROM berth_role WHERE teammate_id = ? AND berth_id = ?",
            (teammate_id, berth_id),
        ).fetchone()
    return row[0] if row is not None else None


def test_set_integration_mode_appends_record_and_updates_projection(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "CoolProject")

    db_path = _team_db(root, alice_hex, "CoolProject")
    core_berth_id = _core_berth_id(db_path)
    bob_id = uuid7()
    _insert_bare_teammate(db_path, bob_id, berth_id=core_berth_id, role="read-write")

    record_id = set_teammate_integration_mode(
        root, alice_hex, "CoolProject", bob_id, core_berth_id, "proposal-only"
    )

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT record_id, record_type, author_teammate_id, author_device_key_id, "
            "created_at, anchor_commit, constitution_digest, schema_version, "
            "teammate_id, berth_id, mode, signature "
            "FROM integration_mode_change WHERE record_id = ?",
            (record_id,),
        ).fetchone()
    assert row is not None
    (
        row_record_id, record_type, author_teammate_id, author_device_key_id,
        created_at, anchor_commit, digest, schema_version,
        row_teammate_id, row_berth_id, mode, signature,
    ) = row
    assert row_record_id == record_id
    assert record_type == "integration_mode_change"
    assert row_teammate_id == bob_id
    assert row_berth_id == core_berth_id
    assert mode == "proposal-only"
    assert schema_version == 1
    assert author_teammate_id == _self_teammate_id(root, alice_hex, "CoolProject")
    assert anchor_commit  # a real git commit hash, not left null for a governance-bearing record

    _, author_public_key = get_current_team_device_key(root, alice_hex, "CoolProject")
    assert author_device_key_id == key_id_from_public(author_public_key)

    # Every envelope column except record_id/signature must be part of the
    # signed bytes (Documentation/team-constitution.md, "Canonical bytes:").
    signed_fields = {
        "record_type": record_type,
        "author_teammate_id": author_teammate_id.hex(),
        "author_device_key_id": author_device_key_id.hex(),
        "created_at": created_at,
        "anchor_commit": anchor_commit,
        "constitution_digest": digest.hex(),
        "schema_version": schema_version,
        "teammate_id": row_teammate_id.hex(),
        "berth_id": row_berth_id.hex(),
        "mode": mode,
    }
    canonical = canonical_constitution_bytes(signed_fields)
    assert verify_constitution_record(author_public_key, canonical, signature)

    # anchor_commit is envelope, not payload: tampering with it must break verification.
    tampered_fields = dict(signed_fields, anchor_commit="0" * 40)
    tampered_canonical = canonical_constitution_bytes(tampered_fields)
    assert not verify_constitution_record(author_public_key, tampered_canonical, signature)

    assert _berth_role_row(db_path, bob_id, core_berth_id) == "read-only"


def test_set_integration_mode_rejects_key_without_cert_trust(playground_dir):
    """A record signed by a key that doesn't resolve through the cert graph
    is unverifiable to peers -- catch that locally rather than publish it.

    This is the same check `issue_device_link_for_teammate` already relies on
    (`resolve_trusted_device_keys_for_teammate`), not the weaker `team_device`
    table, which is a cache that could in principle exist without matching
    cert-graph trust."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "CoolProject")

    db_path = _team_db(root, alice_hex, "CoolProject")
    core_berth_id = _core_berth_id(db_path)
    bob_id = uuid7()
    _insert_bare_teammate(db_path, bob_id, berth_id=core_berth_id, role="read-write")

    # Remove Alice's genesis membership cert, so her own device key no longer
    # resolves through the cert graph, even though her `team_device` row and
    # `berth_role` standing are untouched.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM key_certificate")
        conn.commit()

    with pytest.raises(ValueError, match="not certificate-trusted"):
        set_teammate_integration_mode(root, alice_hex, "CoolProject", bob_id, core_berth_id, "proposal-only")


def test_set_integration_mode_inserts_projection_row_when_none_exists(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "CoolProject")

    db_path = _team_db(root, alice_hex, "CoolProject")
    core_berth_id = _core_berth_id(db_path)
    bob_id = uuid7()
    _insert_bare_teammate(db_path, bob_id)  # no berth_role row at all yet

    assert _berth_role_row(db_path, bob_id, core_berth_id) is None

    set_teammate_integration_mode(root, alice_hex, "CoolProject", bob_id, core_berth_id, "automatic")

    assert _berth_role_row(db_path, bob_id, core_berth_id) == "read-write"


def test_set_integration_mode_rejects_caller_without_standing_on_berth(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "CoolProject")

    db_path = _team_db(root, alice_hex, "CoolProject")
    core_berth_id = _core_berth_id(db_path)
    bob_id = uuid7()
    _insert_bare_teammate(db_path, bob_id, berth_id=core_berth_id, role="read-write")

    # Simulate Alice having been downgraded off automatic standing on Core.
    alice_teammate_id = _self_teammate_id(root, alice_hex, "CoolProject")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE berth_role SET role = 'read-only' WHERE teammate_id = ? AND berth_id = ?",
            (alice_teammate_id, core_berth_id),
        )
        conn.commit()

    with pytest.raises(ValueError, match="automatic"):
        set_teammate_integration_mode(root, alice_hex, "CoolProject", bob_id, core_berth_id, "proposal-only")


def test_set_integration_mode_rejects_unknown_teammate(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "CoolProject")
    db_path = _team_db(root, alice_hex, "CoolProject")
    core_berth_id = _core_berth_id(db_path)

    with pytest.raises(ValueError, match="not found"):
        set_teammate_integration_mode(
            root, alice_hex, "CoolProject", uuid7(), core_berth_id, "automatic"
        )


def test_set_integration_mode_rejects_unknown_mode_value(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "CoolProject")
    db_path = _team_db(root, alice_hex, "CoolProject")
    core_berth_id = _core_berth_id(db_path)
    bob_id = uuid7()
    _insert_bare_teammate(db_path, bob_id, berth_id=core_berth_id, role="read-write")

    with pytest.raises(ValueError, match="Unknown integration mode"):
        set_teammate_integration_mode(
            root, alice_hex, "CoolProject", bob_id, core_berth_id, "read-write"
        )
