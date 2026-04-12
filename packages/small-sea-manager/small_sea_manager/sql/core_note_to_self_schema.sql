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

CREATE TABLE IF NOT EXISTS notification_service (
    id BLOB PRIMARY KEY,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    access_key TEXT,    -- Gotify app token (publish) or ntfy auth token
    access_token TEXT   -- Gotify client token (poll/subscribe)
);

CREATE TABLE IF NOT EXISTS team_device_key (
    team_id          BLOB NOT NULL,
    device_id        BLOB NOT NULL,
    public_key       BLOB NOT NULL,
    private_key_ref  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    revoked_at       TEXT,
    PRIMARY KEY (team_id, device_id),
    FOREIGN KEY (team_id) REFERENCES team(id),
    FOREIGN KEY (device_id) REFERENCES user_device(id)
);

CREATE TABLE IF NOT EXISTS team_sender_key (
    team_id              BLOB PRIMARY KEY,
    group_id             BLOB NOT NULL,
    sender_device_key_id BLOB NOT NULL,
    chain_id             BLOB NOT NULL,
    chain_key            BLOB NOT NULL,
    iteration            INTEGER NOT NULL,
    signing_public_key   BLOB NOT NULL,
    signing_private_key  BLOB,
    skipped_message_keys TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (team_id) REFERENCES team(id)
);

CREATE TABLE IF NOT EXISTS peer_sender_key (
    team_id              BLOB NOT NULL,
    group_id             BLOB NOT NULL,
    sender_device_key_id BLOB NOT NULL,
    chain_id             BLOB NOT NULL,
    chain_key            BLOB NOT NULL,
    iteration            INTEGER NOT NULL,
    signing_public_key   BLOB NOT NULL,
    signing_private_key  BLOB,
    skipped_message_keys TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (team_id, sender_device_key_id),
    FOREIGN KEY (team_id) REFERENCES team(id)
);
