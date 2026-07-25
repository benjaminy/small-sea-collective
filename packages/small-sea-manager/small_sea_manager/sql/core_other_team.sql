PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teammate (
    id BLOB PRIMARY KEY,
    display_name TEXT,
    identity_public_key BLOB
);

CREATE TABLE IF NOT EXISTS app (
    id BLOB PRIMARY KEY,
    name TEXT NOT NULL
);

-- team_id is intentionally absent here: in a team DB the team is implicit.
CREATE TABLE IF NOT EXISTS team_app_berth (
    id BLOB PRIMARY KEY,
    app_id BLOB NOT NULL,
    FOREIGN KEY (app_id) REFERENCES app(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS berth_role (
    id BLOB PRIMARY KEY,
    teammate_id BLOB NOT NULL,
    berth_id BLOB NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('read-only', 'read-write')),
    FOREIGN KEY (teammate_id) REFERENCES teammate(id) ON DELETE CASCADE,
    FOREIGN KEY (berth_id) REFERENCES team_app_berth(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invitation (
    id BLOB PRIMARY KEY,
    nonce BLOB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    invitee_label TEXT,
    role TEXT NOT NULL DEFAULT 'steward',
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    accepted_by BLOB,
    acceptor_device_key_id BLOB,
    acceptor_protocol TEXT,
    acceptor_url TEXT
);

CREATE TABLE IF NOT EXISTS team_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- The quorum-gated admission flow as append-only Team Constitution records
-- (see Documentation/team-constitution.md and issue #164). All four record
-- tables below share the `integration_mode_change` envelope prefix
-- (record_id/record_type/author_*/created_at/anchor_commit/constitution_*
-- /schema_version/signature) and are NEVER mutated: admission status is a
-- computed view over which of these rows exist (see `_admission_status`),
-- not a stored column.
--
-- `admission_proposal`: the inviter's signed offer. `invitee_teammate_id` is
-- pre-allocated (the invitee is not a teammate yet, so there is no FK on it).
-- `invitee_label_commitment` is the signed PII commitment (nullable and left
-- unpopulated in this slice, per #168 -- the column fixes the schema shape).
-- `mode_plan` is the inviter's starting-mode intent as the canonical JSON
-- expansion rule {"core_mode":...,"other_mode":...} (preset vocabulary is
-- never stored -- see architecture.md). It is SIGNED into the canonical
-- bytes: finalization expands it into the invitee's granted authority, so a
-- droppable/unsigned plan would let anyone escalate a proposal undetected.
-- `invitee_label_payload` is the one separable, unsigned column excluded
-- from the canonical signed bytes: the plain label is droppable PII.
CREATE TABLE IF NOT EXISTS admission_proposal (
    record_id BLOB PRIMARY KEY,
    record_type TEXT NOT NULL DEFAULT 'admission_proposal',
    author_teammate_id BLOB NOT NULL,
    author_device_key_id BLOB NOT NULL,
    created_at TEXT NOT NULL,
    anchor_commit TEXT,
    constitution_digest BLOB NOT NULL,
    constitution_snapshot_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    nonce BLOB NOT NULL,
    team_id BLOB NOT NULL,
    invitee_teammate_id BLOB NOT NULL,
    invitee_label_commitment BLOB,
    expires_at TEXT NOT NULL,
    mode_plan TEXT NOT NULL,
    signature BLOB NOT NULL,
    invitee_label_payload TEXT,
    FOREIGN KEY (author_teammate_id) REFERENCES teammate(id) ON DELETE CASCADE
);

-- `admission_acceptance`: constructed and signed invitee-side, transported and
-- inserted verbatim inviter-side. The envelope's anchor_commit/constitution_*
-- are NULL: the invitee signs before having any standing to attest to a
-- governance view. This is the one record whose signature is verified against
-- an *embedded* key (`invitee_device_public_key`) rather than the cert graph
-- because the signer has no membership yet. `author_device_key_id` must equal
-- the key id derived from that embedded key.
CREATE TABLE IF NOT EXISTS admission_acceptance (
    record_id BLOB PRIMARY KEY,
    record_type TEXT NOT NULL DEFAULT 'admission_acceptance',
    author_teammate_id BLOB NOT NULL,
    author_device_key_id BLOB NOT NULL,
    created_at TEXT NOT NULL,
    anchor_commit TEXT,
    constitution_digest BLOB,
    constitution_snapshot_json TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    subject_record_id BLOB NOT NULL,
    nonce BLOB NOT NULL,
    invitee_device_public_key BLOB NOT NULL,
    invitee_bootstrap_key BLOB NOT NULL,
    signature BLOB NOT NULL,
    FOREIGN KEY (subject_record_id) REFERENCES admission_proposal(record_id) ON DELETE CASCADE
);

-- `endorsement`: an automatic Core integrator (UI "steward") vouching for a
-- reviewed acceptance. `subject_digest` is a content commitment (sha256) over
-- the endorsed `admission_acceptance`'s canonical bytes. The UNIQUE constraint
-- dedupes per endorsing teammate (not per device), so quorum is a plain
-- COUNT(*) and one steward's two linked devices count once.
CREATE TABLE IF NOT EXISTS endorsement (
    record_id BLOB PRIMARY KEY,
    record_type TEXT NOT NULL DEFAULT 'endorsement',
    author_teammate_id BLOB NOT NULL,
    author_device_key_id BLOB NOT NULL,
    created_at TEXT NOT NULL,
    anchor_commit TEXT,
    constitution_digest BLOB NOT NULL,
    constitution_snapshot_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    subject_record_id BLOB NOT NULL,
    subject_digest BLOB NOT NULL,
    signature BLOB NOT NULL,
    UNIQUE (subject_record_id, author_teammate_id),
    FOREIGN KEY (author_teammate_id) REFERENCES teammate(id) ON DELETE CASCADE,
    FOREIGN KEY (subject_record_id) REFERENCES admission_proposal(record_id) ON DELETE CASCADE
);

-- `finalization`: the terminal record. Only its presence makes a proposal
-- effective (drives every projection write and `_admission_status`).
-- `endorsement_count` records the observed count that met quorum.
CREATE TABLE IF NOT EXISTS finalization (
    record_id BLOB PRIMARY KEY,
    record_type TEXT NOT NULL DEFAULT 'finalization',
    author_teammate_id BLOB NOT NULL,
    author_device_key_id BLOB NOT NULL,
    created_at TEXT NOT NULL,
    anchor_commit TEXT,
    constitution_digest BLOB NOT NULL,
    constitution_snapshot_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    subject_record_id BLOB NOT NULL,
    subject_digest BLOB NOT NULL,
    endorsement_count INTEGER NOT NULL,
    signature BLOB NOT NULL,
    FOREIGN KEY (author_teammate_id) REFERENCES teammate(id) ON DELETE CASCADE,
    FOREIGN KEY (subject_record_id) REFERENCES admission_proposal(record_id) ON DELETE CASCADE
);

-- Revocation is a local disposition/projection, not one of the signed record
-- types above: an inviter deciding not to proceed. Modeling it as a signed
-- Constitution record is future work (see FOLLOW-UP). Keeping it a mutable
-- projection preserves append-only discipline on the four record tables.
CREATE TABLE IF NOT EXISTS admission_revocation (
    subject_record_id BLOB PRIMARY KEY,
    revoked_at TEXT NOT NULL,
    FOREIGN KEY (subject_record_id) REFERENCES admission_proposal(record_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_admission_acceptance_scan
    ON admission_acceptance(subject_record_id, record_id);
CREATE INDEX IF NOT EXISTS idx_endorsement_scan
    ON endorsement(subject_record_id, record_id);
CREATE INDEX IF NOT EXISTS idx_finalization_scan
    ON finalization(subject_record_id, record_id);

CREATE TABLE IF NOT EXISTS team_device (
    device_key_id BLOB PRIMARY KEY,
    teammate_id BLOB NOT NULL,
    public_key BLOB NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (teammate_id) REFERENCES teammate(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS key_certificate (
    cert_id BLOB PRIMARY KEY,
    cert_type TEXT NOT NULL,
    subject_key_id BLOB NOT NULL,
    subject_public_key BLOB NOT NULL,
    issuer_key_id BLOB NOT NULL,
    issuer_teammate_id BLOB NOT NULL,
    issued_at TEXT NOT NULL,
    claims TEXT NOT NULL,
    signature BLOB NOT NULL,
    FOREIGN KEY (issuer_teammate_id) REFERENCES teammate(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS teammate_transport_announcement (
    announcement_id BLOB PRIMARY KEY,
    teammate_id BLOB NOT NULL,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    bucket TEXT NOT NULL,
    announced_at TEXT NOT NULL,
    signer_key_id BLOB NOT NULL,
    signature BLOB NOT NULL,
    FOREIGN KEY (teammate_id) REFERENCES teammate(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS teammate_berth_storage_announcement (
    announcement_id BLOB PRIMARY KEY,
    teammate_id BLOB NOT NULL,
    berth_id BLOB NOT NULL,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    location TEXT NOT NULL,
    announced_at TEXT NOT NULL,
    signer_key_id BLOB NOT NULL,
    signature BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_teammate_berth_storage_announcement_scan
    ON teammate_berth_storage_announcement(teammate_id, berth_id, announcement_id);

-- Team Constitution record: see Documentation/team-constitution.md.
-- `constitution_digest`/`constitution_snapshot_json` are a Phase 1 stand-in
-- for the doc's target `anchor_frontier` mechanism (see that phase's
-- Archive/design-record for why): a live-query digest over current
-- teammate/device/berth-role state. The admission record tables above share
-- this same envelope and interim anchor.
CREATE TABLE IF NOT EXISTS integration_mode_change (
    record_id BLOB PRIMARY KEY,
    record_type TEXT NOT NULL DEFAULT 'integration_mode_change',
    author_teammate_id BLOB NOT NULL,
    author_device_key_id BLOB NOT NULL,
    created_at TEXT NOT NULL,
    anchor_commit TEXT,
    constitution_digest BLOB NOT NULL,
    constitution_snapshot_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    teammate_id BLOB NOT NULL,
    berth_id BLOB NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('automatic', 'proposal-only')),
    signature BLOB NOT NULL,
    FOREIGN KEY (author_teammate_id) REFERENCES teammate(id) ON DELETE CASCADE,
    FOREIGN KEY (teammate_id) REFERENCES teammate(id) ON DELETE CASCADE,
    FOREIGN KEY (berth_id) REFERENCES team_app_berth(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_integration_mode_change_scan
    ON integration_mode_change(teammate_id, berth_id, record_id);

CREATE TABLE IF NOT EXISTS device_prekey_bundle (
    device_key_id BLOB PRIMARY KEY,
    prekey_bundle_json TEXT NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY (device_key_id) REFERENCES team_device(device_key_id) ON DELETE CASCADE
);
