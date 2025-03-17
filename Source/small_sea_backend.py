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
    schema_version : int = 42
    id_size_bytes  : int = 32
    
    def __init__( self ):
        self.root_dir = platformdirs.user_data_dir( SmallSeaBackend.app_name, SmallSeaBackend.app_author )
        os.makedirs( self.root_dir, exist_ok=True )
        self.path_local_db = os.path.join( self.root_dir, "small_sea_collective_local.db" )


    def _initialize_small_sea_db( self ):
        try:
            conn = None
            conn = sqlite3.connect( self.path_local_db )
            cursor = conn.cursor()
            self._initialize_schema( cursor )
            conn.commit()

        except sqlite3.Error as e:
            print("SQLite error occurred:", e)

        finally:
            if None != conn:
                conn.close()

    def _initialize_schema( self, cursor ):
        cursor.execute( "PRAGMA user_version" )
        user_version = cursor.fetchone()[ 0 ]

        if user_version == SmallSeaBackend.schema_version:
            print( "SmallSea local DB already initialized" )
            return

        if ( ( 0 != user_version )
             and ( user_version < SmallSeaBackend.schema_version ) ):
            print( "TODO: Migrate local DB!" )
            raise NotImplementedError()

        if user_version > SmallSeaBackend.schema_version:
            print( "TODO: DB FROM THE FUTURE!" )
            raise NotImplementedError()

        cursor.execute( "PRAGMA foreign_keys = ON;" )
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suid BYTES NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nickname (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            FOREIGN KEY (idenity_id) REFERENCES identity(id) ON DELETE CASCADE,
            nick TEXT NOT NULL
            )
        """)

        cursor.execute( f"PRAGMA user_version = {SmallSeaBackend.schema_version}" )
        print( "Database schema initialized successfully." )


    def new_identity( self, nickname ):
        ident = secrets.token_bytes( SmallSeaBackend.id_size_bytes )
        id_hex = "".join( f"{b:02x}" for b in ident )
        ident_dir = os.path.join( self.root_dir, id_hex )
        os.makedirs( ident_dir, exist_ok=True )
        self._initialize_small_sea_db()
        return id_hex

        # with sqlite3.connect( path_local_db ) as conn:
        # cursor = conn.cursor()
        # cursor.execute( "PRAGMA schema_version" )
        # schema_version = cursor.fetchone()[ 0 ]
        # if schema_version < 13:
        #     print( f"INIT SCHEMA!" )
        # return { "message": f"NICK '{nickname}'",
        #          "schema": schema_version }


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


