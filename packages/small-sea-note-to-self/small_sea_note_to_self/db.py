import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SHARED_DB_FILENAME = "core.db"
LOCAL_DB_FILENAME = "device_local.db"
SHARED_SCHEMA_VERSION = 58
LOCAL_SCHEMA_VERSION = 11


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


class AcceptanceArtifactAlreadyExportedError(Exception):
    """A differently signed acceptance may not replace one already exported.

    The exported bytes may already be in circulation with the inviter, so a
    second signed acceptance for the same proposal must never supersede them.
    """

    def __init__(self, team_id: bytes, proposal_id: bytes):
        super().__init__(
            "An exported admission acceptance already exists for proposal "
            f"{proposal_id.hex()} in team {team_id.hex()}"
        )


_ACCEPTANCE_ARTIFACT_COLUMNS = (
    "team_id",
    "proposal_id",
    "nonce",
    "author_teammate_id",
    "author_device_key_id",
    "acceptance_record_id",
    "acceptance_token",
    "created_at",
    "first_exported_at",
)


def _local_connection(root_dir: str | Path, participant_hex: str) -> sqlite3.Connection:
    local_db = device_local_db_path(root_dir, participant_hex)
    initialize_device_local_db(local_db)
    return sqlite3.connect(local_db)


def list_admission_acceptance_artifacts(
    root_dir: str | Path,
    participant_hex: str,
    team_id: bytes,
) -> list[dict]:
    """Return every stored acceptance artifact for one team.

    Callers decide which (if any) is eligible by matching it against the
    current derived join; this reader never picks for them.
    """
    conn = _local_connection(root_dir, participant_hex)
    try:
        rows = conn.execute(
            f"SELECT {', '.join(_ACCEPTANCE_ARTIFACT_COLUMNS)} "
            "FROM admission_acceptance_artifact WHERE team_id = ?",
            (team_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(_ACCEPTANCE_ARTIFACT_COLUMNS, row)) for row in rows]


def save_admission_acceptance_artifact(
    root_dir: str | Path,
    participant_hex: str,
    *,
    team_id: bytes,
    proposal_id: bytes,
    nonce: bytes,
    author_teammate_id: bytes,
    author_device_key_id: bytes,
    acceptance_record_id: bytes,
    acceptance_token: str,
) -> None:
    """Persist the signed base acceptance for one pending join.

    Writing the identical artifact again is a no-op. A differently signed
    artifact may replace a never-exported row -- deliberate local cleanup makes
    a join retryable -- but never one that has been exported.
    """
    conn = _local_connection(root_dir, participant_hex)
    try:
        with conn:
            existing = conn.execute(
                "SELECT acceptance_token, first_exported_at "
                "FROM admission_acceptance_artifact "
                "WHERE team_id = ? AND proposal_id = ?",
                (team_id, proposal_id),
            ).fetchone()
            if existing is not None:
                if existing[0] == acceptance_token:
                    return
                if existing[1] is not None:
                    raise AcceptanceArtifactAlreadyExportedError(team_id, proposal_id)
            conn.execute(
                "INSERT INTO admission_acceptance_artifact ("
                "team_id, proposal_id, nonce, author_teammate_id, author_device_key_id, "
                "acceptance_record_id, acceptance_token, created_at, first_exported_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(team_id, proposal_id) DO UPDATE SET "
                "nonce = excluded.nonce, "
                "author_teammate_id = excluded.author_teammate_id, "
                "author_device_key_id = excluded.author_device_key_id, "
                "acceptance_record_id = excluded.acceptance_record_id, "
                "acceptance_token = excluded.acceptance_token, "
                "created_at = excluded.created_at",
                (
                    team_id,
                    proposal_id,
                    nonce,
                    author_teammate_id,
                    author_device_key_id,
                    acceptance_record_id,
                    acceptance_token,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    finally:
        conn.close()


def mark_admission_acceptance_artifact_exported(
    root_dir: str | Path,
    participant_hex: str,
    team_id: bytes,
    proposal_id: bytes,
) -> None:
    """Stamp the first export. Later exports of the same bytes leave it alone."""
    conn = _local_connection(root_dir, participant_hex)
    try:
        with conn:
            conn.execute(
                "UPDATE admission_acceptance_artifact "
                "SET first_exported_at = ? "
                "WHERE team_id = ? AND proposal_id = ? AND first_exported_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), team_id, proposal_id),
            )
    finally:
        conn.close()
