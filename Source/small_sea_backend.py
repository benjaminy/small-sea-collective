# Top Matter

import sys
import os
import sqlite3
import platformdirs
from datetime import datetime
import secrets
import pathlib
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple
from botocore.exceptions import ClientError

class SmallSeaBackend:
    """

    "Maybe overkill..."
    """

    app_author     : str = "Benjamin Ylvisaker"
    hub_schema_version : int = 42
    user_schema_version : int = 42
    id_size_bytes  : int = 32

    def __init__(
            self,
            app_name,
            root_dir_suffix):
        self.root_dir = pathlib.Path(
            platformdirs.user_data_dir( app_name, SmallSeaBackend.app_author ) )
        self.root_dir = pathlib.Path(str(self.root_dir) + root_dir_suffix)
        os.makedirs( self.root_dir, exist_ok=True )
        self.path_local_db = self.root_dir / "small_sea_collective_local.db"
        os.makedirs( self.root_dir / "Logging", exist_ok=True )
        log_path = self.root_dir / "Logging" / "small_sea_hub.log"
        self.logger = setup_logging( log_file=log_path )
        # self._initialize_small_sea_db()


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

        cursor.execute( "PRAGMA foreign_keys = ON;" )
        
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
            CREATE TABLE IF NOT EXISTS session (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BLOB NOT NULL UNIQUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            duration_sec INTEGER
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

        cursor.execute( f"PRAGMA user_version = {SmallSeaBackend.hub_schema_version}" )
        print( "Hub DB schema initialized successfully." )


    def create_new_participant( self, nickname ):
        ident = secrets.token_bytes( SmallSeaBackend.id_size_bytes )
        id_hex = "".join( f"{b:02x}" for b in ident )
        ident_dir = self.root_dir / "Participants" / id_hex / "NoteToSelf"
        try:
            os.makedirs( ident_dir, exist_ok=False )
        except Exception as exn:
            print( f"makedirs failed :( {ident_dir}" )
        self._initialize_user_db( ident, id_hex )
        # self._add_user_to_hub_db( ident, nickname )
        return id_hex

    def _add_user_to_hub_db( self, ident, nickname ):
        try:
            conn = None
            print( f"add_user_to_hub_db {self.path_local_db}" )
            conn = sqlite3.connect( self.path_local_db )
            cursor = conn.cursor()
            self._add_user_to_hub( cursor, ident, nickname )
            conn.commit()

        except sqlite3.Error as e:
            print("SQLite error occurred:", e)

        finally:
            if None != conn:
                conn.close()

    def _add_user_to_hub( self, cursor, ident, nickname ):
        cursor.execute( "INSERT INTO identity (suid) VALUES (?)", ( ident, ) )
        new_id = cursor.lastrowid
        cursor.execute( "INSERT INTO nickname (identity_id, nick) VALUES (?, ?)", ( new_id, nickname ) )

    def _initialize_user_db( self, ident, id_hex ):
        try:
            conn = None
            path = self.root_dir / "Participants" / id_hex / "NoteToSelf" /"participant.db"
            conn = sqlite3.connect( path )
            cursor = conn.cursor()
            self._initialize_core_note_to_self_schema( cursor )
            conn.commit()

        except sqlite3.Error as e:
            print("SQLite error occurred:", e)

        finally:
            if None != conn:
                conn.close()

    def _initialize_core_note_to_self_schema( self, cursor ):
        cursor.execute( "PRAGMA user_version" )
        user_version = cursor.fetchone()[ 0 ]

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

        cursor.execute( "PRAGMA foreign_keys = ON;" )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BLOB NOT NULL,
            name TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BLOB NOT NULL,
            name TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_team (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BLOB NOT NULL,
            app_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            FOREIGN KEY (app_id) REFERENCES app(id) ON DELETE CASCADE,
            FOREIGN KEY (team_id) REFERENCES team(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cloud (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protocol TEXT NOT NULL,
            url TEXT NOT NULL
            )
        """)

        # cursor.execute("""
        #     CREATE TABLE IF NOT EXISTS nickname (
        #     id INTEGER PRIMARY KEY AUTOINCREMENT,
        #     FOREIGN KEY (identity_id) REFERENCES identity(id) ON DELETE CASCADE,
        #     nick TEXT NOT NULL
        #     )
        # """)

        cursor.execute( f"PRAGMA user_version = {SmallSeaBackend.user_schema_version}" )
        print( "User DB schema initialized successfully." )

    def add_cloud_location( self, session, url ):
        idx_colon = url.find( ":" )
        if 0 > idx_colon:
            error
        scheme = url[ : idx_colon ]
        location = url[ 1 + idx_colon : ]
        if "webdav" == scheme.lower():
            pass
        else:
            error

        return _add_cloud_location( self, session, scheme, location )

    def _add_cloud_location( self, session_hex, scheme, location ):
        try:
            session_suid = bytes.fromhex( session_hex )
            conn = None
            conn = sqlite3.connect( self.path_local_db )
            cursor = conn.cursor()
            cursor.execute("""
                SELECT identity.suid FROM identity
                JOIN session ON session.id = session_user.session_id
                JOIN identity ON indentity.id = session_user.identity_id
                WHERE session.suid = ?;""",
                ( session_suid, ) )

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

    def create_team( self, session, team ):
        pass

    def start_session_user( self, nickname ):
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
