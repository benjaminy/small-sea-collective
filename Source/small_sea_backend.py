# Top Matter

import os
import sqlite3
import platformdirs
from datetime import datetime
import secrets

class SmallSeaBackend:
    """

    "Maybe overkill..."
    """

    app_name       : str = "SmallSeaCollectiveLocalHub"
    app_author     : str = "Benjamin Ylvisaker"
    hub_schema_version : int = 42
    user_schema_version : int = 42
    id_size_bytes  : int = 32
    
    def __init__( self ):
        self.root_dir = platformdirs.user_data_dir( SmallSeaBackend.app_name, SmallSeaBackend.app_author )
        os.makedirs( self.root_dir, exist_ok=True )
        self.path_local_db = os.path.join( self.root_dir, "small_sea_collective_local.db" )
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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


    def new_identity( self, nickname ):
        ident = secrets.token_bytes( SmallSeaBackend.id_size_bytes )
        id_hex = "".join( f"{b:02x}" for b in ident )
        ident_dir = os.path.join( self.root_dir, id_hex, "Private" )
        try:
            os.makedirs( ident_dir, exist_ok=False )
        except Exception as exn:
            print( f"makedirs failed :( {ident_dir}" )
        self._initialize_user_db( ident, id_hex )
        self._add_user_to_hub_db( ident, nickname )
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
            path = os.path.join( self.root_dir, id_hex, "Private", "identity.db" )
            conn = sqlite3.connect( path )
            cursor = conn.cursor()
            self._initialize_user_schema( cursor )
            conn.commit()

        except sqlite3.Error as e:
            print("SQLite error occurred:", e)

        finally:
            if None != conn:
                conn.close()

    def _initialize_user_schema( self, cursor ):
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

        # cursor.execute("""
        #     CREATE TABLE IF NOT EXISTS nickname (
        #     id INTEGER PRIMARY KEY AUTOINCREMENT,
        #     FOREIGN KEY (identity_id) REFERENCES identity(id) ON DELETE CASCADE,
        #     nick TEXT NOT NULL
        #     )
        # """)

        cursor.execute( f"PRAGMA user_version = {SmallSeaBackend.user_schema_version}" )
        print( "User DB schema initialized successfully." )

    def add_cloud_location( self, user, url ):
        pass


    def fresh_team( self, user, team ):
        pass


    # try:
    #     cursor.execute("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1")
    #     version = cursor.fetchone()
    #     return version[0] if version else None
    # except sqlite3.Error:
    #     return None
    # finally:
    #     conn.close()


