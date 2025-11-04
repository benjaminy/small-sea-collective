PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS session (
    lid INTEGER PRIMARY KEY AUTOINCREMENT,
    token BLOB NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    duration_sec INTEGER,
    participant_id BLOB NOT NULL,
    team_id INTEGER NOT NULL,
    app_id INTEGER NOT NULL,
    zone_id INTEGER NOT NULL,
    client TEXT NOT NULL
);
