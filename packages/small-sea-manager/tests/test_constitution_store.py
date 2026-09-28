import pathlib
import sqlite3
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text

from small_sea_manager.constitution_store import (
    EventConflictError,
    add_event,
    get_event,
    list_events,
    list_pending_ids,
)
from small_sea_manager.provisioning import (
    TEAM_SCHEMA_VERSION,
    create_new_participant,
    create_team,
)
from wrasse_trust.events import EventInvalidError, make_event


def _key():
    return Ed25519PrivateKey.generate()


def _event(parents=(), key=None):
    key = key or _key()
    return make_event(
        "member_added", {"name": "Ada"}, tuple(parents), key.private_bytes_raw()
    )


@pytest.fixture
def team_db(tmp_path):
    path = tmp_path / "core.db"
    script = (
        pathlib.Path(__file__).parents[1]
        / "small_sea_manager"
        / "sql"
        / "core_other_team.sql"
    )
    with sqlite3.connect(path) as raw:
        raw.executescript(script.read_text())
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def test_valid_root_event_is_stored(team_db):
    event = _event()
    assert add_event(team_db, event) == ("stored", [event])
    assert get_event(team_db, event.event_id) == event
    assert list_events(team_db) == [event]


def test_readding_same_event_is_already_present(team_db):
    event = _event()
    assert add_event(team_db, event) == ("stored", [event])
    assert add_event(team_db, event) == ("already_present", [])


def test_bad_signature_is_refused_and_nothing_stored(team_db):
    event = replace(_event(), signature=b"\x00" * 64)
    with pytest.raises(EventInvalidError, match="bad_signature"):
        add_event(team_db, event)
    assert list_events(team_db) == []
    assert list_pending_ids(team_db) == []


def test_bad_id_is_refused_and_nothing_stored(team_db):
    event = replace(_event(), event_id=b"\x01" * 32)
    with pytest.raises(EventInvalidError, match="bad_id"):
        add_event(team_db, event)
    assert list_events(team_db) == []
    assert list_pending_ids(team_db) == []


def test_missing_parent_goes_pending(team_db):
    parent = _event()
    child = _event((parent.event_id,))
    assert add_event(team_db, child) == ("pending", [])
    assert get_event(team_db, child.event_id) is None
    assert list_pending_ids(team_db) == [child.event_id]


def test_pending_event_is_promoted_when_parent_arrives(team_db):
    parent = _event()
    child = _event((parent.event_id,))
    assert add_event(team_db, child) == ("pending", [])
    assert add_event(team_db, parent) == ("stored", [parent, child])
    assert get_event(team_db, child.event_id) == child
    assert list_pending_ids(team_db) == []


def test_pending_chain_promotes_transitively(team_db):
    root = _event()
    child = _event((root.event_id,))
    grandchild = _event((child.event_id,))
    assert add_event(team_db, grandchild) == ("pending", [])
    assert add_event(team_db, child) == ("pending", [])
    assert add_event(team_db, root) == ("stored", [root, child, grandchild])
    assert {event.event_id for event in list_events(team_db)} == {
        root.event_id, child.event_id, grandchild.event_id
    }
    assert list_pending_ids(team_db) == []


def test_same_id_different_bytes_conflicts(team_db):
    event = _event()
    team_db.execute(
        text(
            "INSERT INTO constitution_event (event_id, event_type, encoded) "
            "VALUES (:event_id, :event_type, :encoded)"
        ),
        {"event_id": event.event_id, "event_type": event.event_type, "encoded": b"different"},
    )
    with pytest.raises(EventConflictError) as error:
        add_event(team_db, event)
    assert error.value.event_id == event.event_id


def test_store_has_no_delete_function():
    import small_sea_manager.constitution_store as store

    assert not any(name.startswith(("delete", "remove")) for name in vars(store))


def test_new_team_db_has_schema_version_70(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex = create_new_participant(root, "Alice")
    create_team(root, participant_hex, "ProjectX")
    db_path = root / "Participants" / participant_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 70
    assert TEAM_SCHEMA_VERSION == 70
