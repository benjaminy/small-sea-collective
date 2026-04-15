"""Core operations for the Small Sea Shared File Vault.

See spec.md for the design. Key points:

- One vault per device/participant. vault_root + participant_hex identify it.
- Niches are shared file trees backed by git repos, synced via Cod Sync.
- The niche registry (which niches exist per team) is itself a git repo
  synced via its own Cod Sync chain. It stores one JSON file per niche.
- Each niche has at most one local checkout per device, tracked in checkouts.db.
- Bundle temp files always live inside the niche/registry git dir so they
  never appear in user-visible checkout directories.
"""

import enum
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


class NicheResidency(enum.Enum):
    """How much of a niche exists locally on this device.

    Residency is about local materialization, not sync freshness. A niche can
    be CACHED or CHECKED_OUT and still be behind a teammate.

    REMOTE_ONLY  — No niche git dir on this device. The niche may still be
                   known via the shared registry.
    CACHED       — The niche git dir exists locally, possibly with fetched
                   refs or committed history, but no checkout is registered.
    CHECKED_OUT  — The niche git dir exists and a checkout is registered in
                   checkouts.db.
    """

    REMOTE_ONLY = "remote_only"
    CACHED = "cached"
    CHECKED_OUT = "checked_out"


class MergeConflictError(RuntimeError):
    """Raised when a pull leaves unresolved merge conflicts."""

    def __init__(self, paths):
        self.paths = paths
        super().__init__("Merge conflict during pull")


class DuplicateCheckoutError(ValueError):
    """Raised when trying to add a checkout for a niche that already has one.

    Remove the existing checkout before attaching a new location.
    """

    def __init__(self, team_name, niche_name, existing_path):
        self.team_name = team_name
        self.niche_name = niche_name
        self.existing_path = existing_path
        super().__init__(
            f"Niche '{niche_name}' in team '{team_name}' already has a checkout at "
            f"'{existing_path}'. Remove it before attaching a new one."
        )


class DirtyCheckoutError(RuntimeError):
    """Raised when a merge operation finds uncommitted changes in the user's checkout.

    Both tracked modifications and untracked files are treated as dirty.
    The checkout must be fully clean before integrating changes from teammates.
    This is intentional: non-git users should not need to understand the
    tracked/untracked distinction, and hiding untracked files could mask
    path-collision cases during merge.
    """

    def __init__(self, paths):
        self.paths = paths
        super().__init__(
            "Checkout has uncommitted changes. Publish or discard them before merging.\n"
            + "".join(f"  {p}\n" for p in paths)
        )


class NoCheckoutError(RuntimeError):
    """Raised when a merge operation is attempted but no checkout is attached.

    Fetch can still run without a checkout. Merge requires a checkout to
    exist so that fetched changes can be written to a visible location.

    The ``residency`` field indicates why there is no checkout:
    - NicheResidency.REMOTE_ONLY: no local niche data at all; run
      ``fetch_niche`` first, then attach a checkout.
    - NicheResidency.CACHED: the niche is fetched locally; attach a checkout
      directly, then merge.
    """

    def __init__(self, team_name, niche_name, residency=None):
        self.team_name = team_name
        self.niche_name = niche_name
        self.residency = residency
        if residency is NicheResidency.REMOTE_ONLY:
            detail = (
                "The niche has no local data yet. "
                "Run fetch_niche first to download it, then attach a checkout before merging."
            )
        else:
            detail = "Attach a checkout before merging."
        super().__init__(
            f"Niche '{niche_name}' in team '{team_name}' has no local checkout. {detail}"
        )


class StaleCheckoutError(RuntimeError):
    """Raised when the registered checkout directory no longer exists on disk.

    The checkout registration is still in the database, but the directory it
    points to has been moved or deleted. Remove the stale registration and
    re-attach at the correct path.
    """

    def __init__(self, team_name, niche_name, checkout_path):
        self.team_name = team_name
        self.niche_name = niche_name
        self.checkout_path = checkout_path
        super().__init__(
            f"Registered checkout '{checkout_path}' for niche '{niche_name}' in team "
            f"'{team_name}' no longer exists on disk. "
            "Remove the stale registration and re-attach at the correct path."
        )


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

_CHECKOUTS_DB_VERSION = 1

_CHECKOUTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS checkout (
    id            BLOB PRIMARY KEY,
    team_name     TEXT NOT NULL,
    niche_name    TEXT NOT NULL,
    checkout_path TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (team_name, niche_name)
);
CREATE TABLE IF NOT EXISTS peer_sync (
    team_name        TEXT NOT NULL,
    repo_kind        TEXT NOT NULL,
    niche_name       TEXT NOT NULL,
    member_id        TEXT NOT NULL,
    last_fetched_sha TEXT,
    last_merged_sha  TEXT,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (team_name, repo_kind, niche_name, member_id)
);
"""


def _connect_checkouts(vault_root, participant_hex):
    db = _checkouts_db_path(vault_root, participant_hex)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    # Check schema version; recreate the DB if stale. checkouts.db is
    # device-local and reconstructable, so recreation is safe.
    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        version = row[0] if row else None
    except sqlite3.OperationalError:
        version = None

    if version != _CHECKOUTS_DB_VERSION:
        conn.close()
        db.unlink(missing_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

    conn.executescript(_CHECKOUTS_SCHEMA)

    if not conn.execute("SELECT 1 FROM schema_version LIMIT 1").fetchone():
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (_CHECKOUTS_DB_VERSION,)
        )
        conn.commit()

    return conn


def _peer_sync_niche_key(repo_kind, niche_name=None):
    if repo_kind == "registry":
        return ""
    return niche_name or ""


def _record_peer_fetch(
    vault_root,
    participant_hex,
    team_name,
    repo_kind,
    niche_name,
    member_id,
    fetched_sha,
):
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        """
        INSERT INTO peer_sync (
            team_name, repo_kind, niche_name, member_id,
            last_fetched_sha, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(team_name, repo_kind, niche_name, member_id)
        DO UPDATE SET
            last_fetched_sha = excluded.last_fetched_sha,
            updated_at = excluded.updated_at
        """,
        (
            team_name,
            repo_kind,
            _peer_sync_niche_key(repo_kind, niche_name),
            member_id,
            fetched_sha,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def _record_peer_merge(
    vault_root,
    participant_hex,
    team_name,
    repo_kind,
    niche_name,
    member_id,
    merged_sha,
):
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        """
        INSERT INTO peer_sync (
            team_name, repo_kind, niche_name, member_id,
            last_fetched_sha, last_merged_sha, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(team_name, repo_kind, niche_name, member_id)
        DO UPDATE SET
            last_fetched_sha = excluded.last_fetched_sha,
            last_merged_sha = excluded.last_merged_sha,
            updated_at = excluded.updated_at
        """,
        (
            team_name,
            repo_kind,
            _peer_sync_niche_key(repo_kind, niche_name),
            member_id,
            merged_sha,
            merged_sha,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def _peer_sync_row(vault_root, participant_hex, team_name, repo_kind, niche_name, member_id):
    conn = _connect_checkouts(vault_root, participant_hex)
    row = conn.execute(
        """
        SELECT team_name, repo_kind, niche_name, member_id, last_fetched_sha, last_merged_sha
        FROM peer_sync
        WHERE team_name = ? AND repo_kind = ? AND niche_name = ? AND member_id = ?
        """,
        (team_name, repo_kind, _peer_sync_niche_key(repo_kind, niche_name), member_id),
    ).fetchone()
    conn.close()
    return row


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


def _is_checkout_clean(checkout_path, git_dir):
    """Return True if the checkout has no tracked or untracked changes.

    Returns False — rather than raising — if the checkout directory does not
    exist on disk or if git exits non-zero for any reason. This prevents a
    missing or unreadable checkout from being mistaken for a clean one.

    Uses 'git status --porcelain' which reports both tracked modifications
    and untracked files. Untracked files block merge operations just as
    tracked changes do — this is intentional: non-git users should not need
    to understand the tracked/untracked distinction, and hiding untracked
    files from the check could mask path-collision cases during merge.

    Always called with the user's checkout_path, never with the transit
    work tree (transit always resets itself to HEAD before use, so checking
    it would silently pass even when the user's checkout is dirty).
    """
    if not pathlib.Path(checkout_path).exists():
        return False
    result = gitCmd(
        [
            "--git-dir", str(git_dir), "--work-tree", str(checkout_path),
            "status", "--porcelain",
        ],
        raise_on_error=False,
    )
    return result.returncode == 0 and result.stdout.strip() == ""


def _conflict_paths(git_dir, work_tree):
    """Return unresolved merge-conflict paths for a git/work tree pair."""
    result = gitCmd(
        [
            "--git-dir", str(git_dir), "--work-tree", str(work_tree),
            "diff", "--name-only", "--diff-filter=U",
        ],
        raise_on_error=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _resolve_ref(git_dir, work_tree, ref_name):
    result = gitCmd(
        ["--git-dir", str(git_dir), "--work-tree", str(work_tree), "rev-parse", "--verify", ref_name],
        raise_on_error=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _is_ancestor(git_dir, work_tree, maybe_ancestor, descendant="HEAD"):
    result = gitCmd(
        [
            "--git-dir", str(git_dir), "--work-tree", str(work_tree),
            "merge-base", "--is-ancestor", maybe_ancestor, descendant,
        ],
        raise_on_error=False,
    )
    return result.returncode == 0


def _peer_ref_name(member_id, branch="main"):
    return f"refs/peers/{member_id}/{branch}"


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

def _cod_push(git_dir, transit, remote):
    saved = os.getcwd()
    try:
        os.chdir(transit)
        cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir))
        cod.remote = remote
        cod.push_to_remote(["main"])
    finally:
        os.chdir(saved)


def _cod_pull(git_dir, transit, remote):
    """Fetch from remote and merge into git_dir via transit work tree."""
    saved = os.getcwd()
    try:
        os.chdir(transit)
        cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir))
        cod.remote = remote

        has_commits = _has_commits(git_dir)
        if has_commits:
            # Sync transit work tree to HEAD before merging.  Without this,
            # files committed from a user checkout (not the transit) would
            # appear as uncommitted deletions in the transit and block the merge.
            gitCmd(["checkout", "HEAD", "--", "."])

        fetch_result = cod.fetch_from_remote(["main"])
        if fetch_result is None:
            raise RuntimeError("pull failed: could not fetch from remote")

        if has_commits:
            exit_code = cod.merge_from_remote(["main"])
            if exit_code != 0:
                raise MergeConflictError(_conflict_paths(git_dir, transit))
        else:
            gitCmd(["checkout", "main"])
    finally:
        os.chdir(saved)


def _cod_fetch(git_dir, transit, remote, pin_to_ref):
    saved = os.getcwd()
    try:
        os.chdir(transit)
        cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir))
        cod.remote = remote
        return cod.fetch_from_remote(["main"], pin_to_ref=pin_to_ref)
    finally:
        os.chdir(saved)


def _cod_merge_ref(git_dir, transit, ref_name):
    saved = os.getcwd()
    try:
        os.chdir(transit)
        cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir))

        has_commits = _has_commits(git_dir)
        if has_commits:
            # Keep the transit work tree aligned with HEAD before merging.
            gitCmd(["checkout", "HEAD", "--", "."])

        if has_commits:
            exit_code = cod.merge_from_ref(ref_name)
            if exit_code != 0:
                raise MergeConflictError(_conflict_paths(git_dir, transit))
        else:
            gitCmd(["checkout", "-B", "main", ref_name])
    finally:
        os.chdir(saved)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_vault(vault_root, participant_hex):
    """Create the vault directory and local checkout registry database."""
    pdir = _participant_dir(vault_root, participant_hex)
    pdir.mkdir(parents=True, exist_ok=True)
    conn = _connect_checkouts(vault_root, participant_hex)
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

    Each returned dict includes a ``"residency"`` key with the string value
    of the niche's NicheResidency on this device, computed on read.
    """
    _ensure_registry(vault_root, participant_hex, team_name)
    registry_co = _registry_checkout_dir(vault_root, participant_hex, team_name)
    niches = []
    for f in sorted(registry_co.glob("*.json")):
        data = json.loads(f.read_text())
        if data:
            data["residency"] = niche_residency(
                vault_root, participant_hex, team_name, data["name"]
            ).value
            niches.append(data)
    return niches


def add_checkout(vault_root, participant_hex, team_name, niche_name, dest_path):
    """Register a local directory as the checkout of a niche.

    Each niche may have at most one checkout on a device. Raises
    DuplicateCheckoutError if one is already registered. Remove the existing
    checkout before attaching a new location.

    If the niche already has commits, dest_path is populated immediately.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    if not git_dir.exists():
        raise ValueError(f"Niche '{niche_name}' does not exist in team '{team_name}'")

    existing = get_checkout(vault_root, participant_hex, team_name, niche_name)
    if existing is not None:
        raise DuplicateCheckoutError(team_name, niche_name, existing)

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


def get_checkout(vault_root, participant_hex, team_name, niche_name):
    """Return the single registered checkout path for a niche, or None."""
    conn = _connect_checkouts(vault_root, participant_hex)
    row = conn.execute(
        "SELECT checkout_path FROM checkout WHERE team_name = ? AND niche_name = ?",
        (team_name, niche_name),
    ).fetchone()
    conn.close()
    return row["checkout_path"] if row else None


def list_checkouts(vault_root, participant_hex, team_name, niche_name):
    """Return list of checkout paths for a niche (at most one element)."""
    checkout = get_checkout(vault_root, participant_hex, team_name, niche_name)
    return [checkout] if checkout is not None else []


def niche_residency(vault_root, participant_hex, team_name, niche_name):
    """Return the NicheResidency for a niche on this device.

    REMOTE_ONLY  — no local git dir exists (niche known only from registry).
    CACHED       — local git dir exists but no checkout is registered.
    CHECKED_OUT  — local git dir exists and a checkout is registered.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    if not git_dir.exists():
        return NicheResidency.REMOTE_ONLY
    checkout = get_checkout(vault_root, participant_hex, team_name, niche_name)
    if checkout is None:
        return NicheResidency.CACHED
    return NicheResidency.CHECKED_OUT


def publish(vault_root, participant_hex, team_name, niche_name, checkout_path,
            files=None, message=None):
    """Stage changes in a checkout and commit. Returns commit hash."""
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
    return result.stdout.strip()


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


def push_registry(vault_root, participant_hex, team_name, remote):
    """Push the niche registry to cloud storage via Cod Sync."""
    _ensure_registry(vault_root, participant_hex, team_name)
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    _cod_push(git_dir, checkout, remote)


def pull_registry(vault_root, participant_hex, team_name, remote):
    """Pull the niche registry from cloud storage and merge."""
    _ensure_registry(vault_root, participant_hex, team_name)
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    _cod_pull(git_dir, checkout, remote)


def fetch_registry(vault_root, participant_hex, team_name, member_id, remote):
    """Fetch the registry from a peer and pin it to a durable local ref."""
    _ensure_registry(vault_root, participant_hex, team_name)
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    ref_name = _peer_ref_name(member_id)
    fetched_sha = _cod_fetch(git_dir, checkout, remote, ref_name)
    if fetched_sha is not None:
        _record_peer_fetch(
            vault_root, participant_hex, team_name, "registry", None, member_id, fetched_sha
        )
    return fetched_sha


def merge_registry(vault_root, participant_hex, team_name, member_id):
    """Merge a previously parked registry ref from a peer."""
    _ensure_registry(vault_root, participant_hex, team_name)
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    ref_name = _peer_ref_name(member_id)
    parked_sha = _resolve_ref(git_dir, checkout, ref_name)
    if parked_sha is None:
        return None
    if _has_commits(git_dir) and _is_ancestor(git_dir, checkout, parked_sha, "HEAD"):
        _record_peer_merge(
            vault_root, participant_hex, team_name, "registry", None, member_id, parked_sha
        )
        return parked_sha
    _cod_merge_ref(git_dir, checkout, ref_name)
    _record_peer_merge(
        vault_root, participant_hex, team_name, "registry", None, member_id, parked_sha
    )
    return parked_sha


def push_niche(vault_root, participant_hex, team_name, niche_name, remote):
    """Push a niche to cloud storage via Cod Sync."""
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
    _cod_push(git_dir, transit, remote)


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


def _require_clean_checkout(vault_root, participant_hex, team_name, niche_name):
    """Verify a checkout is attached, exists on disk, and is clean.

    Returns the checkout path. Raises NoCheckoutError, ValueError (missing
    path), or DirtyCheckoutError as appropriate. Called by pull_niche and
    merge_niche before any transit operations so that transit's always-clean
    state is never mistakenly used as the check target.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    checkout = get_checkout(vault_root, participant_hex, team_name, niche_name)
    if checkout is None:
        residency = niche_residency(vault_root, participant_hex, team_name, niche_name)
        raise NoCheckoutError(team_name, niche_name, residency)
    if not pathlib.Path(checkout).exists():
        raise StaleCheckoutError(team_name, niche_name, checkout)
    if not _is_checkout_clean(checkout, git_dir):
        dirty = [e["path"] for e in status(vault_root, participant_hex, team_name, niche_name, checkout)]
        raise DirtyCheckoutError(dirty)
    return checkout


def pull_niche(vault_root, participant_hex, team_name, niche_name, remote):
    """Pull a niche from cloud storage and merge.

    Requires a checkout to be attached and clean. This keeps pull semantics
    consistent with merge: visible files are only updated through an explicit
    action, never automatically on a subsequent add_checkout call.

    For the initial join flow (no checkout yet), use fetch_niche to park the
    remote content, then add_checkout, then merge_niche.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)

    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
    if not transit.exists():
        _make_work_tree(git_dir, transit)

    checkout = _require_clean_checkout(vault_root, participant_hex, team_name, niche_name)

    _cod_pull(git_dir, transit, remote)
    _refresh_work_tree(git_dir, checkout)


def fetch_niche(vault_root, participant_hex, team_name, niche_name, member_id, remote):
    """Fetch a niche from a peer and pin it to a durable local ref.

    Fetch does not modify the user's checkout, so no clean-checkout guard
    is needed here. The guard fires at merge time.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)

    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
    if not transit.exists():
        _make_work_tree(git_dir, transit)

    ref_name = _peer_ref_name(member_id)
    fetched_sha = _cod_fetch(git_dir, transit, remote, ref_name)
    if fetched_sha is not None:
        _record_peer_fetch(
            vault_root, participant_hex, team_name, "niche", niche_name, member_id, fetched_sha
        )
    return fetched_sha


def merge_niche(vault_root, participant_hex, team_name, niche_name, member_id):
    """Merge a previously parked niche ref from a peer.

    Requires a checkout to be attached (raises NoCheckoutError if none).
    Requires the checkout to be clean (raises DirtyCheckoutError if not).
    The clean-checkout guard fires here, before any transit operations,
    because transit always resets to HEAD and would silently pass the check.
    """
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)

    checkout = _require_clean_checkout(vault_root, participant_hex, team_name, niche_name)

    ref_name = _peer_ref_name(member_id)
    parked_sha = _resolve_ref(git_dir, transit, ref_name)
    if parked_sha is None:
        return None
    if _has_commits(git_dir) and _is_ancestor(git_dir, transit, parked_sha, "HEAD"):
        _record_peer_merge(
            vault_root, participant_hex, team_name, "niche", niche_name, member_id, parked_sha
        )
        return parked_sha
    _cod_merge_ref(git_dir, transit, ref_name)
    _record_peer_merge(
        vault_root, participant_hex, team_name, "niche", niche_name, member_id, parked_sha
    )

    _refresh_work_tree(git_dir, checkout)
    return parked_sha


def registry_conflict_paths(vault_root, participant_hex, team_name):
    """Return unresolved conflict paths for the team's registry repo."""
    git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
    checkout = _registry_checkout_dir(vault_root, participant_hex, team_name)
    return _conflict_paths(git_dir, checkout)


def niche_conflict_paths(vault_root, participant_hex, team_name, niche_name):
    """Return unresolved conflict paths for a niche repo's transit work tree."""
    git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
    transit = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
    return _conflict_paths(git_dir, transit)


def peer_update_status(
    vault_root, participant_hex, team_name, repo_kind, niche_name, member_id
):
    """Return parked-ref status for one peer/repo pair."""
    if repo_kind == "registry":
        _ensure_registry(vault_root, participant_hex, team_name)
        git_dir = _registry_git_dir(vault_root, participant_hex, team_name)
        work_tree = _registry_checkout_dir(vault_root, participant_hex, team_name)
    else:
        git_dir = _niche_git_dir(vault_root, participant_hex, team_name, niche_name)
        work_tree = _niche_transit_dir(vault_root, participant_hex, team_name, niche_name)
        if not git_dir.exists() or not work_tree.exists():
            row = _peer_sync_row(
                vault_root, participant_hex, team_name, repo_kind, niche_name, member_id
            )
            return {
                "member_id": member_id,
                "repo_kind": repo_kind,
                "parked_ref": _peer_ref_name(member_id),
                "parked_sha": None,
                "ready_to_merge": False,
                "already_merged": False,
                "last_fetched_sha": row["last_fetched_sha"] if row else None,
                "last_merged_sha": row["last_merged_sha"] if row else None,
            }

    ref_name = _peer_ref_name(member_id)
    parked_sha = _resolve_ref(git_dir, work_tree, ref_name)
    row = _peer_sync_row(vault_root, participant_hex, team_name, repo_kind, niche_name, member_id)
    if parked_sha is None:
        return {
            "member_id": member_id,
            "repo_kind": repo_kind,
            "parked_ref": ref_name,
            "parked_sha": None,
            "ready_to_merge": False,
            "already_merged": False,
            "last_fetched_sha": row["last_fetched_sha"] if row else None,
            "last_merged_sha": row["last_merged_sha"] if row else None,
        }

    already_merged = _has_commits(git_dir) and _is_ancestor(git_dir, work_tree, parked_sha, "HEAD")
    return {
        "member_id": member_id,
        "repo_kind": repo_kind,
        "parked_ref": ref_name,
        "parked_sha": parked_sha,
        "ready_to_merge": not already_merged,
        "already_merged": already_merged,
        "last_fetched_sha": row["last_fetched_sha"] if row else None,
        "last_merged_sha": row["last_merged_sha"] if row else None,
    }
