PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suid BLOB NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    duration_sec INTEGER,
    participant TEXT,
    app TEXT,
    team TEXT,
    client TEXT
);
