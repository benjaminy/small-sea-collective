PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS session (
    id BLOB PRIMARY KEY,
    token BLOB NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    duration_sec INTEGER,
    participant_id BLOB NOT NULL,
    team_id BLOB NOT NULL,
    app_id BLOB NOT NULL,
    zone_id BLOB NOT NULL,
    client TEXT NOT NULL
);
