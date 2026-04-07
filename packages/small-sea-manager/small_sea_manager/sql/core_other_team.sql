PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS member (
    id BLOB PRIMARY KEY,
    identity_public_key BLOB,
    device_public_key BLOB
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
    member_id BLOB NOT NULL,
    berth_id BLOB NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('read-only', 'read-write')),
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE,
    FOREIGN KEY (berth_id) REFERENCES team_app_berth(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invitation (
    id BLOB PRIMARY KEY,
    nonce BLOB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    invitee_label TEXT,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    accepted_by BLOB,
    acceptor_protocol TEXT,
    acceptor_url TEXT
);

CREATE TABLE IF NOT EXISTS peer (
    id BLOB PRIMARY KEY,
    member_id BLOB NOT NULL,
    display_name TEXT,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    bucket TEXT,
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS key_certificate (
    cert_id BLOB PRIMARY KEY,
    cert_type TEXT NOT NULL,
    subject_key_id BLOB NOT NULL,
    subject_public_key BLOB NOT NULL,
    issuer_key_id BLOB NOT NULL,
    issuer_member_id BLOB NOT NULL,
    issued_at TEXT NOT NULL,
    claims TEXT NOT NULL,
    signature BLOB NOT NULL,
    FOREIGN KEY (issuer_member_id) REFERENCES member(id) ON DELETE CASCADE
);
