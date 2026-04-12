PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cloud_storage_credential (
    cloud_storage_id BLOB PRIMARY KEY,
    access_key TEXT,
    secret_key TEXT,
    client_secret TEXT,
    refresh_token TEXT,
    access_token TEXT,
    token_expiry TEXT
);

CREATE TABLE IF NOT EXISTS notification_service_credential (
    notification_service_id BLOB PRIMARY KEY,
    access_key TEXT,
    access_token TEXT
);

CREATE TABLE IF NOT EXISTS note_to_self_device_key_secret (
    device_id BLOB PRIMARY KEY,
    encryption_private_key_ref TEXT NOT NULL,
    signing_private_key_ref TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_device_key_secret (
    team_id BLOB NOT NULL,
    device_id BLOB NOT NULL,
    private_key_ref TEXT NOT NULL,
    PRIMARY KEY (team_id, device_id)
);

CREATE TABLE IF NOT EXISTS team_sender_key (
    team_id BLOB PRIMARY KEY,
    group_id BLOB NOT NULL,
    sender_device_key_id BLOB NOT NULL,
    chain_id BLOB NOT NULL,
    chain_key BLOB NOT NULL,
    iteration INTEGER NOT NULL,
    signing_public_key BLOB NOT NULL,
    signing_private_key BLOB,
    skipped_message_keys TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS peer_sender_key (
    team_id BLOB NOT NULL,
    group_id BLOB NOT NULL,
    sender_device_key_id BLOB NOT NULL,
    chain_id BLOB NOT NULL,
    chain_key BLOB NOT NULL,
    iteration INTEGER NOT NULL,
    signing_public_key BLOB NOT NULL,
    signing_private_key BLOB,
    skipped_message_keys TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (team_id, sender_device_key_id)
);
