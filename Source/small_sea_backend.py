# Top Matter

import os
import sqlite3
import platformdirs
from datetime import datetime

class SmallSeaBackend:
    """
    """

    app_name = "SmallSeaCollectiveLocalHub"
    app_author = "Benjamin Ylvisaker"
    schema_version_number = 42
    
    def __init__( self ):
        self.root_dir = platformdirs.user_data_dir( SmallSeaBackend.app_name, SmallSeaBackend.app_author )
        os.makedirs( root_dir, exist_ok=True )
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

        if user_version == SmallSeaBackend.schema_version_number:
            print( "SmallSea local DB already initialized" )
            return

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

        cursor.execute( f"PRAGMA user_version = {SmallSeaBackend.schema_version_number}" )


print("Database schema initialized successfully.")

    def get_db_schema_version( self ):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1")
        version = cursor.fetchone()
        return version[0] if version else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()

# Example usage
version = get_schema_version(db_path)
if version:
    print(f"Database is initialized. Schema version: {version}")
else:
    print("Database is NOT initialized.")

    def fresh_user( self, nickname ):
        pass

    def add_cloud_location( self, user, url ):
        pass

    def fresh_team( self, user, team ):
        pass
