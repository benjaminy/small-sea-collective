# Top Matter

import sys
import os
import sqlite3
from datetime import datetime, timezone
import secrets
import pathlib
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple
import plyer
import yaml

from sqlalchemy import create_engine, text, Column, Integer, String, LargeBinary, DateTime
from sqlalchemy.orm import declarative_base, Session
Base = declarative_base()

import corncob.protocol as CornCob
from small_sea_team_manager.provisioning import uuid7
from small_sea_hub.adapters import SmallSeaStorageAdapter, SmallSeaS3Adapter, SmallSeaGDriveAdapter, SmallSeaDropboxAdapter
from small_sea_hub.adapters.oauth import is_token_expired, refresh_google_token, refresh_dropbox_token

class SmallSeaBackendExn(Exception):
    pass

class SmallSeaNotFoundExn(SmallSeaBackendExn):
    pass


# ---- SQLAlchemy models ----
# Hub-local session table

class SmallSeaSession(Base):
    __tablename__ = 'session'

    id = Column(LargeBinary, primary_key=True)
    token = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    duration_sec = Column(Integer)
    participant_id = Column(LargeBinary, nullable=False)
    app_id = Column(LargeBinary, nullable=False)
    team_id = Column(LargeBinary, nullable=False)
    zone_id = Column(LargeBinary, nullable=False)
    client = Column(String, nullable=False)

    def __repr__(self):
        return f"<Session(id='{self.id.hex()}', token='{self.token.hex()}')>"


# Per-user core.db models (duplicated in team manager — the DB is the contract)

class Nickname(Base):
    __tablename__ = 'nickname'

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)


class Team(Base):
    __tablename__ = 'team'

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)
    self_in_team = Column(LargeBinary, nullable=False)


class App(Base):
    __tablename__ = 'app'

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)


class TeamAppZone(Base):
    __tablename__ = 'team_app_zone'

    id = Column(LargeBinary, primary_key=True)
    team_id = Column(LargeBinary, nullable=False)
    app_id = Column(LargeBinary, nullable=False)


class CloudStorage(Base):
    __tablename__ = 'cloud_storage'

    id = Column(LargeBinary, primary_key=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    # Credential storage will likely change (e.g. to a keyring or vault reference)
    access_key = Column(String, nullable=True)
    secret_key = Column(String, nullable=True)
    # OAuth fields for Google Drive / Dropbox
    client_id = Column(String, nullable=True)
    client_secret = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    access_token = Column(String, nullable=True)
    token_expiry = Column(String, nullable=True)
    # JSON dict mapping path → provider-specific metadata (e.g. Google Drive file IDs)
    path_metadata = Column(String, nullable=True)

    def __repr__(self):
        return f"<CloudStorage(id='{self.id.hex()}')>"


class SmallSeaBackend:
    """
    Hub backend — session management, cloud storage, sync.

    Participant/user/team provisioning has moved to the
    small-sea-team-manager package (provisioning.py).
    """

    hub_schema_version : int = 43

    def __init__(
            self,
            root_dir):
        self.root_dir = pathlib.Path(root_dir)
        os.makedirs( self.root_dir, exist_ok=True )
        self.path_local_db = self.root_dir / "small_sea_collective_local.db"
        os.makedirs( self.root_dir / "Logging", exist_ok=True )
        log_path = self.root_dir / "Logging" / "small_sea_hub.log"
        self.logger = setup_logging( log_file=log_path )
        self._initialize_small_sea_db()


    def _initialize_small_sea_db( self ):
        try:
            conn = None
            conn = sqlite3.connect( self.path_local_db )
            cursor = conn.cursor()
            self._initialize_small_sea_schema( cursor )
            conn.commit()

        except sqlite3.Error as e:
            print( f"SQLite error occurred: '{e}'" )

        finally:
            if None != conn:
                conn.close()


    def _initialize_small_sea_schema( self, cursor ):
        cursor.execute( "PRAGMA user_version" )
        user_version = cursor.fetchone()[ 0 ]

        if user_version == SmallSeaBackend.hub_schema_version:
            print( "SmallSea local DB already initialized" )
            return

        if ( ( 0 != user_version )
             and ( user_version < SmallSeaBackend.hub_schema_version ) ):
            print( "TODO: Migrate local DB!" )
            raise NotImplementedError()

        if user_version > SmallSeaBackend.hub_schema_version:
            print( "TODO: DB FROM THE FUTURE!" )
            raise NotImplementedError()

        schema_path = pathlib.Path(__file__).parent
        schema_path = schema_path / "sql" / "hub_local_schema.sql"

        with open(schema_path, "r") as f:
            schema_script = f.read()
        cursor.executescript(schema_script)

        cursor.execute( f"PRAGMA user_version = {SmallSeaBackend.hub_schema_version}" )
        print( "Hub DB schema initialized successfully." )


    # ---- Session management ----

    def open_session(
            self,
            nickname,
            app,
            team,
            client) -> bytes:
        auth_token = str(secrets.randbelow(10000)).zfill(4)
        if client != "Smoke Tests":
            plyer.notification.notify(
                title="Small Sea Access Request",
                message=f"Client {client} requested access to the resources for app {app}. {auth_token} is the code to provide to the client if you approve this request.",
                app_name="Small Sea Hub",
                # app_icon="PATH",
                timeout=5,
                ticker="WHAT THE HECK IS A TICKER"
            )

        matching_dirs = []
        participants_dir = self.root_dir / "Participants"
        for d in participants_dir.iterdir():
            if not d.is_dir():
                continue
            note_to_self_db_path = d / "NoteToSelf" / "Sync" / "core.db"
            engine = create_engine(f"sqlite:///{note_to_self_db_path}")
            # TODO: exn handling
            with Session(engine) as session:
                results = session.query(Nickname).filter(Nickname.name == nickname).all()
                if 0 < len(results):
                    matching_dirs.append((d, engine))

        if 1 > len(matching_dirs):
            raise SmallSeaNotFoundExn()

        # TODO: Multiple matches?

        (participant_dir, engine) = matching_dirs[0]
        participant_lid = participant_dir.absolute().name
        participant_lid = bytes.fromhex(participant_lid)

        with Session(engine) as session:
            results_team = session.query(Team).filter(Team.name == team).all()
            results_app = session.query(App).filter(App.name == app).all()
            if (1 > len(results_team)) or (1 > len(results_app)):
                raise SmallSeaNotFoundExn()
            results_zone = session.query(TeamAppZone).filter(
                (TeamAppZone.team_id == results_team[0].id) and (TeamAppZone.app_id == results_app[0].id)).all()
            print(results_team[0])
            print(results_app[0])
            if 1 > len(results_zone):
                raise SmallSeaNotFoundExn()

        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as session:
            token = secrets.token_bytes(32)
            ss_session = SmallSeaSession(
                id=uuid7(),
                token=token,
                duration_sec=1234,
                participant_id=participant_lid,
                team_id=results_team[0].id,
                app_id=results_app[0].id,
                zone_id=results_zone[0].id,
                client=client)

            print(f"ADD SESH {token.hex()} {token}")
            session.add_all([ss_session])
            session.commit()

        return token


    def _lookup_session(
            self,
            session_hex):
        session_token = bytes.fromhex(session_hex)
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        print(f"FIND SESH {session_hex} {session_token}")
        with Session(engine_local) as session:
            results_sesh = session.query(SmallSeaSession).filter(SmallSeaSession.token == session_token).all()

        ss_session = results_sesh[0]
        ss_session.participant_path = self.root_dir / "Participants" / ss_session.participant_id.hex()
        return ss_session


    # ---- Cloud storage ----

    def add_cloud_location(
            self,
            session,
            protocol,
            url,
            access_key=None,
            secret_key=None,
            client_id=None,
            client_secret=None,
            refresh_token=None ):
        known_protocols = ["s3", "webdav", "gdrive", "dropbox"]
        if protocol not in known_protocols:
            raise SmallSeaBackendExn(f"Unknown protocol: {protocol}")

        return self._add_cloud_location(
            session, protocol, url,
            access_key=access_key, secret_key=secret_key,
            client_id=client_id, client_secret=client_secret,
            refresh_token=refresh_token)


    def _add_cloud_location(
            self,
            session_hex,
            scheme,
            location,
            access_key=None,
            secret_key=None,
            client_id=None,
            client_secret=None,
            refresh_token=None ):
        ss_session = self._lookup_session(session_hex)

        # TODO: Should we check permissions? Probably.
        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            cloud = CloudStorage(
                id=uuid7(),
                protocol=scheme,
                url=location,
                access_key=access_key,
                secret_key=secret_key,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token)
            session.add_all([cloud])
            session.commit()

    def _commit_any_changes(
            self,
            ss_session:SmallSeaSession):
        repo_dir = ss_session.participant_path / "NoteToSelf" / "Sync"
        diff_q = CornCob.gitCmd(["-C", str(repo_dir), "diff", "--quiet"], raise_on_error=False)
        if 0 != diff_q.returncode:
            CornCob.gitCmd(["-C", str(repo_dir), "add", "-A"])
            CornCob.gitCmd(["-C", str(repo_dir), "commit", "-m", "TODO: Better commit message"])


    def _get_cloud_link(
            self,
            ss_session:SmallSeaSession):
        # TODO: Should we check permissions? Probably.
        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            results = session.query(CloudStorage).all()
            if 1 != len(results):
                print(f"TODO: Other cases {len(results)}")
                raise NotImplementedError()
            cloud = results[0]
        return cloud


    def _make_storage_adapter(
            self,
            ss_session:SmallSeaSession):
        cloud = self._get_cloud_link(ss_session)

        if cloud.protocol == "s3":
            return self._make_s3_adapter(ss_session, cloud)
        elif cloud.protocol == "gdrive":
            return self._make_gdrive_adapter(ss_session, cloud)
        elif cloud.protocol == "dropbox":
            return self._make_dropbox_adapter(ss_session, cloud)
        else:
            raise SmallSeaBackendExn(f"Unsupported protocol: {cloud.protocol}")


    def _refresh_token_if_needed(self, ss_session, cloud):
        """Refresh OAuth token if expired, persisting the new token to the DB."""
        if not is_token_expired(cloud.token_expiry):
            return cloud.access_token

        if cloud.protocol == "gdrive":
            access_token, expiry = refresh_google_token(
                cloud.client_id, cloud.client_secret, cloud.refresh_token)
        elif cloud.protocol == "dropbox":
            access_token, expiry = refresh_dropbox_token(
                cloud.client_id, cloud.client_secret, cloud.refresh_token)
        else:
            raise SmallSeaBackendExn(f"No token refresh for protocol: {cloud.protocol}")

        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            session.execute(
                text("UPDATE cloud_storage SET access_token = :token, token_expiry = :expiry WHERE id = :id"),
                {"token": access_token, "expiry": expiry, "id": cloud.id})
            session.commit()

        return access_token


    def _make_s3_adapter(self, ss_session, cloud):
        import boto3
        from botocore.config import Config as BotoConfig

        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            zone = session.query(TeamAppZone).filter(
                TeamAppZone.id == ss_session.zone_id).first()
            if zone is None:
                raise SmallSeaNotFoundExn("zone not found")

        bucket_name = f"ss-{zone.id.hex()[:16]}"

        s3_client = boto3.client(
            "s3",
            endpoint_url=cloud.url,
            aws_access_key_id=cloud.access_key,
            aws_secret_access_key=cloud.secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )

        return SmallSeaS3Adapter(s3_client, bucket_name)


    def _make_gdrive_adapter(self, ss_session, cloud):
        import json as _json
        access_token = self._refresh_token_if_needed(ss_session, cloud)
        path_metadata = None
        if cloud.path_metadata:
            path_metadata = _json.loads(cloud.path_metadata)
        return SmallSeaGDriveAdapter(access_token, path_metadata=path_metadata)


    def _make_dropbox_adapter(self, ss_session, cloud):
        access_token = self._refresh_token_if_needed(ss_session, cloud)
        return SmallSeaDropboxAdapter(access_token)


    def upload_to_cloud(
            self,
            session_hex,
            path,
            data):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        return adapter.upload_overwrite(path, data)


    def download_from_cloud(
            self,
            session_hex,
            path):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        return adapter.download(path)


    # ---- Sync ----

    def sync_to_cloud(
            self,
            session:str):
        ss_session = self._lookup_session(session)
        self._commit_any_changes(ss_session)
        cloud = self._get_cloud_link(ss_session)

        repo_dir = ss_session.participant_path / "NoteToSelf" / "Sync"
        rev_parse = CornCob.gitCmd(
            ["-C", str(repo_dir), "rev-parse", "HEAD"])

        local_hash = bytes.fromhex(rev_parse.stdout.strip())

        head_path = ss_session.participant_path / "NoteToSelf" / "Local" / "cached_cloud_head.yaml"
        try:
            cached_head_str = head_path.read_text()
            cached_head = yaml.safe_load(cached_head_str)
            cached_cloud_hash = bytes.fromhex(cached_head.commit_hash)
        except FileNotFoundError:
            data = None

    def sync_from_cloud(
            self,
            session:str):
        ss_session = self._lookup_session(session)


def setup_logging(
    log_file="app.log",
    console_level=logging.INFO,
    file_level=logging.DEBUG,
    max_bytes=5*1024*1024,
    backup_count=3
):
    # Create a root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Capture everything, handlers will filter

    # Clear existing handlers (important for e.g. pytest or re-imports)
    if logger.hasHandlers():
        logger.handlers.clear()

    # Formatters
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    # Attach handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
