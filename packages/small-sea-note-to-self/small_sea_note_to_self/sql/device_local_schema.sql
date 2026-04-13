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

CREATE TABLE IF NOT EXISTS linked_team_bootstrap_session (
    bootstrap_id BLOB PRIMARY KEY,
    team_id BLOB NOT NULL,
    device_id BLOB NOT NULL,
    team_device_public_key BLOB NOT NULL,
    team_device_private_key BLOB,
    x3dh_identity_dh_public_key BLOB NOT NULL,
    x3dh_identity_dh_private_key BLOB NOT NULL,
    x3dh_identity_signing_public_key BLOB NOT NULL,
    x3dh_identity_signing_private_key BLOB NOT NULL,
    signed_prekey_id BLOB NOT NULL,
    signed_prekey_public_key BLOB NOT NULL,
    signed_prekey_private_key BLOB NOT NULL,
    one_time_prekey_id BLOB,
    one_time_prekey_public_key BLOB,
    one_time_prekey_private_key BLOB,
    ratchet_state_json TEXT,
    finalized_at TEXT,
    response_payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_linked_team_bootstrap (
    bootstrap_id BLOB PRIMARY KEY,
    team_id BLOB NOT NULL,
    peer_device_id BLOB NOT NULL,
    peer_team_device_public_key BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS redistribution_prekey_state (
    team_id BLOB PRIMARY KEY,
    identity_dh_public_key BLOB NOT NULL,
    identity_dh_private_key BLOB NOT NULL,
    identity_signing_public_key BLOB NOT NULL,
    identity_signing_private_key BLOB NOT NULL,
    signed_prekey_id BLOB NOT NULL,
    signed_prekey_public_key BLOB NOT NULL,
    signed_prekey_private_key BLOB NOT NULL,
    signed_prekey_signature BLOB NOT NULL,
    published_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS redistribution_one_time_prekey (
    team_id BLOB NOT NULL,
    prekey_id BLOB NOT NULL,
    public_key BLOB NOT NULL,
    private_key BLOB,
    consumed_at TEXT,
    PRIMARY KEY (team_id, prekey_id)
);
