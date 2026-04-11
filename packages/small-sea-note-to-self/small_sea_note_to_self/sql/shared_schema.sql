PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_device (
    id BLOB PRIMARY KEY,
    bootstrap_encryption_key BLOB NOT NULL,
    signing_key BLOB NOT NULL
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

CREATE TABLE IF NOT EXISTS team_app_berth (
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
    client_id TEXT,
    path_metadata TEXT
);

CREATE TABLE IF NOT EXISTS notification_service (
    id BLOB PRIMARY KEY,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_device_key (
    team_id BLOB NOT NULL,
    device_id BLOB NOT NULL,
    public_key BLOB NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    PRIMARY KEY (team_id, device_id),
    FOREIGN KEY (team_id) REFERENCES team(id),
    FOREIGN KEY (device_id) REFERENCES user_device(id)
);
