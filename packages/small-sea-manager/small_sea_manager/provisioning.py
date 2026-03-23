# Participant/user/team/app provisioning — stashed here from the Hub backend.
#
# This code used to live in small_sea_hub.backend.  It handles creating
# participants, initializing per-user databases, and managing teams/apps.
#
# It's not properly wired up yet — the TeamManager stubs in manager.py
# will eventually call into this, or this will be restructured to work
# through the Hub's HTTP API.
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


class TeamAppStation(Base):
    __tablename__ = "team_app_station"

    id = Column(LargeBinary, primary_key=True)
    team_id = Column(LargeBinary, nullable=False)
    app_id = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<TeamAppStation(id='{self.id.hex()}')>"


class NotificationService(Base):
    __tablename__ = "notification_service"

    id = Column(LargeBinary, primary_key=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)

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
    acceptor_access_key = Column(String)
    acceptor_secret_key = Column(String)

    def __repr__(self):
        return f"<Invitation(id='{self.id.hex()}', status='{self.status}')>"


class Peer(Base):
    __tablename__ = "peer"

    id = Column(LargeBinary, primary_key=True)
    member_id = Column(LargeBinary, nullable=False)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    access_key = Column(String)
    secret_key = Column(String)

    def __repr__(self):
        return f"<Peer(id='{self.id.hex()}')>"


# ---- Constants ----

USER_SCHEMA_VERSION = 46


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
            team_app = TeamAppStation(id=uuid7(), team_id=team1.id, app_id=app1.id)
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
        pass  # team DB schema updated (app, team_app_station, station_role); NoteToSelf schema unchanged


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


def create_team(root_dir, participant_hex, team_name):
    """Create a new team for an existing participant.

    Adds team + team_app_station rows to the user's NoteToSelf/Sync/core.db,
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
    # (App, TeamAppStation, StationRole) lives in the team's own DB.
    user_db_path = participant_dir / "NoteToSelf" / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{user_db_path}")

    with Session(engine) as session:
        team_row = Team(id=team_id, name=team_name, self_in_team=member_id)
        session.add(team_row)
        session.commit()

    # --- Create team directory and its core.db ---
    team_sync_dir = participant_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    team_db_path = team_sync_dir / "core.db"
    team_engine = _init_team_db(team_db_path)

    # Populate the team DB: creator member, app, station, and creator's role.
    app_id = uuid7()
    station_id = uuid7()
    with team_engine.begin() as conn:
        conn.execute(text("INSERT INTO member (id) VALUES (:id)"), {"id": member_id})
        conn.execute(
            text("INSERT INTO app (id, name) VALUES (:id, :name)"),
            {"id": app_id, "name": "SmallSeaCollectiveCore"},
        )
        conn.execute(
            text("INSERT INTO team_app_station (id, app_id) VALUES (:id, :app_id)"),
            {"id": station_id, "app_id": app_id},
        )
        conn.execute(
            text(
                "INSERT INTO station_role (id, member_id, station_id, role) "
                "VALUES (:id, :mid, :sid, :role)"
            ),
            {"id": uuid7(), "mid": member_id, "sid": station_id, "role": "read-write"},
        )

    # --- Git init ---
    CodSync.gitCmd(["init", "-b", "main", str(team_sync_dir)])
    _install_sqlite_merge_driver(team_sync_dir)
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db", ".gitattributes"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"New team: {team_name}"])

    return {
        "team_id_hex": team_id.hex(),
        "member_id_hex": member_id.hex(),
        "station_id_hex": station_id.hex(),
    }


def create_invitation(
    root_dir, participant_hex, team_name, inviter_cloud, invitee_label=None, role="admin"
):
    """Create an invitation token for a team.

    inviter_cloud: dict with keys protocol, url, access_key, secret_key.
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

    # Look up the station ID from the team DB (to derive the bucket name).
    # Station structural data lives in the team DB, not NoteToSelf.
    with team_engine.begin() as conn:
        station_row = conn.execute(
            text("SELECT id FROM team_app_station LIMIT 1")
        ).fetchone()
    if station_row is None:
        raise ValueError(f"No station found in team DB for '{team_name}'")
    station_id_hex = station_row[0].hex()
    inviter_bucket = f"ss-{station_id_hex[:16]}"

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

    # Build token
    token_data = {
        "invitation_id": inv_id.hex(),
        "nonce": nonce.hex(),
        "team_name": team_name,
        "inviter_member_id": inviter_member_id.hex(),
        "inviter_cloud": inviter_cloud,
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
    acceptor_cloud,
    acceptor_bucket,
    inviter_remote=None,
    acceptor_remote=None,
):
    """Accept a team invitation token (acceptor side).

    Clones the team repo from the inviter's cloud, adds self as member,
    pushes to own cloud, and returns an acceptance response for the inviter.

    acceptor_cloud: dict with keys protocol, url, access_key, secret_key.
    acceptor_bucket: S3 bucket name for the acceptor's cloud.
    inviter_remote: CodSyncRemote for reading the inviter's cloud.
    acceptor_remote: CodSyncRemote for writing to the acceptor's cloud.
    Returns a base64-encoded acceptance response JSON string.
    """
    root_dir = pathlib.Path(root_dir)

    # Decode token
    token_json = base64.b64decode(token_b64).decode()
    token = json.loads(token_json)
    team_name = token["team_name"]
    inviter_member_id = bytes.fromhex(token["inviter_member_id"])
    inviter_cloud = token["inviter_cloud"]
    inviter_bucket = token["inviter_bucket"]
    invitation_id = bytes.fromhex(token["invitation_id"])
    nonce = bytes.fromhex(token["nonce"])

    # Generate acceptor's fresh team-local member ID
    acceptor_member_id = uuid7()

    acceptor_dir = root_dir / "Participants" / acceptor_participant_hex

    # --- Create acceptor's team directory ---
    team_sync_dir = acceptor_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    # --- Clone the team repo from inviter's cloud ---
    if inviter_remote is None:
        raise ValueError("inviter_remote is required")

    saved_cwd = os.getcwd()
    os.chdir(team_sync_dir)
    try:
        cod = CodSync.CodSync("inviter")
        cod.gitCmd = CodSync.gitCmd

        # Build a URL for git remote registration (used by add_remote inside clone_from_remote)
        inviter_url = (
            f"{inviter_cloud['protocol']}://{inviter_cloud['url']}/{inviter_bucket}"
        )
        result = cod.clone_from_remote(inviter_url, remote=inviter_remote)
        if result != 0:
            raise RuntimeError(
                f"Failed to clone team repo from inviter's cloud (code {result})"
            )
    finally:
        os.chdir(saved_cwd)

    # --- Add acceptor as member in the cloned DB ---
    team_db_path = team_sync_dir / "core.db"
    team_engine = create_engine(f"sqlite:///{team_db_path}")

    with team_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO member (id) VALUES (:id)"), {"id": acceptor_member_id}
        )
        # Store inviter's cloud info as a peer
        conn.execute(
            text(
                "INSERT INTO peer (id, member_id, protocol, url, access_key, secret_key) "
                "VALUES (:id, :member_id, :protocol, :url, :access_key, :secret_key)"
            ),
            {
                "id": uuid7(),
                "member_id": inviter_member_id,
                "protocol": inviter_cloud["protocol"],
                "url": inviter_cloud["url"],
                "access_key": inviter_cloud.get("access_key"),
                "secret_key": inviter_cloud.get("secret_key"),
            },
        )

    team_engine.dispose()

    # --- Install sqlite merge driver ---
    _install_sqlite_merge_driver(team_sync_dir)

    # --- Add team membership pointer to acceptor's NoteToSelf ---
    # Only a lightweight Team reference goes in NoteToSelf; structural data
    # (App, TeamAppStation, StationRole) lives in the team DB, which was cloned above.
    user_db_path = acceptor_dir / "NoteToSelf" / "Sync" / "core.db"
    user_engine = create_engine(f"sqlite:///{user_db_path}")

    with Session(user_engine) as session:
        team_id = uuid7()
        team_row = Team(id=team_id, name=team_name, self_in_team=acceptor_member_id)
        session.add(team_row)
        session.commit()

    # --- Git commit the DB changes ---
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db", ".gitattributes"])
    CodSync.gitCmd(
        ["-C", str(team_sync_dir), "commit", "-m", f"Joined team: {team_name}"]
    )

    # --- Push to acceptor's cloud ---
    if acceptor_remote is None:
        raise ValueError("acceptor_remote is required")

    saved_cwd = os.getcwd()
    os.chdir(team_sync_dir)
    try:
        cod = CodSync.CodSync("acceptor-cloud")
        cod.gitCmd = CodSync.gitCmd
        cod.remote = acceptor_remote
        cod.push_to_remote(["main"])
    finally:
        os.chdir(saved_cwd)

    # --- Build and return acceptance response ---
    acceptance_data = {
        "invitation_id": invitation_id.hex(),
        "nonce": nonce.hex(),
        "acceptor_member_id": acceptor_member_id.hex(),
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
    acceptor_cloud = acceptance["acceptor_cloud"]
    acceptor_bucket = acceptance["acceptor_bucket"]

    # Find and validate the invitation in the inviter's team DB
    team_db_path = participant_dir / team_name / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{team_db_path}")

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT nonce, status FROM invitation WHERE id = :id"),
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
                "acceptor_url=:url, acceptor_access_key=:access_key, "
                "acceptor_secret_key=:secret_key "
                "WHERE id = :id"
            ),
            {
                "id": invitation_id,
                "now": now,
                "member_id": acceptor_member_id,
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
                "access_key": acceptor_cloud.get("access_key"),
                "secret_key": acceptor_cloud.get("secret_key"),
            },
        )

        # Add acceptor as member + peer in inviter's team DB
        conn.execute(
            text("INSERT INTO member (id) VALUES (:id)"), {"id": acceptor_member_id}
        )
        conn.execute(
            text(
                "INSERT INTO peer (id, member_id, protocol, url, access_key, secret_key) "
                "VALUES (:id, :member_id, :protocol, :url, :access_key, :secret_key)"
            ),
            {
                "id": uuid7(),
                "member_id": acceptor_member_id,
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
                "access_key": acceptor_cloud.get("access_key"),
                "secret_key": acceptor_cloud.get("secret_key"),
            },
        )

        # Grant the acceptor read-write on all stations (default).
        # The inviter (admin) can change this later.
        station_row = conn.execute(
            text("SELECT id FROM team_app_station LIMIT 1")
        ).fetchone()
        if station_row is not None:
            conn.execute(
                text(
                    "INSERT INTO station_role (id, member_id, station_id, role) "
                    "VALUES (:id, :mid, :sid, :role)"
                ),
                {
                    "id": uuid7(),
                    "mid": acceptor_member_id,
                    "sid": station_row[0],
                    "role": "read-write",
                },
            )

    # Dispose engine to release file locks before git operations
    engine.dispose()

    team_sync_dir = participant_dir / team_name / "Sync"
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"Accepted invitation"])


def add_notification_service(root_dir, participant_hex, protocol, url):
    """Register a notification service in a participant's NoteToSelf DB.

    Returns the notification service ID hex.
    """
    if protocol != "ntfy":
        raise ValueError(f"Unknown notification protocol: {protocol}")

    root_dir = pathlib.Path(root_dir)
    user_db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{user_db_path}")
    ns_id = uuid7()
    with Session(engine) as session:
        ns = NotificationService(id=ns_id, protocol=protocol, url=url)
        session.add(ns)
        session.commit()
    return ns_id.hex()


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
