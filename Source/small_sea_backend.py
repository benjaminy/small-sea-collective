# Top Matter

import os
import sqlite3
import platformdirs

class SmallSeaBackend:
    """
    """

    app_name = "SmallSeaCollectiveLocalHub"
    app_author = "Benjamin Ylvisaker"
    
    def __init__( self ):
        self.root_dir = platformdirs.user_data_dir( SmallSeaBackend.app_name, SmallSeaBackend.app_author )
        os.makedirs( root_dir, exist_ok=True )
        self.path_local_db = os.path.join( self.root_dir, "small_sea_collective_local.db" )

        pass

    def __initialize_small_sea_db( self ):
        import sqlite3
from datetime import datetime

# Connect to (or create) the SQLite database
db_path = "database.db"  # Change this if needed
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Create schema version table
cursor.execute("""
CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
""")

# Create users table
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL
)
""")

# Create posts table (linked to users)
cursor.execute("""
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
""")

# Insert initial schema version
schema_version = "1.0"
cursor.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", 
               (schema_version, datetime.utcnow().isoformat()))

# Commit and close
conn.commit()
conn.close()

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
