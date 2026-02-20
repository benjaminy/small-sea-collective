# Top Matter

import sys
import os
import sqlite3
import platformdirs
from datetime import datetime, timezone
import secrets
import pathlib
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple
from botocore.exceptions import ClientError
import plyer
import yaml

from sqlalchemy import create_engine, text, Column, Integer, String, LargeBinary, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, Session, relationship
Base = declarative_base()

import corncob.protocol as CornCob

class SmallSeaBackendExn(Exception):
    pass

class SmallSeaNotFoundExn(SmallSeaBackendExn):
    pass


# ---- SQLAlchemy models ----
# Hub-local session table

class SmallSeaSession(Base):
    __tablename__ = 'session'

    lid = Column(Integer, primary_key=True)
    token = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    duration_sec = Column(Integer)
    participant_id = Column(LargeBinary, nullable=False)
    app_id = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=False)
    zone_id = Column(Integer, nullable=False)
    client = Column(String, nullable=False)

    def __repr__(self):
        return f"<Session(lid={self.lid}, title='{self.token.hex()}')>"


# Per-user core.db models (duplicated in team manager — the DB is the contract)

class Nickname(Base):
    __tablename__ = 'nickname'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    name = Column(String, nullable=False)


class Team(Base):
    __tablename__ = 'team'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    name = Column(String, nullable=False)
    self_in_team = Column(LargeBinary, nullable=False)


class App(Base):
    __tablename__ = 'app'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    name = Column(String, nullable=False)


class TeamAppZone(Base):
    __tablename__ = 'team_app_zone'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    team_id = Column(Integer, ForeignKey("team.lid"), nullable=False)
    app_id = Column(Integer, ForeignKey("app.lid"), nullable=False)


class CloudStorage(Base):
    __tablename__ = 'cloud_storage'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    # Credential storage will likely change (e.g. to a keyring or vault reference)
    access_key = Column(String, nullable=True)
    secret_key = Column(String, nullable=True)

    def __repr__(self):
        return f"<CloudStorage(lid={self.lid}, suid='{self.suid}')>"


class SmallSeaBackend:
    """
    Hub backend — session management, cloud storage, sync.

    Participant/user/team provisioning has moved to the
    small-sea-team-manager package (provisioning.py).
    """

    app_author     : str = "Benjamin Ylvisaker"
    app_name : str = "SmallSeaCollectiveCore"
    hub_schema_version : int = 42
    id_size_bytes  : int = 32

    def __init__(
            self,
            root_dir=None):
        if root_dir is None:
            self.root_dir = pathlib.Path(
                platformdirs.user_data_dir( SmallSeaBackend.app_name, SmallSeaBackend.app_author ) )
        else:
            self.root_dir = pathlib.Path(root_dir)
        print(f"ROOTROOTROOT '{self.root_dir}'")
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
                (TeamAppZone.team_id == results_team[0].lid) and (TeamAppZone.app_id == results_app[0].lid)).all()
            print(results_team[0])
            print(results_app[0])
            if 1 > len(results_zone):
                raise SmallSeaNotFoundExn()

        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as session:
            token = secrets.token_bytes(SmallSeaBackend.id_size_bytes)
            ss_session = SmallSeaSession(
                token=token,
                duration_sec=1234,
                participant_id=participant_lid,
                team_id=results_team[0].lid,
                app_id=results_app[0].lid,
                zone_id=results_zone[0].lid,
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
            secret_key=None ):
        known_protocols = ["s3", "webdav"]
        if protocol in known_protocols:
            pass
        else:
            error

        return self._add_cloud_location( session, protocol, url, access_key, secret_key )


    def _add_cloud_location(
            self,
            session_hex,
            scheme,
            location,
            access_key=None,
            secret_key=None ):
        ss_session = self._lookup_session(session_hex)

        # TODO: Should we check permissions? Probably.
        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            cloud_suid = secrets.token_bytes(SmallSeaBackend.id_size_bytes)
            cloud = CloudStorage(
                suid=cloud_suid,
                protocol=scheme,
                url=location,
                access_key=access_key,
                secret_key=secret_key)
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


    def _make_s3_adapter(
            self,
            ss_session:SmallSeaSession):
        import boto3
        from botocore.config import Config as BotoConfig

        cloud = self._get_cloud_link(ss_session)

        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            zone = session.query(TeamAppZone).filter(
                TeamAppZone.lid == ss_session.zone_id).first()
            if zone is None:
                raise SmallSeaNotFoundExn("zone not found")
            zone_suid = zone.suid

        bucket_name = f"ss-{zone_suid.hex()[:16]}"

        s3_client = boto3.client(
            "s3",
            endpoint_url=cloud.url,
            aws_access_key_id=cloud.access_key,
            aws_secret_access_key=cloud.secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )

        return SmallSeaS3Adapter(s3_client, bucket_name)


    def upload_to_cloud(
            self,
            session_hex,
            path,
            data):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_s3_adapter(ss_session)
        return adapter.upload_overwrite(path, data)


    def download_from_cloud(
            self,
            session_hex,
            path):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_s3_adapter(ss_session)
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


# ---- Storage adapters ----

class SmallSeaStorageAdapter:
    def __init__(
            self,
            zone:str):
        self.zone = zone

    def upload_overwrite(
            self,
            path:str,
            data:bytes,
            content_type: str = 'application/octet-stream'):
        return self._upload(path, data, None, content_type)

    def upload_fresh(
            self,
            path:str,
            data:bytes,
            content_type: str = 'application/octet-stream'):
        return self._upload(path, data, "*", content_type)

    def upload_if_match(
            self,
            path:str,
            data:bytes,
            expected_etag:str,
            content_type: str = 'application/octet-stream'):
        return self._upload(path, data, expected_etag, content_type)


class SmallSeaS3Adapter(SmallSeaStorageAdapter):
    def __init__(self, s3, bucket_name):
        super().__init__(bucket_name)
        self.s3 = s3

    def download(self, path:str):
        try:
            response = self.s3.get_object(Bucket=self.zone, Key=path)
            return True, response['Body'].read(), response['ETag'].strip('"')
        except ClientError as exn:
            error_code = exn.response['Error']['Code']
            return False, None, f"Download failed: {error_code}"

    def _upload(
            self,
            path:str,
            data:bytes,
            expected_etag:Optional[str],
            content_type: str = 'application/octet-stream' ):
        try:
            if expected_etag is None:
                response = self.s3.put_object(
                    Bucket=self.zone,
                    Key=path,
                    Body=data,
                    ContentType=content_type
                )
            elif "*" == expected_etag:
                response = self.s3.put_object(
                    Bucket=self.zone,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                    IfNoneMatch=expected_etag
                )
            else:
                response = self.s3.put_object(
                    Bucket=self.zone,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                    IfMatch=expected_etag
                )
            new_etag = response['ETag'].strip('"')
            return True, new_etag, "Object updated successfully"
        except ClientError as exn:
            error_code = exn.response['Error']['Code']
            if error_code == 'PreconditionFailed':
                if expected_etag is None:
                    return False, None, "Object already exists"
                else:
                    return False, None, "ETag mismatch - object was modified"
            return False, None, f"Operation failed: {exn}"

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
