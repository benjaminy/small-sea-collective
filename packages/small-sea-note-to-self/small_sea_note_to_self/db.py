import sqlite3
from pathlib import Path


SHARED_DB_FILENAME = "core.db"
LOCAL_DB_FILENAME = "device_local.db"
SHARED_SCHEMA_VERSION = 55
LOCAL_SCHEMA_VERSION = 2


def note_to_self_sync_db_path(root_dir: str | Path, participant_hex: str) -> Path:
    root_dir = Path(root_dir)
    return root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / SHARED_DB_FILENAME


def device_local_db_path(root_dir: str | Path, participant_hex: str) -> Path:
    root_dir = Path(root_dir)
    return root_dir / "Participants" / participant_hex / "NoteToSelf" / "Local" / LOCAL_DB_FILENAME


def _sql_dir() -> Path:
    return Path(__file__).parent / "sql"


def initialize_shared_db(shared_db_path: str | Path) -> None:
    shared_db_path = Path(shared_db_path)
    shared_db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(shared_db_path)
    try:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version == SHARED_SCHEMA_VERSION:
            return
        if current_version > SHARED_SCHEMA_VERSION:
            raise NotImplementedError("TODO: SHARED NOTE_TO_SELF DB FROM THE FUTURE!")
        if current_version != 0:
            raise NotImplementedError("TODO: shared NoteToSelf DB migrations")

        schema = (_sql_dir() / "shared_schema.sql").read_text()
        conn.executescript(schema)
        conn.execute(f"PRAGMA user_version = {SHARED_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def initialize_device_local_db(local_db_path: str | Path) -> None:
    local_db_path = Path(local_db_path)
    local_db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(local_db_path)
    try:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version == LOCAL_SCHEMA_VERSION:
            return
        if current_version > LOCAL_SCHEMA_VERSION:
            raise NotImplementedError("TODO: LOCAL NOTE_TO_SELF DB FROM THE FUTURE!")
        if current_version != 0:
            raise NotImplementedError("TODO: local NoteToSelf DB migrations")

        schema = (_sql_dir() / "device_local_schema.sql").read_text()
        conn.executescript(schema)
        conn.execute(f"PRAGMA user_version = {LOCAL_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def initialize_bootstrap_local_state(root_dir: str | Path, participant_hex: str) -> Path:
    """Create only device-local NoteToSelf state for a joining installation.

    This intentionally does not create the shared NoteToSelf DB.
    """
    root_dir = Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex
    (participant_dir / "NoteToSelf" / "Local").mkdir(parents=True, exist_ok=True)
    (participant_dir / "NoteToSelf" / "Sync").mkdir(parents=True, exist_ok=True)
    local_db = device_local_db_path(root_dir, participant_hex)
    initialize_device_local_db(local_db)
    return local_db


def attached_note_to_self_connection(root_dir: str | Path, participant_hex: str) -> sqlite3.Connection:
    shared_db = note_to_self_sync_db_path(root_dir, participant_hex)
    local_db = device_local_db_path(root_dir, participant_hex)
    initialize_shared_db(shared_db)
    initialize_device_local_db(local_db)

    conn = sqlite3.connect(shared_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("ATTACH DATABASE ? AS local", (str(local_db),))
    return conn
