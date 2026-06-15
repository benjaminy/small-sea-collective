import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SHARED_DB_FILENAME = "core.db"
LOCAL_DB_FILENAME = "device_local.db"
SHARED_SCHEMA_VERSION = 57
LOCAL_SCHEMA_VERSION = 10


class FutureNoteToSelfDatabaseVersionError(Exception):
    def __init__(
        self,
        db_path: str | Path,
        database_label: str,
        actual_version: int,
        supported_version: int,
    ):
        self.db_path = Path(db_path)
        self.database_label = database_label
        self.actual_version = actual_version
        self.supported_version = supported_version
        super().__init__(
            "NoteToSelf "
            f"{database_label} database {self.db_path} "
            f"has schema version {actual_version}, "
            f"but this NoteToSelf package supports up to {supported_version}. "
            "Upgrade Small Sea before opening this database."
        )


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
            raise FutureNoteToSelfDatabaseVersionError(
                shared_db_path,
                "shared",
                current_version,
                SHARED_SCHEMA_VERSION,
            )
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
            raise FutureNoteToSelfDatabaseVersionError(
                local_db_path,
                "device-local",
                current_version,
                LOCAL_SCHEMA_VERSION,
            )
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
    """Scaffold for future device-local DB migrations.

    Pre-alpha databases older than the current schema are intentionally not
    migrated. Delete and recreate the local workspace instead.
    """
    raise NotImplementedError(
        "Pre-alpha NoteToSelf device-local DB migrations are not supported; "
        f"delete/recreate this DB (schema {current_version} -> {LOCAL_SCHEMA_VERSION})."
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


def get_note_to_self_adopted_count(
    root_dir: str | Path, participant_hex: str, berth_id: bytes
) -> int | None:
    local_db = device_local_db_path(root_dir, participant_hex)
    initialize_device_local_db(local_db)
    conn = sqlite3.connect(local_db)
    try:
        row = conn.execute(
            """
            SELECT last_adopted_count
            FROM note_to_self_sync_state
            WHERE berth_id = ?
            """,
            (berth_id,),
        ).fetchone()
        return None if row is None else int(row[0])
    finally:
        conn.close()


def set_note_to_self_adopted_count(
    root_dir: str | Path, participant_hex: str, berth_id: bytes, count: int
) -> None:
    local_db = device_local_db_path(root_dir, participant_hex)
    initialize_device_local_db(local_db)
    conn = sqlite3.connect(local_db)
    try:
        conn.execute(
            """
            INSERT INTO note_to_self_sync_state (berth_id, last_adopted_count, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(berth_id) DO UPDATE SET
                last_adopted_count = excluded.last_adopted_count,
                updated_at = excluded.updated_at
            """,
            (berth_id, int(count), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
