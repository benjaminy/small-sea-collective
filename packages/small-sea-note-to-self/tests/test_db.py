import sqlite3

import pytest

from small_sea_note_to_self.db import (
    FutureNoteToSelfDatabaseVersionError,
    LOCAL_SCHEMA_VERSION,
    SHARED_SCHEMA_VERSION,
    initialize_device_local_db,
    initialize_shared_db,
)


def _table_names(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def _columns(db_path, table):
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def _stamp_future_version(db_path, version):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()


def test_fresh_shared_db_has_a_nullable_user_device_label(tmp_path):
    """The participant's own device labels live here, not in any team database."""
    db_path = tmp_path / "core.db"
    initialize_shared_db(db_path)

    label = _columns(db_path, "user_device").get("label")
    assert label is not None, "user_device.label is missing from the shared schema"
    _cid, _name, column_type, notnull, default, _pk = label
    assert column_type == "TEXT"
    assert notnull == 0
    assert default is None

    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SHARED_SCHEMA_VERSION


def test_future_shared_note_to_self_db_version_fails_fast(tmp_path):
    db_path = tmp_path / "core.db"
    future_version = SHARED_SCHEMA_VERSION + 1
    _stamp_future_version(db_path, future_version)

    with pytest.raises(FutureNoteToSelfDatabaseVersionError) as exc_info:
        initialize_shared_db(db_path)

    message = str(exc_info.value)
    assert str(db_path) in message
    assert "shared" in message
    assert str(future_version) in message
    assert str(SHARED_SCHEMA_VERSION) in message

    with sqlite3.connect(str(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == future_version
    assert "user_device" not in _table_names(db_path)


def test_future_device_local_note_to_self_db_version_fails_fast(tmp_path):
    db_path = tmp_path / "device_local.db"
    future_version = LOCAL_SCHEMA_VERSION + 1
    _stamp_future_version(db_path, future_version)

    with pytest.raises(FutureNoteToSelfDatabaseVersionError) as exc_info:
        initialize_device_local_db(db_path)

    message = str(exc_info.value)
    assert str(db_path) in message
    assert "device-local" in message
    assert str(future_version) in message
    assert str(LOCAL_SCHEMA_VERSION) in message

    with sqlite3.connect(str(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == future_version
    assert "note_to_self_sync_state" not in _table_names(db_path)


def test_old_shared_note_to_self_db_version_requires_recreate(tmp_path):
    """Bumping the schema version has to reject stale workspaces, not silently adopt them."""
    db_path = tmp_path / "core.db"
    old_version = SHARED_SCHEMA_VERSION - 1
    _stamp_future_version(db_path, old_version)

    with pytest.raises(NotImplementedError) as exc_info:
        initialize_shared_db(db_path)

    assert "shared NoteToSelf DB migrations" in str(exc_info.value)

    with sqlite3.connect(str(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == old_version
    assert "user_device" not in _table_names(db_path)


def test_old_device_local_note_to_self_db_version_requires_recreate(tmp_path):
    db_path = tmp_path / "device_local.db"
    old_version = LOCAL_SCHEMA_VERSION - 1
    _stamp_future_version(db_path, old_version)

    with pytest.raises(NotImplementedError) as exc_info:
        initialize_device_local_db(db_path)

    message = str(exc_info.value)
    assert "Pre-alpha NoteToSelf device-local DB migrations are not supported" in message
    assert str(old_version) in message
    assert str(LOCAL_SCHEMA_VERSION) in message

    with sqlite3.connect(str(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == old_version
    assert "note_to_self_sync_state" not in _table_names(db_path)
