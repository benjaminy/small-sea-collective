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

from sqlalchemy import create_engine, text, Column, Integer, String, LargeBinary, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, Session, relationship
Base = declarative_base()

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import Common.git_remote_workalike_corncob as CornCob

class SmallSeaBackendExn(Exception):
    pass

class SmallSeaNotFoundExn(SmallSeaBackendExn):
    pass

class SmallSeaSession(Base):
    __tablename__ = 'session'

    lid = Column(Integer, primary_key=True)
    token = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    duration_sec = Column(Integer)
    participant_id = Column(Integer, nullable=False)
    app_id = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=False)
    zone_id = Column(Integer, nullable=False)
    client = Column(String, nullable=False)

    def __repr__(self):
        return f"<Session(lid={self.lid}, title='{self.token.hex()}')>"


class UserDevice(Base):
    __tablename__ = 'user_device'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    key = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<UserDevice(lid={self.lid}, suid='{self.suid}')>"


class Nickname(Base):
    __tablename__ = 'nickname'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<Nickname(lid={self.lid}, suid='{self.suid}')>"


class Team(Base):
    __tablename__ = 'team'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    name = Column(String, nullable=False)
    self_in_team = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<Team(lid={self.lid}, suid='{self.suid}')>"


class App(Base):
    __tablename__ = 'app'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<App(lid={self.lid}, suid='{self.suid}')>"


class TeamAppZone(Base):
    __tablename__ = 'team_app_zone'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    team_id = Column(Integer, ForeignKey("team.lid"), nullable=False)
    app_id = Column(Integer, ForeignKey("app.lid"), nullable=False)

    def __repr__(self):
        return f"<TeamAppZone(lid={self.lid}, suid='{self.suid}')>"


class CloudStorage(Base):
    __tablename__ = 'cloud_storage'

    lid = Column(Integer, primary_key=True)
    suid = Column(LargeBinary, nullable=False)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)

    def __repr__(self):
        return f"<CloudStorage(lid={self.lid}, suid='{self.suid}')>"


class SmallSeaBackend:
    """

    "Maybe overkill..."
    """

    app_author     : str = "Benjamin Ylvisaker"
    app_name : str = "SmallSeaCollectiveCore"
    hub_schema_version : int = 42
    user_schema_version : int = 42
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
        # self.root_dir = pathlib.Path(str(self.root_dir) + root_dir_suffix)
        os.makedirs( self.root_dir, exist_ok=True )
        self.path_local_db = self.root_dir / "small_sea_collective_local.db"
        os.makedirs( self.root_dir / "Logging", exist_ok=True )
        log_path = self.root_dir / "Logging" / "small_sea_hub.log"
        self.logger = setup_logging( log_file=log_path )
        self._initialize_small_sea_db()


    def create_new_participant(
            self,
            nickname:str,
            device=None):
        ident = secrets.token_bytes( SmallSeaBackend.id_size_bytes )
        ident_dir = self.root_dir / "Participants" / ident.hex()
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

        if False:
            signature = private_key.sign(b"my authenticated message")
            # Raises InvalidSignature if verification fails
            public_key.verify(signature, b"my authenticated message")
            loaded_public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)

        try:
            os.makedirs( ident_dir / "NoteToSelf" / "Sync", exist_ok=False )
            os.makedirs( ident_dir / "FakeEnclave", exist_ok=False )
        except Exception as exn:
            print( f"makedirs failed :( {ident_dir}" )
        if device is None:
            device = "42"
        self._initialize_user_db(
            ident,
            nickname,
            device)
        return ident.hex()


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


    def _initialize_user_db(
            self,
            ident:bytes,
            nickname:str,
            device:str):
        path = self.root_dir / "Participants" / ident.hex() / "NoteToSelf" / "Sync" / "core.db"
        engine = create_engine(f"sqlite:///{path}")
        conn = None
        try:
            with engine.begin() as conn:
                self._initialize_core_note_to_self_schema(conn)

            with Session(engine) as session:
                nick_id = secrets.token_bytes(SmallSeaBackend.id_size_bytes)
                nick1 = Nickname(suid=nick_id, name=nickname)
                note_to_self_id = secrets.token_bytes(SmallSeaBackend.id_size_bytes)
                team1 = Team(
                    suid=note_to_self_id,
                    name="NoteToSelf",
                    self_in_team=b"0")
                core_id = secrets.token_bytes(SmallSeaBackend.id_size_bytes)
                app1 = App(suid=core_id, name="SmallSeaCollectiveCore")
                session.add_all([nick1, team1, app1])
                session.flush()
                zone_id = secrets.token_bytes(SmallSeaBackend.id_size_bytes)
                print(f"ADD ZONE {team1.lid} {app1.lid}")
                team_app = TeamAppZone(suid=zone_id, team_id=team1.lid, app_id=app1.lid)
                session.add_all([team_app])
                session.commit()

        except sqlite3.Error as e:
            print("SQLite error occurred:", e)

        repo_dir = self.root_dir / "Participants" / ident.hex() / "NoteToSelf" / "Sync"
        CornCob.gitCmd(["init", "-b", "main", str(repo_dir)])
        CornCob.gitCmd(["-C", str(repo_dir), "add", "core.db"])
        CornCob.gitCmd(["-C", str(repo_dir), "commit", "-m", f"Hey, a new Small Sea Collective user"])

    def _initialize_core_note_to_self_schema(
            self,
            conn ):
        result = conn.execute(text("PRAGMA user_version"))
        user_version = result.scalar()

        if user_version == SmallSeaBackend.user_schema_version:
            print( "SmallSea local DB already initialized" )
            return

        if ( ( 0 != user_version )
             and ( user_version < SmallSeaBackend.user_schema_version ) ):
            print( "TODO: Migrate user DB!" )
            raise NotImplementedError()

        if user_version > SmallSeaBackend.user_schema_version:
            print( "TODO: DB FROM THE FUTURE!" )
            raise NotImplementedError()

        schema_path = pathlib.Path(__file__).parent
        schema_path = schema_path / "sql" / "core_note_to_self_schema.sql"

        with open(schema_path, "r") as f:
            schema_script = f.read()

        for statement in schema_script.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))

        conn.execute(text(f"PRAGMA user_version = {SmallSeaBackend.user_schema_version}"))
        print( "User DB schema initialized successfully." )


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

    def add_cloud_location(
            self,
            session,
            protocol,
            url ):
        known_protocols = ["s3", "webdav"]
        if protocol in known_protocols:
            pass
        else:
            error

        return self._add_cloud_location( session, protocol, url )


    def _lookup_session(
            self,
            session_hex):
        session_token = bytes.fromhex(session_hex)
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        print(f"FIND SESH {session_hex} {session_token}")
        with Session(engine_local) as session:
            results_sesh = session.query(SmallSeaSession).filter(SmallSeaSession.token == session_token).all()

        return results_sesh[0]

    def _add_cloud_location(
            self,
            session_hex,
            scheme,
            location ):

        ss_session = self._lookup_session(session_hex)

        participant_id = ss_session.participant_id.hex()
        # TODO: Should we check permissions? Probably.
        core_path = self.root_dir / "Participants" / participant_id / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            cloud_suid = secrets.token_bytes(SmallSeaBackend.id_size_bytes)
            cloud = CloudStorage(
                suid=cloud_suid,
                protocol=scheme,
                url=location)
            session.add_all([cloud])
            session.commit()


    def sync_to_cloud(
            self,
            session:str):
        ss_session = self._lookup_session(session)
        participant_id = ss_session.participant_id.hex()
        # TODO: Should we check permissions? Probably.
        core_path = self.root_dir / "Participants" / participant_id / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            results = session.query(CloudStorage).all()
            if 1 != len(results):
                print(f"TODO: Other cases {len(results)} {participant_id}")
                raise NotImplementedError()

    def make_device_link_invitation(
            self,
            session):
        # make keypair
        pass

    def create_team( self, session, team ):
        pass

    # try:
    #     cursor.execute("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1")
    #     version = cursor.fetchone()
    #     return version[0] if version else None
    # except sqlite3.Error:
    #     return None
    # finally:
    #     conn.close()

class SmallSeaStorageAdapter:
    def __init__(self):
        pass

class SmallSeaS3Adapter:
    def __init__(self, s3, bucket_name):
        self.s3 = s3
        self.bucket_name = bucket_name

    def upload(
            self,
            path:str,
            data:bytes,
            expected_etag:Optional[str],
            content_type: str = 'application/octet-stream' ):
        """
        """
        try:
            if expected_etag is None:
                # IfNoneMatch='*'  # Only upload if key doesn't exist
                response = s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=path,
                    Body=data,
                    ContentType=content_type
                )
            else:
                response = s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                    IfMatch=expected_etag
                )
            new_etag = response['ETag'].strip('"')
            return True, new_etag, "Object updated successfully"
        except ClientError as exn:
            error_code = e.response['Error']['Code']
            if error_code == 'PreconditionFailed':
                if expected_etag is None:
                    return False, None, "Object already exists"
                else:
                    return False, None, "ETag mismatch - object was modified"
            return False, None, f"Operation failed: {e}"

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

# GRAVEYARD:
if False:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BLOB NOT NULL UNIQUE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nickname (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_id INTEGER NOT NULL,
            nick TEXT NOT NULL,
            FOREIGN KEY (identity_id) REFERENCES identity(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BLOB NOT NULL UNIQUE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BLOB NOT NULL UNIQUE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            identity_id INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sesion(id) ON DELETE CASCADE,
            FOREIGN KEY (identity_id) REFERENCES identity(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_team (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sesion(id) ON DELETE CASCADE,
            FOREIGN KEY (team_id) REFERENCES team(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_app (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            app_id INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sesion(id) ON DELETE CASCADE,
            FOREIGN KEY (app_id) REFERENCES app(id) ON DELETE CASCADE
            )
        """)

def open_session(
        self,
        nickname,
        app,
        team,
        client):
    try:
        before = datetime.now()
        conn = None
        conn = sqlite3.connect( self.path_local_db )
        cursor = conn.cursor()
        print( f"GET IDENT {nickname}" )
        cursor.execute("SELECT identity_id FROM nickname WHERE nick = ?;", ( nickname, ) )

        ident = cursor.fetchall()[ 0 ][ 0 ]
        session_suid = secrets.token_bytes( SmallSeaBackend.id_size_bytes )

        print( f"ADD SESS {session_suid} {1234}" )
        cursor.execute("INSERT INTO session (suid, duration_sec) VALUES (?, ?);", ( session_suid, 1234 ) )
        session_id = cursor.lastrowid
        print( f"ADD SESSU {session_id} {ident}" )
        cursor.execute("INSERT INTO session_user (session_id, identity_id) VALUES (?, ?);",
                       ( session_id, ident ) )
        after = datetime.now()

        conn.commit()
        print( f"Starting a session took: {after - before}" )
        return session_suid

    except sqlite3.Error as e:
        print("SQLite error occurred:", e)

    finally:
        if None != conn:
            conn.close()
