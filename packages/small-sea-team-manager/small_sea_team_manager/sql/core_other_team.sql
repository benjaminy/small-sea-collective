PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS member (
    id BLOB PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS member_cloud (
    id BLOB PRIMARY KEY,
    member_id BLOB NOT NULL,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    access_key TEXT,
    secret_key TEXT,
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invitation (
    id BLOB PRIMARY KEY,
    nonce BLOB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    invitee_label TEXT,
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    accepted_by BLOB,
    acceptor_protocol TEXT,
    acceptor_url TEXT,
    acceptor_access_key TEXT,
    acceptor_secret_key TEXT
);

CREATE TABLE IF NOT EXISTS peer (
    id BLOB PRIMARY KEY,
    member_id BLOB NOT NULL,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    access_key TEXT,
    secret_key TEXT,
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
);
