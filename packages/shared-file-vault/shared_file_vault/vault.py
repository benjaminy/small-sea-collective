"""Core operations for the Small Sea Shared File Vault."""

import pathlib
import secrets
import sqlite3
import struct
import time
from datetime import datetime, timezone

from corncob.protocol import gitCmd


def uuid7():
    """Generate a UUIDv7 (time-ordered, random) as 16 bytes."""
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = secrets.token_bytes(10)
    b = struct.pack(">Q", timestamp_ms)[2:]  # 6 bytes of timestamp
    b += bytes([(0x70 | (rand_bytes[0] & 0x0F)), rand_bytes[1]])  # ver + rand_a
    b += bytes([(0x80 | (rand_bytes[2] & 0x3F))]) + rand_bytes[3:10]  # variant + rand_b
    return b


SQL_DIR = pathlib.Path(__file__).parent / "sql"


def _vault_dir(vault_root, participant_hex, team_name):
    return pathlib.Path(vault_root) / "Participants" / participant_hex / team_name


def _db_path(vault_root, participant_hex, team_name):
    return _vault_dir(vault_root, participant_hex, team_name) / "vault.db"


def _niche_git_dir(vault_root, participant_hex, team_name, niche_name):
    return _vault_dir(vault_root, participant_hex, team_name) / "Niches" / niche_name / "git"


def _connect(vault_root, participant_hex, team_name):
    db = _db_path(vault_root, participant_hex, team_name)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_vault(vault_root, participant_hex, team_name):
    """Create vault directory and initialize vault.db with schema."""
    vdir = _vault_dir(vault_root, participant_hex, team_name)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "Niches").mkdir(exist_ok=True)

    db = _db_path(vault_root, participant_hex, team_name)
    conn = sqlite3.connect(str(db))
    schema = (SQL_DIR / "vault_schema.sql").read_text()
    conn.executescript(schema)
    conn.close()
    return str(db)


def create_niche(vault_root, participant_hex, team_name, niche_name):
    """Create a new niche with a bare git repo. Returns niche id hex."""
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    git_dir.mkdir(parents=True, exist_ok=True)

    gitCmd(["init", "--bare", str(git_dir)])

    niche_id = uuid7()
    now = datetime.now(timezone.utc).isoformat()

    conn = _connect(vault_root, participant_hex, team_name)
    conn.execute(
        "INSERT INTO niche (id, name, created_at, checkout_path) VALUES (?, ?, ?, NULL)",
        (niche_id, niche_name, now),
    )
    conn.commit()
    conn.close()

    return niche_id.hex()


def checkout_niche(vault_root, participant_hex, team_name, niche_name, dest_path):
    """Check out a niche to a filesystem location using --separate-git-dir."""
    conn = _connect(vault_root, participant_hex, team_name)
    row = conn.execute("SELECT checkout_path FROM niche WHERE name = ?", (niche_name,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"Niche '{niche_name}' does not exist")
    if row["checkout_path"] is not None:
        conn.close()
        raise ValueError(f"Niche '{niche_name}' is already checked out at {row['checkout_path']}")

    dest = pathlib.Path(dest_path)
    dest.mkdir(parents=True, exist_ok=True)

    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)

    # Init a new repo with separate git dir pointing to our vault's git dir
    gitCmd(["init", f"--separate-git-dir={git_dir}", str(dest)])

    conn.execute(
        "UPDATE niche SET checkout_path = ? WHERE name = ?",
        (str(dest), niche_name),
    )
    conn.commit()
    conn.close()

    return str(dest)


def _git_dirs(vault_root, participant_hex, team_name, niche_name):
    """Return (git_dir, checkout_path) for a niche. Raises if not found or not checked out."""
    conn = _connect(vault_root, participant_hex, team_name)
    row = conn.execute(
        "SELECT checkout_path FROM niche WHERE name = ?", (niche_name,)
    ).fetchone()
    conn.close()

    if row is None:
        raise ValueError(f"Niche '{niche_name}' does not exist")
    if row["checkout_path"] is None:
        raise ValueError(f"Niche '{niche_name}' is not checked out")

    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    return str(git_dir), row["checkout_path"]


def status(vault_root, participant_hex, team_name, niche_name):
    """Get git status for a niche. Returns list of {status, path} dicts."""
    git_dir, work_tree = _git_dirs(vault_root, participant_hex, team_name, niche_name)
    result = gitCmd(
        ["--git-dir", git_dir, "--work-tree", work_tree, "status", "--porcelain"],
        raise_on_error=False,
    )

    entries = []
    for line in result.stdout.strip().splitlines():
        if line:
            st = line[:2].strip()
            path = line[3:]
            entries.append({"status": st, "path": path})
    return entries


def publish(vault_root, participant_hex, team_name, niche_name, files=None, message=None):
    """Stage and commit changes. Returns commit hash."""
    git_dir, work_tree = _git_dirs(vault_root, participant_hex, team_name, niche_name)
    git_prefix = ["--git-dir", git_dir, "--work-tree", work_tree]

    if files:
        for f in files:
            gitCmd(git_prefix + ["add", f])
    else:
        gitCmd(git_prefix + ["add", "--all"])

    if message is None:
        message = "Published changes"

    gitCmd(git_prefix + ["commit", "-m", message])

    result = gitCmd(git_prefix + ["rev-parse", "HEAD"])
    return result.stdout.strip()


def log(vault_root, participant_hex, team_name, niche_name, limit=20):
    """Get commit log. Returns list of {hash, message} dicts."""
    git_dir, work_tree = _git_dirs(vault_root, participant_hex, team_name, niche_name)
    result = gitCmd(
        ["--git-dir", git_dir, "--work-tree", work_tree, "log", "--oneline", "-n", str(limit)],
        raise_on_error=False,
    )

    entries = []
    for line in result.stdout.strip().splitlines():
        if line:
            parts = line.split(" ", 1)
            entries.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
    return entries


def list_niches(vault_root, participant_hex, team_name):
    """List all niches. Returns list of dicts."""
    conn = _connect(vault_root, participant_hex, team_name)
    rows = conn.execute("SELECT id, name, created_at, checkout_path FROM niche").fetchall()
    conn.close()

    return [
        {
            "id": row["id"].hex(),
            "name": row["name"],
            "created_at": row["created_at"],
            "checkout_path": row["checkout_path"],
        }
        for row in rows
    ]
