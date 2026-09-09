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

CREATE TABLE IF NOT EXISTS note_to_self_sync_state (
    berth_id BLOB PRIMARY KEY,
    last_adopted_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL
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
    bootstrap_bundle TEXT,
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

CREATE TABLE IF NOT EXISTS redistribution_delivery (
    team_id BLOB NOT NULL,
    sender_device_key_id BLOB NOT NULL,
    sender_chain_id BLOB NOT NULL,
    target_device_key_id BLOB NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (team_id, sender_device_key_id, sender_chain_id, target_device_key_id)
);

CREATE TABLE IF NOT EXISTS redistribution_receipt (
    team_id BLOB NOT NULL,
    sender_device_key_id BLOB NOT NULL,
    sender_chain_id BLOB NOT NULL,
    target_device_key_id BLOB NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (team_id, sender_device_key_id, sender_chain_id, target_device_key_id)
);

CREATE TABLE IF NOT EXISTS runtime_reconciliation_state (
    team_id BLOB PRIMARY KEY,
    trusted_teammate_ids_json TEXT NOT NULL,
    trusted_device_key_ids_json TEXT NOT NULL,
    last_sender_device_key_id BLOB,
    last_sender_chain_id BLOB,
    updated_at TEXT NOT NULL
);

-- The invitee's signed `admission_acceptance` for one pending join. Immutable
-- once exported: `created_at` is inside the signature, so re-signing would mint
-- a different `record_id` and could put two valid acceptances for one proposal
-- into circulation. Device-local because it is an installation-bound ceremony
-- artifact, not a team fact.
CREATE TABLE IF NOT EXISTS admission_acceptance_artifact (
    team_id BLOB NOT NULL,
    proposal_id BLOB NOT NULL,
    nonce BLOB NOT NULL,
    author_teammate_id BLOB NOT NULL,
    author_device_key_id BLOB NOT NULL,
    acceptance_record_id BLOB NOT NULL,
    acceptance_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    first_exported_at TEXT,
    PRIMARY KEY (team_id, proposal_id)
);

-- A held pause on one berth's source-use question. Device-local because it is
-- this device's own refusal to keep operating on a berth whose placement it
-- cannot resolve alone, and durable because new evidence that removes the
-- apparent disagreement must not release it: only an explicit human decision
-- does. `evidence_json` is a versioned report snapshot -- complete candidate
-- route content, provenance, live/withdrawn status and investigation outcomes
-- -- retained so shared deletion cannot destroy the local explanation or the
-- locator deliberate inspection needs. It carries no credentials.
CREATE TABLE IF NOT EXISTS berth_source_pause (
    berth_id BLOB PRIMARY KEY,
    evidence_digest BLOB NOT NULL,
    evidence_json TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

-- The human decision that ended a pause, with the evidence it was made over.
-- Not a routing override: the single surviving live allocation determines the
-- destination. Keeping the resolved snapshot means resolution does not erase
-- its own explanation.
CREATE TABLE IF NOT EXISTS berth_write_choice (
    berth_id BLOB PRIMARY KEY,
    allocation_id BLOB NOT NULL,
    evidence_digest BLOB NOT NULL,
    evidence_json TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
