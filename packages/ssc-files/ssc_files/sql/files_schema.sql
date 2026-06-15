PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS niche (
    id BLOB PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    checkout_path TEXT
);
