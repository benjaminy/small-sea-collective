import sqlite3
from pathlib import Path


SHARED_DB_FILENAME = "core.db"
LOCAL_DB_FILENAME = "device_local.db"
SHARED_SCHEMA_VERSION = 56
LOCAL_SCHEMA_VERSION = 5


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
            _migrate_device_local_db(conn, current_version)
            conn.execute(f"PRAGMA user_version = {LOCAL_SCHEMA_VERSION}")
            conn.commit()
            return

        schema = (_sql_dir() / "device_local_schema.sql").read_text()
        conn.executescript(schema)
        conn.execute(f"PRAGMA user_version = {LOCAL_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def _migrate_device_local_db(conn: sqlite3.Connection, current_version: int) -> None:
    if current_version < 4:
        _rename_sender_key_column_if_present(conn, "team_sender_key")
        _rename_sender_key_column_if_present(conn, "peer_sender_key")
    if current_version < 5:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS linked_team_bootstrap_session (
                bootstrap_id BLOB PRIMARY KEY,
                team_id BLOB NOT NULL,
                device_id BLOB NOT NULL,
                team_device_public_key BLOB NOT NULL,
                team_device_private_key BLOB,
                x3dh_identity_dh_public_key BLOB NOT NULL,
                x3dh_identity_dh_private_key BLOB NOT NULL,
                x3dh_identity_signing_public_key BLOB NOT NULL,
                x3dh_identity_signing_private_key BLOB NOT NULL,
                signed_prekey_id BLOB NOT NULL,
                signed_prekey_public_key BLOB NOT NULL,
                signed_prekey_private_key BLOB NOT NULL,
                one_time_prekey_id BLOB,
                one_time_prekey_public_key BLOB,
                one_time_prekey_private_key BLOB,
                ratchet_state_json TEXT,
                finalized_at TEXT,
                response_payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_linked_team_bootstrap (
                bootstrap_id BLOB PRIMARY KEY,
                team_id BLOB NOT NULL,
                peer_device_id BLOB NOT NULL,
                peer_team_device_public_key BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def _rename_sender_key_column_if_present(conn: sqlite3.Connection, table_name: str) -> None:
    columns = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if "sender_participant_id" not in columns or "sender_device_key_id" in columns:
        return
    conn.execute(
        f"ALTER TABLE {table_name} "
        "RENAME COLUMN sender_participant_id TO sender_device_key_id"
    )


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
