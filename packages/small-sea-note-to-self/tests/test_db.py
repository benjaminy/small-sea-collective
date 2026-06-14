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


def _stamp_future_version(db_path, version):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()


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
