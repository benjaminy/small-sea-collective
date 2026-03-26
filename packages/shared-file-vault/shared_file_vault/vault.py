"""Core operations for the Small Sea Shared File Vault.

See spec.md for the design. Key points:

- One vault per device/participant. vault_root + participant_hex identify it.
- Niches are shared file trees backed by git repos, synced via Cod Sync.
- The niche registry (which niches exist per team) is itself a git repo
  synced via its own Cod Sync chain. It stores one YAML file per niche.
- Checkouts are purely local: multiple checkouts of the same niche are
  allowed; they are tracked in a local checkouts.db.
- Bundle temp files always live inside the niche/registry git dir so they
  never appear in user-visible checkout directories.
"""

import json
import os
import pathlib
import re
import secrets
import sqlite3
import struct
import time
import unicodedata
from datetime import datetime, timezone

import cod_sync.protocol as CS
from cod_sync.protocol import gitCmd


def uuid7():
    """Generate a UUIDv7 (time-ordered, random) as 16 bytes."""
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = secrets.token_bytes(10)
    b = struct.pack(">Q", timestamp_ms)[2:]  # 6 bytes of timestamp
    b += bytes([(0x70 | (rand_bytes[0] & 0x0F)), rand_bytes[1]])  # ver + rand_a
    b += bytes([0x80 | (rand_bytes[2] & 0x3F)]) + rand_bytes[3:10]  # variant + rand_b
    return b


def _canonical_name(name):
    """Normalise a niche name to NFC + casefold + slug characters only.

    Raises ValueError if the result is empty or contains invalid characters.
    Allowed: ASCII letters, digits, hyphens, underscores.
    """
    name = unicodedata.normalize("NFC", name).casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise ValueError(
            f"Niche name {name!r} is invalid after canonicalization. "
            "Use letters, digits, hyphens, and underscores only."
        )
    return name


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _participant_dir(vault_root, participant_hex):
    return pathlib.Path(vault_root) / participant_hex


def _checkouts_db_path(vault_root, participant_hex):
    return _participant_dir(vault_root, participant_hex) / "checkouts.db"


def _team_dir(vault_root, participant_hex, team_name):
    return _participant_dir(vault_root, participant_hex) / team_name


def _registry_git_dir(vault_root, participant_hex, team_name):
    return _team_dir(vault_root, participant_hex, team_name) / "registry" / "git"


def _registry_checkout_dir(vault_root, participant_hex, team_name):
    return _team_dir(vault_root, participant_hex, team_name) / "registry" / "checkout"


def _niche_git_dir(vault_root, participant_hex, team_name, niche_name):
    return _team_dir(vault_root, participant_hex, team_name) / "niches" / niche_name / "git"


def _niche_transit_dir(vault_root, participant_hex, team_name, niche_name):
    """Internal checkout used by push/pull. Never user-managed."""
    return _team_dir(vault_root, participant_hex, team_name) / "niches" / niche_name / "transit"


def _bundle_tmp_dir(git_dir):
    """Bundle temp files live inside the git dir, off all work trees."""
    return pathlib.Path(git_dir) / "codsync-bundle-tmp"


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

_CHECKOUTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkout (
    id            BLOB PRIMARY KEY,
    team_name     TEXT NOT NULL,
    niche_name    TEXT NOT NULL,
    checkout_path TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""


def _connect_checkouts(vault_root, participant_hex):
    db = _checkouts_db_path(vault_root, participant_hex)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Git work tree helpers
# ---------------------------------------------------------------------------

def _has_commits(git_dir):
    r = gitCmd(["--git-dir", str(git_dir), "rev-parse", "HEAD"], raise_on_error=False)
    return r.returncode == 0


def _make_work_tree(git_dir, dest):
    """Link dest to git_dir and populate it if the repo has commits.

    Writes a .git pointer file (same format as git init --separate-git-dir).
    Does not touch git_dir's config, so multiple work trees can coexist.
    """
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".git").write_text(f"gitdir: {pathlib.Path(git_dir).resolve()}\n")
    if _has_commits(git_dir):
        gitCmd([
            "--git-dir", str(git_dir), "--work-tree", str(dest),
            "checkout", "HEAD", "--", ".",
        ])


def _refresh_work_tree(git_dir, dest):
    """Update dest to match HEAD, leaving untracked files alone.

    Uses 'checkout HEAD -- .' which overwrites tracked files that differ
    from HEAD but does not remove files that HEAD doesn't know about.
    Silently skips if dest does not exist.
    """
    dest = pathlib.Path(dest)
    if not dest.exists():
        return
    gitCmd([
        "--git-dir", str(git_dir), "--work-tree", str(dest),
        "checkout", "HEAD", "--", ".",
    ])


# ---------------------------------------------------------------------------
# Registry helpers (internal)
# ---------------------------------------------------------------------------

def _init_git_dir(git_dir):
    """Initialise a git dir that supports attached work trees.

    Uses --bare for the layout (no working tree files at the root) but
    immediately sets core.bare = false so that 'git checkout' and other
    work-tree commands succeed when run from a linked work tree.
    """
    gitCmd(["init", "--bare", str(git_dir)])
    gitCmd(["--git-dir", str(git_dir), "config", "core.bare", "false"])


def _ensure_registry(vault_root, participant_hex, team_name):
    """Lazily create the registry git repo and checkout for a team."""
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)
    if not checkout.exists():
        _make_work_tree(git_dir, checkout)


# ---------------------------------------------------------------------------
# Cod Sync push/pull primitives
# ---------------------------------------------------------------------------

def _cod_push(git_dir, transit, cloud_dir):
    saved = os.getcwd()
    try:
        os.chdir(transit)
        cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir))
        cod.remote = CS.LocalFolderRemote(str(cloud_dir))
        cod.push_to_remote(["main"])
    finally:
        os.chdir(saved)


def _cod_pull(git_dir, transit, cloud_dir):
    """Fetch from cloud_dir and merge into git_dir via transit work tree."""
    saved = os.getcwd()
    try:
        os.chdir(transit)
        cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir))
        cod.remote = CS.LocalFolderRemote(str(cloud_dir))

        if _has_commits(git_dir):
            # Sync transit work tree to HEAD before merging.  Without this,
            # files committed from a user checkout (not the transit) would
            # appear as uncommitted deletions in the transit and block the merge.
            gitCmd(["checkout", "HEAD", "--", "."])

            [bundle_remote, path_tmp] = cod.bundle_tmp()
            check = gitCmd(["remote", "get-url", bundle_remote], raise_on_error=False)
            if check.returncode != 0:
                os.makedirs(path_tmp, exist_ok=True)
                gitCmd(["remote", "add", bundle_remote, f"{path_tmp}/fetch.bundle"])
            cod.fetch_from_remote(["main"])
            cod.merge_from_remote(["main"])
        else:
            cod.add_remote(f"file://{cloud_dir}", [])
            cod.fetch_from_remote(["main"])
            gitCmd(["checkout", "main"])
    finally:
        os.chdir(saved)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_vault(vault_root, participant_hex):
    """Create the vault directory and local checkout registry database."""
    pdir = _participant_dir(vault_root, participant_hex)
    pdir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_checkouts_db_path(vault_root, participant_hex)))
    conn.executescript(_CHECKOUTS_SCHEMA)
    conn.close()


def create_niche(vault_root, participant_hex, team_name, niche_name):
    """Create a niche and record it in the team's shared registry.

    Creates the niche git repo locally. The registry entry propagates to
    teammates on the next push_registry call.

    niche_name is canonicalized (NFC + casefold + slug) before use.
    """
    niche_name = _canonical_name(niche_name)
    _ensure_registry(vault_root, participant_hex, team_name)

    # Create niche git repo and transit work tree
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)

    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
    if not transit.exists():
        _make_work_tree(git_dir, transit)

    # Write niche record to registry checkout and commit
    registry_git = _registry_git_dir(vault_root, participant_hex, team_name)
    registry_co = _registry_checkout_dir(vault_root, participant_hex, team_name)
    git_prefix = ["--git-dir", str(registry_git), "--work-tree", str(registry_co)]

    niche_id = uuid7().hex()
    record = {
        "id": niche_id,
        "name": niche_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # On the very first niche, also commit .gitattributes so that concurrent
    # additions of the same-named niche produce explicit conflicts while
    # additions of different niches (different filenames) auto-merge cleanly.
    if not _has_commits(registry_git):
        (registry_co / ".gitattributes").write_text("*.json merge=binary\n")
        gitCmd(git_prefix + ["add", ".gitattributes"])

    (registry_co / f"{niche_name}.json").write_text(json.dumps(record, indent=2))
    gitCmd(git_prefix + ["add", f"{niche_name}.json"])
    gitCmd(git_prefix + ["commit", "-m", f"add niche {niche_name}"])

    return niche_id


def list_niches(vault_root, participant_hex, team_name):
    """List all niches known to this participant for a team.

    Reads from the local registry checkout, which reflects whatever has
    been pulled from the shared registry chain.
    """
    _ensure_registry(vault_root, participant_hex, team_name)
    registry_co = _registry_checkout_dir(vault_root, participant_hex, team_name)
    niches = []
    for f in sorted(registry_co.glob("*.json")):
        data = json.loads(f.read_text())
        if data:
            niches.append(data)
    return niches


def add_checkout(vault_root, participant_hex, team_name, niche_name, dest_path):
    """Register a local directory as a checkout of a niche.

    Multiple checkouts of the same niche are allowed.
    If the niche already has commits, dest_path is populated immediately.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    if not git_dir.exists():
        raise ValueError(f"Niche '{niche_name}' does not exist in team '{team_name}'")

    _make_work_tree(git_dir, dest_path)

    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        "INSERT INTO checkout (id, team_name, niche_name, checkout_path, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            uuid7(),
            team_name,
            niche_name,
            str(pathlib.Path(dest_path)),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def remove_checkout(vault_root, participant_hex, team_name, niche_name, checkout_path):
    """Unregister a checkout. Does not delete files in the directory."""
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        "DELETE FROM checkout WHERE team_name = ? AND niche_name = ? AND checkout_path = ?",
        (team_name, niche_name, str(pathlib.Path(checkout_path))),
    )
    conn.commit()
    conn.close()


def list_checkouts(vault_root, participant_hex, team_name, niche_name):
    """Return list of checkout paths registered for a niche."""
    conn = _connect_checkouts(vault_root, participant_hex)
    rows = conn.execute(
        "SELECT checkout_path FROM checkout WHERE team_name = ? AND niche_name = ?",
        (team_name, niche_name),
    ).fetchall()
    conn.close()
    return [row["checkout_path"] for row in rows]


def publish(vault_root, participant_hex, team_name, niche_name, checkout_path,
            files=None, message=None):
    """Stage changes in a checkout and commit. Returns commit hash.

    After committing, refreshes all other registered checkouts of this
    niche so they reflect the new HEAD.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    checkout = pathlib.Path(checkout_path).resolve()
    git_prefix = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]

    if files:
        for f in files:
            gitCmd(git_prefix + ["add", f])
    else:
        gitCmd(git_prefix + ["add", "--all"])

    gitCmd(git_prefix + ["commit", "-m", message or "Published changes"])

    result = gitCmd(git_prefix + ["rev-parse", "HEAD"])
    head = result.stdout.strip()

    # Refresh sibling checkouts
    for other in list_checkouts(vault_root, participant_hex, team_name, niche_name):
        if pathlib.Path(other).resolve() != checkout:
            _refresh_work_tree(git_dir, other)

    return head


def status(vault_root, participant_hex, team_name, niche_name, checkout_path):
    """Get git status for a checkout. Returns list of {status, path} dicts."""
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    checkout = pathlib.Path(checkout_path)
    result = gitCmd(
        ["--git-dir", str(git_dir), "--work-tree", str(checkout),
         "status", "--porcelain"],
        raise_on_error=False,
    )
    entries = []
    for line in result.stdout.strip().splitlines():
        if line:
            entries.append({"status": line[:2].strip(), "path": line[3:]})
    return entries


def log(vault_root, participant_hex, team_name, niche_name, limit=20):
    """Get commit log for a niche. Returns list of {hash, message} dicts."""
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    result = gitCmd(
        ["--git-dir", str(git_dir), "log", "--oneline", "-n", str(limit)],
        raise_on_error=False,
    )
    entries = []
    for line in result.stdout.strip().splitlines():
        if line:
            parts = line.split(" ", 1)
            entries.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
    return entries


def push_registry(vault_root, participant_hex, team_name, cloud_dir):
    """Push the niche registry to a cloud directory via Cod Sync."""
    _ensure_registry(vault_root, participant_hex, team_name)
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    _cod_push(git_dir, checkout, cloud_dir)


def pull_registry(vault_root, participant_hex, team_name, cloud_dir):
    """Pull the niche registry from a cloud directory and merge."""
    _ensure_registry(vault_root, participant_hex, team_name)
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    _cod_pull(git_dir, checkout, cloud_dir)


def push_niche(vault_root, participant_hex, team_name, niche_name, cloud_dir):
    """Push a niche to a cloud directory via Cod Sync."""
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
    _cod_push(git_dir, transit, cloud_dir)


def list_teams(vault_root, participant_hex):
    """List team names that have a local registry in this vault."""
    pdir = _participant_dir(vault_root, participant_hex)
    if not pdir.exists():
        return []
    return [
        d.name
        for d in sorted(pdir.iterdir())
        if d.is_dir() and (d / "registry" / "git").exists()
    ]


def pull_niche(vault_root, participant_hex, team_name, niche_name, cloud_dir):
    """Pull a niche from a cloud directory and merge.

    Creates the niche git repo if this is the first time pulling it
    (e.g. a new team member joining). Updates all registered checkouts
    after a successful merge.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)

    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
    if not transit.exists():
        _make_work_tree(git_dir, transit)

    _cod_pull(git_dir, transit, cloud_dir)

    # Refresh all user checkouts
    for checkout in list_checkouts(vault_root, participant_hex, team_name, niche_name):
        _refresh_work_tree(git_dir, checkout)
