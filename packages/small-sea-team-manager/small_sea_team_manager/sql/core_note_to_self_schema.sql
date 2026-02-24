PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_device (
    id BLOB PRIMARY KEY,
    key BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS nickname (
    id BLOB PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team (
    id BLOB PRIMARY KEY,
    name TEXT NOT NULL,
    self_in_team BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS app (
    id BLOB PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_app_zone (
    id BLOB PRIMARY KEY,
    app_id BLOB NOT NULL,
    team_id BLOB NOT NULL,
    FOREIGN KEY (app_id) REFERENCES app(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES team(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cloud_storage (
    id BLOB PRIMARY KEY,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    -- Credential storage will likely change (e.g. to a keyring or vault reference)
    access_key TEXT,
    secret_key TEXT,
    -- OAuth fields for Google Drive / Dropbox
    client_id TEXT,
    client_secret TEXT,
    refresh_token TEXT,
    access_token TEXT,
    token_expiry TEXT,
    -- JSON dict mapping path → provider-specific metadata (e.g. Google Drive file IDs)
    path_metadata TEXT
);
