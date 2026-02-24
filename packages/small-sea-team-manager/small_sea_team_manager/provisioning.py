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

import os
import struct
import sqlite3
import secrets
import pathlib
import time
import json
import base64
from datetime import datetime, timezone

from sqlalchemy import create_engine, text, Column, String, LargeBinary
from sqlalchemy.orm import declarative_base, Session
Base = declarative_base()

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import corncob.protocol as CornCob


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
    b += bytes([(0x80 | (rand_bytes[2] & 0x3F))]) + rand_bytes[3:10]  # variant + rand_b
    return b


# ---- SQLAlchemy models for per-user core.db ----

class UserDevice(Base):
    __tablename__ = 'user_device'

    id = Column(LargeBinary, primary_key=True)
    key = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<UserDevice(id='{self.id.hex()}')>"


class Nickname(Base):
    __tablename__ = 'nickname'

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<Nickname(id='{self.id.hex()}')>"


class Team(Base):
    __tablename__ = 'team'

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)
    self_in_team = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<Team(id='{self.id.hex()}')>"


class App(Base):
    __tablename__ = 'app'

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<App(id='{self.id.hex()}')>"


class TeamAppZone(Base):
    __tablename__ = 'team_app_zone'

    id = Column(LargeBinary, primary_key=True)
    team_id = Column(LargeBinary, nullable=False)
    app_id = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<TeamAppZone(id='{self.id.hex()}')>"


# ---- SQLAlchemy models for per-team core.db ----

class Invitation(Base):
    __tablename__ = 'invitation'

    id = Column(LargeBinary, primary_key=True)
    nonce = Column(LargeBinary, nullable=False)
    status = Column(String, nullable=False, default='pending')
    invitee_label = Column(String)
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
    __tablename__ = 'peer'

    id = Column(LargeBinary, primary_key=True)
    member_id = Column(LargeBinary, nullable=False)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    access_key = Column(String)
    secret_key = Column(String)

    def __repr__(self):
        return f"<Peer(id='{self.id.hex()}')>"


class MemberCloud(Base):
    __tablename__ = 'member_cloud'

    id = Column(LargeBinary, primary_key=True)
    member_id = Column(LargeBinary, nullable=False)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    access_key = Column(String)
    secret_key = Column(String)

    def __repr__(self):
        return f"<MemberCloud(id='{self.id.hex()}')>"


# ---- Constants ----

USER_SCHEMA_VERSION = 44


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
        encryption_algorithm=serialization.NoEncryption()
    )
    device_public_key_bytes = device_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
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
            team1 = Team(
                id=uuid7(),
                name="NoteToSelf",
                self_in_team=b"0")
            app1 = App(id=uuid7(), name="SmallSeaCollectiveCore")
            session.add_all([nick1, team1, app1])
            session.flush()
            team_app = TeamAppZone(id=uuid7(), team_id=team1.id, app_id=app1.id)
            session.add_all([team_app])
            session.commit()

    except sqlite3.Error as e:
        print("SQLite error occurred:", e)

    repo_dir = root_dir / "Participants" / ident.hex() / "NoteToSelf" / "Sync"
    CornCob.gitCmd(["init", "-b", "main", str(repo_dir)])
    CornCob.gitCmd(["-C", str(repo_dir), "add", "core.db"])
    CornCob.gitCmd(["-C", str(repo_dir), "commit", "-m", f"Welcome to Small Sea Collective"])


def _migrate_user_db(conn, from_version):
    """Apply incremental migrations to bring a user DB up to USER_SCHEMA_VERSION."""
    if from_version < 44:
        for col in ["client_id", "client_secret", "refresh_token",
                     "access_token", "token_expiry", "path_metadata"]:
            conn.execute(text(f"ALTER TABLE cloud_storage ADD COLUMN {col} TEXT"))


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


def create_team(root_dir, participant_hex, team_name):
    """Create a new team for an existing participant.

    Adds team + team_app_zone rows to the user's NoteToSelf/Sync/core.db,
    creates the team directory with its own core.db (member table),
    and initializes a git repo for the team sync directory.

    Returns {"team_id_hex": ..., "member_id_hex": ...}.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    team_id = uuid7()
    member_id = uuid7()

    # --- Update the user's NoteToSelf core.db ---
    user_db_path = participant_dir / "NoteToSelf" / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{user_db_path}")

    with Session(engine) as session:
        app_row = session.query(App).filter_by(name="SmallSeaCollectiveCore").one()

        team_row = Team(
            id=team_id,
            name=team_name,
            self_in_team=member_id)
        session.add(team_row)
        session.flush()

        team_app = TeamAppZone(id=uuid7(), team_id=team_row.id, app_id=app_row.id)
        session.add(team_app)
        session.commit()

    # --- Create team directory and its core.db ---
    team_sync_dir = participant_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    team_db_path = team_sync_dir / "core.db"
    team_engine = _init_team_db(team_db_path)

    # Add the creator as the first member (fresh per-team ID)
    with team_engine.begin() as conn:
        conn.execute(text("INSERT INTO member (id) VALUES (:id)"),
                     {"id": member_id})

    # --- Git init ---
    CornCob.gitCmd(["init", "-b", "main", str(team_sync_dir)])
    CornCob.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CornCob.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"New team: {team_name}"])

    return {"team_id_hex": team_id.hex(), "member_id_hex": member_id.hex()}


def create_invitation(root_dir, participant_hex, team_name, inviter_cloud, invitee_label=None):
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

    # Create invitation row
    inv_id = uuid7()
    nonce = secrets.token_bytes(16)
    now = datetime.now(timezone.utc).isoformat()

    with team_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO invitation (id, nonce, status, invitee_label, created_at) "
            "VALUES (:id, :nonce, 'pending', :label, :created_at)"
        ), {"id": inv_id, "nonce": nonce, "label": invitee_label, "created_at": now})

    # Build token
    token_data = {
        "invitation_id": inv_id.hex(),
        "nonce": nonce.hex(),
        "team_name": team_name,
        "inviter_member_id": inviter_member_id.hex(),
        "inviter_cloud": inviter_cloud,
    }
    token_json = json.dumps(token_data)
    token_b64 = base64.b64encode(token_json.encode()).decode()

    # Git commit the updated DB
    team_sync_dir = participant_dir / team_name / "Sync"
    CornCob.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CornCob.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"Created invitation"])

    return token_b64


def accept_invitation(root_dir, acceptor_participant_hex, token_b64, acceptor_cloud):
    """Accept a team invitation token.

    acceptor_cloud: dict with keys protocol, url, access_key, secret_key.
    Returns {"team_name": ..., "member_id_hex": ...}.
    """
    root_dir = pathlib.Path(root_dir)

    # Decode token
    token_json = base64.b64decode(token_b64).decode()
    token = json.loads(token_json)
    team_name = token["team_name"]
    inviter_member_id = bytes.fromhex(token["inviter_member_id"])
    inviter_cloud = token["inviter_cloud"]
    invitation_id = bytes.fromhex(token["invitation_id"])
    nonce = bytes.fromhex(token["nonce"])

    # Generate Bob's fresh team-local member ID
    acceptor_member_id = uuid7()

    acceptor_dir = root_dir / "Participants" / acceptor_participant_hex

    # --- Create acceptor's team directory + DB ---
    team_sync_dir = acceptor_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    team_db_path = team_sync_dir / "core.db"
    team_engine = _init_team_db(team_db_path)

    with team_engine.begin() as conn:
        # Add acceptor as member
        conn.execute(text("INSERT INTO member (id) VALUES (:id)"),
                     {"id": acceptor_member_id})
        # Add inviter as member
        conn.execute(text("INSERT INTO member (id) VALUES (:id)"),
                     {"id": inviter_member_id})
        # Store inviter's cloud info as a peer
        conn.execute(text(
            "INSERT INTO peer (id, member_id, protocol, url, access_key, secret_key) "
            "VALUES (:id, :member_id, :protocol, :url, :access_key, :secret_key)"
        ), {
            "id": uuid7(),
            "member_id": inviter_member_id,
            "protocol": inviter_cloud["protocol"],
            "url": inviter_cloud["url"],
            "access_key": inviter_cloud.get("access_key"),
            "secret_key": inviter_cloud.get("secret_key"),
        })

    # --- Add team to acceptor's NoteToSelf ---
    user_db_path = acceptor_dir / "NoteToSelf" / "Sync" / "core.db"
    user_engine = create_engine(f"sqlite:///{user_db_path}")

    with Session(user_engine) as session:
        app_row = session.query(App).filter_by(name="SmallSeaCollectiveCore").one()
        team_id = uuid7()
        team_row = Team(id=team_id, name=team_name, self_in_team=acceptor_member_id)
        session.add(team_row)
        session.flush()
        team_app = TeamAppZone(id=uuid7(), team_id=team_row.id, app_id=app_row.id)
        session.add(team_app)
        session.commit()

    # --- Mark invitation accepted in inviter's DB ---
    _mark_invitation_accepted(
        root_dir, team_name, invitation_id, nonce,
        acceptor_member_id, acceptor_cloud)

    # --- Git init acceptor's team sync dir ---
    CornCob.gitCmd(["init", "-b", "main", str(team_sync_dir)])
    CornCob.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CornCob.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"Joined team: {team_name}"])

    return {"team_name": team_name, "member_id_hex": acceptor_member_id.hex()}


def _mark_invitation_accepted(root_dir, team_name, invitation_id, nonce,
                               acceptor_member_id, acceptor_cloud):
    """Scan local Participants dirs to find and mark the invitation as accepted."""
    root_dir = pathlib.Path(root_dir)
    participants_dir = root_dir / "Participants"

    for participant_dir in participants_dir.iterdir():
        team_db_path = participant_dir / team_name / "Sync" / "core.db"
        if not team_db_path.exists():
            continue

        engine = create_engine(f"sqlite:///{team_db_path}")
        found = False
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT nonce, status FROM invitation WHERE id = :id"),
                {"id": invitation_id}
            ).fetchone()

            if row is None:
                engine.dispose()
                continue

            # Found the invitation
            if row[1] != "pending":
                engine.dispose()
                raise ValueError(f"Invitation is not pending (status: {row[1]})")
            if row[0] != nonce:
                engine.dispose()
                raise ValueError("Nonce mismatch")

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(text(
                "UPDATE invitation SET status='accepted', accepted_at=:now, "
                "accepted_by=:member_id, acceptor_protocol=:protocol, "
                "acceptor_url=:url, acceptor_access_key=:access_key, "
                "acceptor_secret_key=:secret_key "
                "WHERE id = :id"
            ), {
                "id": invitation_id,
                "now": now,
                "member_id": acceptor_member_id,
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
                "access_key": acceptor_cloud.get("access_key"),
                "secret_key": acceptor_cloud.get("secret_key"),
            })

            # Add acceptor as member + peer in inviter's team DB
            conn.execute(text("INSERT INTO member (id) VALUES (:id)"),
                         {"id": acceptor_member_id})
            conn.execute(text(
                "INSERT INTO peer (id, member_id, protocol, url, access_key, secret_key) "
                "VALUES (:id, :member_id, :protocol, :url, :access_key, :secret_key)"
            ), {
                "id": uuid7(),
                "member_id": acceptor_member_id,
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
                "access_key": acceptor_cloud.get("access_key"),
                "secret_key": acceptor_cloud.get("secret_key"),
            })
            found = True

        # Dispose engine to release file locks before git operations
        engine.dispose()

        if found:
            team_sync_dir = participant_dir / team_name / "Sync"
            CornCob.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
            CornCob.gitCmd(["-C", str(team_sync_dir), "commit", "-m",
                            f"Accepted invitation"])
            return

    raise ValueError("Invitation not found in any participant's team DB")


def list_invitations(root_dir, participant_hex, team_name):
    """List invitations for a team. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (root_dir / "Participants" / participant_hex /
                    team_name / "Sync" / "core.db")
    engine = create_engine(f"sqlite:///{team_db_path}")

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, status, invitee_label, created_at FROM invitation"
        )).fetchall()

    return [
        {
            "id": row[0].hex(),
            "status": row[1],
            "invitee_label": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]
