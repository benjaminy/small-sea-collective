# Participant/user/team/app provisioning for small-sea-manager.
#
# Handles creating participants, initializing per-user databases, and managing
# teams/apps via direct SQLite and filesystem operations. No network I/O.
# Called by TeamManager (manager.py) for all local DB reads and writes.
#
# The SQLAlchemy models here are duplicated from the hub — the SQLite DB
# schema is the shared contract between the two packages.

import base64
import json
import os
import pathlib
import secrets
import sqlite3
import struct
import time
from datetime import datetime, timezone

from sqlalchemy import Column, LargeBinary, String, create_engine, text
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()

import shutil
import subprocess

import cod_sync.protocol as CodSync
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# ---- UUIDv7 ----


def uuid7():
    """Generate a UUIDv7 (time-ordered, random) as 16 bytes."""
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = secrets.token_bytes(10)

    # 48-bit timestamp | 4-bit version (0111) | 12-bit rand_a
    # 2-bit variant (10) | 62-bit rand_b
    high = (timestamp_ms << 16) | 0x7000 | (rand_bytes[0] << 4 | rand_bytes[1] >> 4)
    # This gives us the first 8 bytes
    # Actually let me do this more carefully with struct

    # Bytes 0-5: 48-bit unix timestamp ms (big-endian)
    # Byte 6: version (0111) + top 4 bits of rand
    # Byte 7: next 8 bits of rand
    # Byte 8: variant (10) + 6 bits of rand
    # Bytes 9-15: 48 bits of rand
    b = struct.pack(">Q", timestamp_ms)[2:]  # 6 bytes of timestamp
    b += bytes([(0x70 | (rand_bytes[0] & 0x0F)), rand_bytes[1]])  # ver + rand_a
    b += bytes([0x80 | (rand_bytes[2] & 0x3F)]) + rand_bytes[3:10]  # variant + rand_b
    return b


# ---- SQLAlchemy models for per-user core.db ----


class UserDevice(Base):
    __tablename__ = "user_device"

    id = Column(LargeBinary, primary_key=True)
    key = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<UserDevice(id='{self.id.hex()}')>"


class Nickname(Base):
    __tablename__ = "nickname"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<Nickname(id='{self.id.hex()}')>"


class Team(Base):
    __tablename__ = "team"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)
    self_in_team = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<Team(id='{self.id.hex()}')>"


class App(Base):
    __tablename__ = "app"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<App(id='{self.id.hex()}')>"


class TeamAppBerth(Base):
    __tablename__ = "team_app_berth"

    id = Column(LargeBinary, primary_key=True)
    team_id = Column(LargeBinary, nullable=False)
    app_id = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<TeamAppBerth(id='{self.id.hex()}')>"


class NotificationService(Base):
    __tablename__ = "notification_service"

    id = Column(LargeBinary, primary_key=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    access_key = Column(String, nullable=True)   # Gotify app token; ntfy auth token
    access_token = Column(String, nullable=True)  # Gotify client token

    def __repr__(self):
        return f"<NotificationService(id='{self.id.hex()}')>"


# ---- SQLAlchemy models for per-team core.db ----


class Invitation(Base):
    __tablename__ = "invitation"

    id = Column(LargeBinary, primary_key=True)
    nonce = Column(LargeBinary, nullable=False)
    status = Column(String, nullable=False, default="pending")
    invitee_label = Column(String)
    role = Column(String, nullable=False, default="admin")
    created_at = Column(String, nullable=False)
    accepted_at = Column(String)
    accepted_by = Column(LargeBinary)
    acceptor_protocol = Column(String)
    acceptor_url = Column(String)

    def __repr__(self):
        return f"<Invitation(id='{self.id.hex()}', status='{self.status}')>"


class Peer(Base):
    __tablename__ = "peer"

    id = Column(LargeBinary, primary_key=True)
    member_id = Column(LargeBinary, nullable=False)
    display_name = Column(String, nullable=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    bucket = Column(String, nullable=True)

    def __repr__(self):
        return f"<Peer(id='{self.id.hex()}')>"


# ---- Constants ----

USER_SCHEMA_VERSION = 50


# ---- Provisioning functions ----


def create_new_participant(root_dir, nickname, device=None):
    """Create a new participant: directory layout, user DB, git repo."""
    root_dir = pathlib.Path(root_dir)
    ident = uuid7()
    ident_dir = root_dir / "Participants" / ident.hex()

    device_key = Ed25519PrivateKey.generate()
    device_public_key = device_key.public_key()
    device_key_bytes = device_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    device_public_key_bytes = device_public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )

    try:
        os.makedirs(ident_dir / "NoteToSelf" / "Sync", exist_ok=False)
        os.makedirs(ident_dir / "FakeEnclave", exist_ok=False)
    except Exception as exn:
        print(f"makedirs failed :( {ident_dir}")

    if device is None:
        device = "42"

    _initialize_user_db(root_dir, ident, nickname, device)
    return ident.hex()


def _initialize_user_db(root_dir, ident, nickname, device):
    path = root_dir / "Participants" / ident.hex() / "NoteToSelf" / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            _initialize_core_note_to_self_schema(conn)

        with Session(engine) as session:
            nick1 = Nickname(id=uuid7(), name=nickname)
            team1 = Team(id=uuid7(), name="NoteToSelf", self_in_team=b"0")
            app1 = App(id=uuid7(), name="SmallSeaCollectiveCore")
            session.add_all([nick1, team1, app1])
            session.flush()
            team_app = TeamAppBerth(id=uuid7(), team_id=team1.id, app_id=app1.id)
            session.add_all([team_app])
            session.commit()

    except sqlite3.Error as e:
        print("SQLite error occurred:", e)

    repo_dir = root_dir / "Participants" / ident.hex() / "NoteToSelf" / "Sync"
    CodSync.gitCmd(["init", "-b", "main", str(repo_dir)])
    CodSync.gitCmd(["-C", str(repo_dir), "add", "core.db"])
    CodSync.gitCmd(
        ["-C", str(repo_dir), "commit", "-m", f"Welcome to Small Sea Collective"]
    )


def _migrate_user_db(conn, from_version):
    """Apply incremental migrations to bring a user DB up to USER_SCHEMA_VERSION."""
    if from_version < 44:
        for col in [
            "client_id",
            "client_secret",
            "refresh_token",
            "access_token",
            "token_expiry",
            "path_metadata",
        ]:
            conn.execute(text(f"ALTER TABLE cloud_storage ADD COLUMN {col} TEXT"))
    if from_version < 45:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS notification_service ("
                "id BLOB PRIMARY KEY, "
                "protocol TEXT NOT NULL, "
                "url TEXT NOT NULL)"
            )
        )
    if from_version < 46:
        pass  # team DB schema updated (app, team_app_berth, berth_role); NoteToSelf schema unchanged
    if from_version < 47:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS team_signing_key ("
                "id BLOB PRIMARY KEY, "
                "team_id BLOB NOT NULL, "
                "public_key BLOB NOT NULL, "
                "private_key BLOB NOT NULL, "
                "created_at TEXT NOT NULL, "
                "FOREIGN KEY (team_id) REFERENCES team(id))"
            )
        )
    if from_version < 48:
        conn.execute(text("ALTER TABLE notification_service ADD COLUMN access_key TEXT"))
        conn.execute(text("ALTER TABLE notification_service ADD COLUMN access_token TEXT"))
    if from_version < 49:
        pass  # peer.bucket added to team DB schema; NoteToSelf schema unchanged
    if from_version < 50:
        pass  # peer.display_name added to team DB schema; NoteToSelf schema unchanged


def _migrate_team_db(conn, from_version):
    """Apply incremental migrations to bring a team DB up to USER_SCHEMA_VERSION."""
    if from_version < 49:
        conn.execute(text("ALTER TABLE peer ADD COLUMN bucket TEXT"))
    if from_version < 50:
        conn.execute(text("ALTER TABLE peer ADD COLUMN display_name TEXT"))


def ensure_team_db_schema(db_path):
    """Upgrade an existing team DB in place if needed."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            user_version = conn.execute(text("PRAGMA user_version")).scalar()
            if user_version == USER_SCHEMA_VERSION:
                return
            if (0 != user_version) and (user_version < USER_SCHEMA_VERSION):
                _migrate_team_db(conn, user_version)
                conn.execute(text(f"PRAGMA user_version = {USER_SCHEMA_VERSION}"))
                return
            if user_version > USER_SCHEMA_VERSION:
                raise NotImplementedError("TODO: DB FROM THE FUTURE!")
    finally:
        engine.dispose()


def migrate_participant_team_dbs(root_dir, participant_hex):
    """Ensure all existing team DBs for a participant are on the current schema."""
    root_dir = pathlib.Path(root_dir)
    for team in list_teams(root_dir, participant_hex):
        team_name = team["name"]
        if team_name == "NoteToSelf":
            continue
        team_db_path = (
            root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
        )
        if team_db_path.exists():
            ensure_team_db_schema(team_db_path)


def _initialize_core_note_to_self_schema(conn):
    result = conn.execute(text("PRAGMA user_version"))
    user_version = result.scalar()

    if user_version == USER_SCHEMA_VERSION:
        print("SmallSea local DB already initialized")
        return

    if (0 != user_version) and (user_version < USER_SCHEMA_VERSION):
        _migrate_user_db(conn, user_version)
        conn.execute(text(f"PRAGMA user_version = {USER_SCHEMA_VERSION}"))
        print(f"User DB migrated from v{user_version} to v{USER_SCHEMA_VERSION}.")
        return

    if user_version > USER_SCHEMA_VERSION:
        print("TODO: DB FROM THE FUTURE!")
        raise NotImplementedError()

    schema_path = pathlib.Path(__file__).parent / "sql" / "core_note_to_self_schema.sql"

    with open(schema_path, "r") as f:
        schema_script = f.read()

    for statement in schema_script.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(text(statement))

    conn.execute(text(f"PRAGMA user_version = {USER_SCHEMA_VERSION}"))
    print("User DB schema initialized successfully.")


def make_device_link_invitation(session):
    # make keypair
    pass


def _init_team_db(db_path):
    """Initialize a team core.db with the team schema. Returns the engine."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        schema_path = pathlib.Path(__file__).parent / "sql" / "core_other_team.sql"
        with open(schema_path, "r") as f:
            schema_script = f.read()
        for statement in schema_script.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
        conn.execute(text(f"PRAGMA user_version = {USER_SCHEMA_VERSION}"))
    return engine


def _install_sqlite_merge_driver(team_sync_dir):
    """Install the harmonic-sqlite-merge git merge driver for core.db.

    Writes .gitattributes (tracked) and configures the merge driver
    command in .git/config (local only).
    """
    team_sync_dir = pathlib.Path(team_sync_dir)

    # .gitattributes — tracked by git, cloned automatically
    gitattributes = team_sync_dir / ".gitattributes"
    gitattributes.write_text("core.db merge=harmonic-sqlite\n")

    # Find the harmonic-sqlite-merge executable
    merge_bin = shutil.which("harmonic-sqlite-merge")
    if merge_bin is None:
        # Fallback: try to find it via the Python that's running us
        merge_bin = "harmonic-sqlite-merge"

    driver_cmd = f"{merge_bin} %O %A %B %L %P"
    CodSync.gitCmd(
        [
            "-C",
            str(team_sync_dir),
            "config",
            "merge.harmonic-sqlite.driver",
            driver_cmd,
        ]
    )


def _generate_team_signing_key(nts_engine, team_id):
    """Generate an Ed25519 signing key pair for a team.

    Stores the key in the team_signing_key table in NoteToSelf.
    Returns (private_key_bytes, public_key_bytes).
    """
    private_key = Ed25519PrivateKey.generate()
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    now = datetime.now(timezone.utc).isoformat()
    with nts_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO team_signing_key (id, team_id, public_key, private_key, created_at) "
                "VALUES (:id, :team_id, :pub, :priv, :created_at)"
            ),
            {"id": uuid7(), "team_id": team_id, "pub": public_key_bytes,
             "priv": private_key_bytes, "created_at": now},
        )
    return private_key_bytes, public_key_bytes


def get_team_signing_key(root_dir, participant_hex, team_name):
    """Return the signing key for a team as (private_key_bytes, public_key_bytes).

    Looks up the team by name in NoteToSelf, then fetches the key from team_signing_key.
    Raises ValueError if no key is found.
    """
    root_dir = pathlib.Path(root_dir)
    nts_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{nts_db_path}")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT tsk.private_key, tsk.public_key FROM team_signing_key tsk "
                "JOIN team t ON tsk.team_id = t.id "
                "WHERE t.name = :team_name LIMIT 1"
            ),
            {"team_name": team_name},
        ).fetchone()
    engine.dispose()
    if row is None:
        raise ValueError(f"No signing key found for team '{team_name}'")
    return row[0], row[1]


def create_team(root_dir, participant_hex, team_name):
    """Create a new team for an existing participant.

    Adds team + team_app_berth rows to the user's NoteToSelf/Sync/core.db,
    creates the team directory with its own core.db (member table),
    and initializes a git repo for the team sync directory.

    Returns {"team_id_hex": ..., "member_id_hex": ...}.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    team_id = uuid7()
    member_id = uuid7()

    # --- Update the user's NoteToSelf core.db ---
    # Only a lightweight membership pointer goes here; structural team data
    # (App, TeamAppBerth, BerthRole) lives in the team's own DB.
    user_db_path = participant_dir / "NoteToSelf" / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{user_db_path}")

    with Session(engine) as session:
        team_row = Team(id=team_id, name=team_name, self_in_team=member_id)
        session.add(team_row)
        session.commit()

    # --- Generate team signing key (stored in NoteToSelf) ---
    _priv, pub = _generate_team_signing_key(engine, team_id)

    # --- Create team directory and its core.db ---
    team_sync_dir = participant_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    team_db_path = team_sync_dir / "core.db"
    team_engine = _init_team_db(team_db_path)

    # Populate the team DB: creator member, app, berth, and creator's role.
    app_id = uuid7()
    berth_id = uuid7()
    with team_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO member (id, public_key) VALUES (:id, :pub)"),
            {"id": member_id, "pub": pub},
        )
        conn.execute(
            text("INSERT INTO app (id, name) VALUES (:id, :name)"),
            {"id": app_id, "name": "SmallSeaCollectiveCore"},
        )
        conn.execute(
            text("INSERT INTO team_app_berth (id, app_id) VALUES (:id, :app_id)"),
            {"id": berth_id, "app_id": app_id},
        )
        conn.execute(
            text(
                "INSERT INTO berth_role (id, member_id, berth_id, role) "
                "VALUES (:id, :mid, :bid, :role)"
            ),
            {"id": uuid7(), "mid": member_id, "bid": berth_id, "role": "read-write"},
        )

    # --- Git init ---
    CodSync.gitCmd(["init", "-b", "main", str(team_sync_dir)])
    _install_sqlite_merge_driver(team_sync_dir)
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db", ".gitattributes"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"New team: {team_name}"])

    return {
        "team_id_hex": team_id.hex(),
        "member_id_hex": member_id.hex(),
        "berth_id_hex": berth_id.hex(),
    }


def create_invitation(
    root_dir, participant_hex, team_name, inviter_cloud, invitee_label=None, role="admin"
):
    """Create an invitation token for a team.

    inviter_cloud: dict with keys protocol and url (endpoint only — no credentials).
    Returns a base64-encoded JSON token string.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    # Look up the inviter's member ID from the team DB
    team_db_path = participant_dir / team_name / "Sync" / "core.db"
    team_engine = create_engine(f"sqlite:///{team_db_path}")

    with team_engine.begin() as conn:
        row = conn.execute(text("SELECT id FROM member LIMIT 1")).fetchone()
        inviter_member_id = row[0]
    inviter_display_name = get_nickname(root_dir, participant_hex) or None

    # Look up the berth ID from the team DB (to derive the bucket name).
    # Berth structural data lives in the team DB, not NoteToSelf.
    with team_engine.begin() as conn:
        berth_row = conn.execute(
            text("SELECT id FROM team_app_berth LIMIT 1")
        ).fetchone()
    if berth_row is None:
        raise ValueError(f"No berth found in team DB for '{team_name}'")
    berth_id_hex = berth_row[0].hex()
    if inviter_cloud["protocol"] == "dropbox":
        inviter_bucket = f"ss-{inviter_member_id.hex()[:16]}"
    else:
        inviter_bucket = f"ss-{berth_id_hex[:16]}"

    # Create invitation row
    inv_id = uuid7()
    nonce = secrets.token_bytes(16)
    now = datetime.now(timezone.utc).isoformat()

    with team_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO invitation (id, nonce, status, invitee_label, role, created_at) "
                "VALUES (:id, :nonce, 'pending', :label, :role, :created_at)"
            ),
            {"id": inv_id, "nonce": nonce, "label": invitee_label, "role": role, "created_at": now},
        )

    # Build token — credentials are never included; bucket is publicly readable.
    token_data = {
        "invitation_id": inv_id.hex(),
        "nonce": nonce.hex(),
        "team_name": team_name,
        "inviter_member_id": inviter_member_id.hex(),
        "inviter_display_name": inviter_display_name,
        "inviter_cloud": {"protocol": inviter_cloud["protocol"], "url": inviter_cloud["url"]},
        "inviter_bucket": inviter_bucket,
    }
    token_json = json.dumps(token_data)
    token_b64 = base64.b64encode(token_json.encode()).decode()

    # Git commit the updated DB
    team_sync_dir = participant_dir / team_name / "Sync"
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"Created invitation"])

    return token_b64


def accept_invitation(
    root_dir,
    acceptor_participant_hex,
    token_b64,
    inviter_remote,
    acceptor_remote=None,
    acceptor_member_id=None,
):
    """Accept a team invitation token (acceptor side).

    Clones the team repo from the inviter's cloud, adds self as member,
    and returns an acceptance response for the inviter. The caller is
    responsible for pushing to the acceptor's own cloud after this returns
    (typically via a Hub team session).

    inviter_remote: CodSyncRemote for reading the inviter's (public) bucket.
    acceptor_remote: ignored (deprecated; push is now the caller's responsibility).
    acceptor_member_id: pre-generated member ID bytes (optional). When None a
        new UUID is generated. Pass a pre-generated ID when the acceptor's
        bucket must be derived before this call (e.g. Dropbox folder-prefix).
    Returns a base64-encoded acceptance response JSON string.
    """
    root_dir = pathlib.Path(root_dir)

    # Decode token
    token_json = base64.b64decode(token_b64).decode()
    token = json.loads(token_json)
    team_name = token["team_name"]
    inviter_member_id = bytes.fromhex(token["inviter_member_id"])
    inviter_cloud = token["inviter_cloud"]  # protocol + url only, no credentials
    inviter_bucket = token["inviter_bucket"]
    inviter_display_name = token.get("inviter_display_name") or None
    invitation_id = bytes.fromhex(token["invitation_id"])
    nonce = bytes.fromhex(token["nonce"])

    # Read acceptor's own cloud config (URL only; credentials stay in Hub)
    acceptor_cloud_full = get_cloud_storage(root_dir, acceptor_participant_hex)
    acceptor_cloud = {"protocol": acceptor_cloud_full["protocol"], "url": acceptor_cloud_full["url"]}

    # Use pre-generated member ID if provided (required when acceptor_remote must
    # be constructed before this call, e.g. Dropbox folder-prefix naming).
    if acceptor_member_id is None:
        acceptor_member_id = uuid7()

    acceptor_dir = root_dir / "Participants" / acceptor_participant_hex

    # --- Create acceptor's team directory ---
    team_sync_dir = acceptor_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    # --- Clone the team repo from inviter's cloud ---
    # Use git init + fetch_from_remote + checkout rather than clone_from_remote,
    # so this works when the workspace lives inside an existing git repo.

    CodSync.gitCmd(["init", "-b", "main", str(team_sync_dir)])

    saved_cwd = os.getcwd()
    os.chdir(team_sync_dir)
    try:
        cod = CodSync.CodSync("inviter")
        cod.remote = inviter_remote
        result = cod.fetch_from_remote(["main"])
        if result is None:
            inviter_url = (
                f"{inviter_cloud['protocol']}://{inviter_cloud['url']}/{inviter_bucket}"
            )
            raise RuntimeError(
                f"Failed to fetch team repo from inviter's cloud (code {result}; {inviter_url})"
            )
        CodSync.gitCmd(["checkout", "main"])
    finally:
        os.chdir(saved_cwd)

    # --- Add acceptor as member in the cloned DB ---
    team_db_path = team_sync_dir / "core.db"
    ensure_team_db_schema(team_db_path)
    team_engine = create_engine(f"sqlite:///{team_db_path}")

    with team_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO member (id) VALUES (:id)"), {"id": acceptor_member_id}
        )
        # Store inviter's cloud location as a peer (URL only, no credentials)
        conn.execute(
            text(
                "INSERT INTO peer (id, member_id, display_name, protocol, url, bucket) "
                "VALUES (:id, :member_id, :display_name, :protocol, :url, :bucket)"
            ),
            {
                "id": uuid7(),
                "member_id": inviter_member_id,
                "display_name": inviter_display_name,
                "protocol": inviter_cloud["protocol"],
                "url": inviter_cloud["url"],
                "bucket": inviter_bucket,
            },
        )

    team_engine.dispose()

    # --- Install sqlite merge driver ---
    _install_sqlite_merge_driver(team_sync_dir)

    # --- Add team membership pointer to acceptor's NoteToSelf ---
    # Only a lightweight Team reference goes in NoteToSelf; structural data
    # (App, TeamAppBerth, BerthRole) lives in the team DB, which was cloned above.
    user_db_path = acceptor_dir / "NoteToSelf" / "Sync" / "core.db"
    user_engine = create_engine(f"sqlite:///{user_db_path}")

    with Session(user_engine) as session:
        team_id = uuid7()
        team_row = Team(id=team_id, name=team_name, self_in_team=acceptor_member_id)
        session.add(team_row)
        session.commit()

    # --- Generate team signing key (stored in NoteToSelf) ---
    _priv, acceptor_public_key = _generate_team_signing_key(user_engine, team_id)

    # --- Store public key in team DB member row ---
    team_db_path = team_sync_dir / "core.db"
    team_engine2 = create_engine(f"sqlite:///{team_db_path}")
    with team_engine2.begin() as conn:
        conn.execute(
            text("UPDATE member SET public_key = :pub WHERE id = :id"),
            {"pub": acceptor_public_key, "id": acceptor_member_id},
        )
    team_engine2.dispose()

    # --- Git commit the DB changes ---
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db", ".gitattributes"])
    CodSync.gitCmd(
        ["-C", str(team_sync_dir), "commit", "-m", f"Joined team: {team_name}"]
    )

    # Derive acceptor's bucket name (protocol-aware to avoid folder collisions)
    team_db_path = team_sync_dir / "core.db"
    team_engine = create_engine(f"sqlite:///{team_db_path}")
    if acceptor_cloud["protocol"] == "dropbox":
        acceptor_bucket = f"ss-{acceptor_member_id.hex()[:16]}"
    else:
        with team_engine.begin() as conn:
            berth_row = conn.execute(
                text("SELECT id FROM team_app_berth LIMIT 1")
            ).fetchone()
        acceptor_bucket = f"ss-{berth_row[0].hex()[:16]}"

    # --- Build and return acceptance response (no credentials) ---
    acceptance_data = {
        "invitation_id": invitation_id.hex(),
        "nonce": nonce.hex(),
        "acceptor_member_id": acceptor_member_id.hex(),
        "acceptor_public_key": acceptor_public_key.hex(),
        "acceptor_cloud": acceptor_cloud,
        "acceptor_bucket": acceptor_bucket,
    }
    acceptance_json = json.dumps(acceptance_data)
    acceptance_b64 = base64.b64encode(acceptance_json.encode()).decode()

    return acceptance_b64


def complete_invitation_acceptance(
    root_dir, participant_hex, team_name, acceptance_b64
):
    """Complete an invitation acceptance (inviter side).

    Decodes the acceptance response, validates it against the invitation row,
    and adds the acceptor as a member + peer in the inviter's team DB.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    # Decode acceptance response
    acceptance_json = base64.b64decode(acceptance_b64).decode()
    acceptance = json.loads(acceptance_json)
    invitation_id = bytes.fromhex(acceptance["invitation_id"])
    nonce = bytes.fromhex(acceptance["nonce"])
    acceptor_member_id = bytes.fromhex(acceptance["acceptor_member_id"])
    acceptor_public_key = bytes.fromhex(acceptance["acceptor_public_key"])
    acceptor_cloud = acceptance["acceptor_cloud"]
    acceptor_bucket = acceptance["acceptor_bucket"]

    # Find and validate the invitation in the inviter's team DB
    team_db_path = participant_dir / team_name / "Sync" / "core.db"
    ensure_team_db_schema(team_db_path)
    engine = create_engine(f"sqlite:///{team_db_path}")

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT nonce, status, invitee_label FROM invitation WHERE id = :id"),
            {"id": invitation_id},
        ).fetchone()

        if row is None:
            engine.dispose()
            raise ValueError("Invitation not found")

        if row[1] != "pending":
            engine.dispose()
            raise ValueError(f"Invitation is not pending (status: {row[1]})")
        if row[0] != nonce:
            engine.dispose()
            raise ValueError("Nonce mismatch")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            text(
                "UPDATE invitation SET status='accepted', accepted_at=:now, "
                "accepted_by=:member_id, acceptor_protocol=:protocol, "
                "acceptor_url=:url "
                "WHERE id = :id"
            ),
            {
                "id": invitation_id,
                "now": now,
                "member_id": acceptor_member_id,
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
            },
        )

        # Add acceptor as member + peer in inviter's team DB (URL only, no credentials)
        conn.execute(
            text("INSERT INTO member (id, public_key) VALUES (:id, :pub)"),
            {"id": acceptor_member_id, "pub": acceptor_public_key},
        )
        conn.execute(
            text(
                "INSERT INTO peer (id, member_id, display_name, protocol, url, bucket) "
                "VALUES (:id, :member_id, :display_name, :protocol, :url, :bucket)"
            ),
            {
                "id": uuid7(),
                "member_id": acceptor_member_id,
                "display_name": row[2],
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
                "bucket": acceptor_bucket,
            },
        )

        # Grant the acceptor read-write on all berths (default).
        # The inviter (admin) can change this later.
        berth_row = conn.execute(
            text("SELECT id FROM team_app_berth LIMIT 1")
        ).fetchone()
        if berth_row is not None:
            conn.execute(
                text(
                    "INSERT INTO berth_role (id, member_id, berth_id, role) "
                    "VALUES (:id, :mid, :bid, :role)"
                ),
                {
                    "id": uuid7(),
                    "mid": acceptor_member_id,
                    "bid": berth_row[0],
                    "role": "read-write",
                },
            )

    # Dispose engine to release file locks before git operations
    engine.dispose()

    team_sync_dir = participant_dir / team_name / "Sync"
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"Accepted invitation"])


def add_notification_service(
    root_dir, participant_hex, protocol, url,
    access_key=None, access_token=None,
):
    """Register a notification service in a participant's NoteToSelf DB.

    protocol: "ntfy" or "gotify"
      ntfy:   url = ntfy server base URL; access_key = auth token if server requires it
      gotify: url = Gotify server base URL; access_key = app token (publish);
              access_token = client token (poll/subscribe)

    Returns the notification service ID hex.
    """
    known = {"ntfy", "gotify"}
    if protocol not in known:
        raise ValueError(f"Unknown notification protocol: {protocol}")

    root_dir = pathlib.Path(root_dir)
    user_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{user_db_path}")
    ns_id = uuid7()
    with Session(engine) as session:
        ns = NotificationService(
            id=ns_id,
            protocol=protocol,
            url=url,
            access_key=access_key,
            access_token=access_token,
        )
        session.add(ns)
        session.commit()
    return ns_id.hex()


def set_notification_service(
    root_dir, participant_hex, protocol, url,
    access_key=None, access_token=None,
):
    """Upsert a notification service in a participant's NoteToSelf DB.

    Replaces any existing row with the same protocol before inserting, so this
    is safe to call multiple times (e.g. to update the URL).

    Returns the new notification service ID hex.
    """
    known = {"ntfy", "gotify"}
    if protocol not in known:
        raise ValueError(f"Unknown notification protocol: {protocol}")

    root_dir = pathlib.Path(root_dir)
    user_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{user_db_path}")
    ns_id = uuid7()
    with Session(engine) as session:
        session.query(NotificationService).filter_by(protocol=protocol).delete()
        ns = NotificationService(
            id=ns_id,
            protocol=protocol,
            url=url,
            access_key=access_key,
            access_token=access_token,
        )
        session.add(ns)
        session.commit()
    return ns_id.hex()


def get_cloud_storage(root_dir, participant_hex):
    """Return the first cloud storage config from NoteToSelf DB as a dict.

    Raises ValueError if no cloud storage is configured.
    """
    root_dir = pathlib.Path(root_dir)
    nts_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{nts_db_path}")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT protocol, url, access_key, secret_key FROM cloud_storage LIMIT 1")
        ).fetchone()
    engine.dispose()
    if row is None:
        raise ValueError("No cloud storage configured for this participant")
    return {"protocol": row[0], "url": row[1], "access_key": row[2], "secret_key": row[3]}


def add_cloud_storage(
    root_dir,
    participant_hex,
    protocol,
    url,
    access_key=None,
    secret_key=None,
    client_id=None,
    client_secret=None,
    refresh_token=None,
    access_token=None,
    token_expiry=None,
):
    """Add a cloud storage configuration to a participant's NoteToSelf DB."""
    root_dir = pathlib.Path(root_dir)
    nts_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{nts_db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cloud_storage "
                "(id, protocol, url, access_key, secret_key, "
                " client_id, client_secret, refresh_token, access_token, token_expiry) "
                "VALUES "
                "(:id, :protocol, :url, :access_key, :secret_key, "
                " :client_id, :client_secret, :refresh_token, :access_token, :token_expiry)"
            ),
            {
                "id": uuid7(), "protocol": protocol, "url": url,
                "access_key": access_key, "secret_key": secret_key,
                "client_id": client_id, "client_secret": client_secret,
                "refresh_token": refresh_token, "access_token": access_token,
                "token_expiry": token_expiry,
            },
        )
    engine.dispose()


def list_cloud_storage(root_dir, participant_hex):
    """Return all cloud storage configs as a list of dicts (credentials masked)."""
    root_dir = pathlib.Path(root_dir)
    nts_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{nts_db_path}")
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, protocol, url, access_key, client_id FROM cloud_storage ORDER BY rowid")
        ).fetchall()
    engine.dispose()
    result = []
    for row in rows:
        storage_id = row[0].hex() if isinstance(row[0], bytes) else row[0]
        result.append({
            "id": storage_id,
            "protocol": row[1],
            "url": row[2],
            "access_key": row[3],
            "client_id": row[4],
        })
    return result


def remove_cloud_storage(root_dir, participant_hex, storage_id_hex):
    """Remove a cloud storage config by its hex ID."""
    root_dir = pathlib.Path(root_dir)
    nts_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    storage_id = bytes.fromhex(storage_id_hex)
    engine = create_engine(f"sqlite:///{nts_db_path}")
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM cloud_storage WHERE id = :id"),
            {"id": storage_id},
        )
    engine.dispose()


def revoke_invitation(root_dir, participant_hex, team_name, invitation_id_hex):
    """Set an invitation's status to 'revoked'. Raises ValueError if not pending."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (
        root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    invitation_id = bytes.fromhex(invitation_id_hex)
    engine = create_engine(f"sqlite:///{team_db_path}")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM invitation WHERE id = :id"), {"id": invitation_id}
        ).fetchone()
        if row is None:
            raise ValueError("Invitation not found")
        if row[0] != "pending":
            raise ValueError(f"Invitation is not pending (status: {row[0]})")
        conn.execute(
            text("UPDATE invitation SET status = 'revoked' WHERE id = :id"),
            {"id": invitation_id},
        )
    engine.dispose()
    team_sync_dir = root_dir / "Participants" / participant_hex / team_name / "Sync"
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", "Revoked invitation"])


def get_nickname(root_dir, participant_hex):
    """Return the participant's first nickname, or empty string if none."""
    root_dir = pathlib.Path(root_dir)
    nts_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{nts_db_path}")
    with engine.begin() as conn:
        row = conn.execute(text("SELECT name FROM nickname LIMIT 1")).fetchone()
    engine.dispose()
    return row[0] if row else ""


def list_teams(root_dir, participant_hex):
    """List teams from NoteToSelf DB. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    nts_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{nts_db_path}")

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, name, self_in_team FROM team")
        ).fetchall()

    engine.dispose()
    return [
        {"id": row[0].hex(), "name": row[1], "self_in_team": row[2].hex()}
        for row in rows
    ]


def list_members(root_dir, participant_hex, team_name):
    """List members of a team with their berth roles. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (
        root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{team_db_path}")

    with engine.begin() as conn:
        members = conn.execute(text("SELECT id FROM member")).fetchall()
        role_rows = conn.execute(
            text("SELECT member_id, berth_id, role FROM berth_role")
        ).fetchall()

    engine.dispose()

    roles_by_member = {}
    for r in role_rows:
        key = r[0].hex()
        roles_by_member.setdefault(key, []).append(
            {"berth_id": r[1].hex(), "role": r[2]}
        )

    return [
        {"id": row[0].hex(), "berth_roles": roles_by_member.get(row[0].hex(), [])}
        for row in members
    ]


def list_invitations(root_dir, participant_hex, team_name):
    """List invitations for a team. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (
        root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{team_db_path}")

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, status, invitee_label, role, created_at FROM invitation")
        ).fetchall()

    return [
        {
            "id": row[0].hex(),
            "status": row[1],
            "invitee_label": row[2],
            "role": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]
