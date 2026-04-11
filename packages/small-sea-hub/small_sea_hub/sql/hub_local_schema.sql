PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS session (
    id BLOB PRIMARY KEY,
    token BLOB NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    duration_sec INTEGER,
    participant_id BLOB NOT NULL,
    team_id BLOB NOT NULL,
    team_name TEXT NOT NULL,
    app_id BLOB NOT NULL,
    app_name TEXT NOT NULL,
    berth_id BLOB NOT NULL,
    mode TEXT NOT NULL,
    client TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_session (
    id BLOB PRIMARY KEY,
    participant_hex TEXT NOT NULL,
    team_name TEXT NOT NULL,
    app_name TEXT NOT NULL,
    client_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    pin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bootstrap_session (
    id BLOB PRIMARY KEY,
    token BLOB NOT NULL,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    bucket TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
