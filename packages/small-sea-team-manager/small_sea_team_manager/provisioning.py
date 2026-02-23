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



# ---- Constants ----

USER_SCHEMA_VERSION = 43


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


def _initialize_core_note_to_self_schema(conn):
    result = conn.execute(text("PRAGMA user_version"))
    user_version = result.scalar()

    if user_version == USER_SCHEMA_VERSION:
        print("SmallSea local DB already initialized")
        return

    if (0 != user_version) and (user_version < USER_SCHEMA_VERSION):
        print("TODO: Migrate user DB!")
        raise NotImplementedError()

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


def create_team(root_dir, participant_hex, team_name):
    """Create a new team for an existing participant.

    Adds team + team_app_zone rows to the user's NoteToSelf/Sync/core.db,
    creates the team directory with its own core.db (member table),
    and initializes a git repo for the team sync directory.

    Returns the new team's id hex.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    # --- Update the user's NoteToSelf core.db ---
    user_db_path = participant_dir / "NoteToSelf" / "Sync" / "core.db"
    engine = create_engine(f"sqlite:///{user_db_path}")

    team_id = uuid7()

    with Session(engine) as session:
        # Reuse the existing SmallSeaCollectiveCore app row
        app_row = session.query(App).filter_by(name="SmallSeaCollectiveCore").one()

        team_row = Team(
            id=team_id,
            name=team_name,
            self_in_team=b"0")
        session.add(team_row)
        session.flush()

        team_app = TeamAppZone(id=uuid7(), team_id=team_row.id, app_id=app_row.id)
        session.add(team_app)
        session.commit()

    # --- Create team directory and its core.db ---
    team_sync_dir = participant_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    team_db_path = team_sync_dir / "core.db"
    team_engine = create_engine(f"sqlite:///{team_db_path}")

    with team_engine.begin() as conn:
        schema_path = pathlib.Path(__file__).parent / "sql" / "core_other_team.sql"
        with open(schema_path, "r") as f:
            schema_script = f.read()
        for statement in schema_script.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
        conn.execute(text(f"PRAGMA user_version = {USER_SCHEMA_VERSION}"))

    # Add the creator as the first member
    with team_engine.begin() as conn:
        conn.execute(text("INSERT INTO member (id) VALUES (:id)"),
                     {"id": bytes.fromhex(participant_hex)})

    # --- Git init ---
    CornCob.gitCmd(["init", "-b", "main", str(team_sync_dir)])
    CornCob.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CornCob.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"New team: {team_name}"])

    return team_id.hex()
