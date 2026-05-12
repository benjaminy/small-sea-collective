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
import pathlib
import re
import secrets
import sqlite3
import struct
import time
import unicodedata
from dataclasses import dataclass
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


@dataclass(frozen=True)
class VaultMaterializationContext:
    """Vault-owned local materialization coordinates for one team."""

    participant_hex: str
    team_id: str
    team_name: str
    app_name: str = "SharedFileVault"

    def __str__(self):
        return self.team_name

    @classmethod
    def from_session_info(cls, info):
        missing = [
            key for key in ("participant_hex", "berth_id", "team_name", "app_name")
            if not info.get(key)
        ]
        if missing:
            raise ValueError(
                "Hub session_info missing required Vault materialization field(s): "
                + ", ".join(missing)
            )
        if info.get("app_name") != "SharedFileVault":
            raise ValueError(
                "Hub session_info app_name must be 'SharedFileVault' for Vault "
                f"materialization, got {info.get('app_name')!r}"
            )
        return cls(
            participant_hex=str(info["participant_hex"]),
            team_id=str(info["berth_id"]),
            team_name=str(info["team_name"]),
            app_name=str(info["app_name"]),
        )

def materialization_context_from_session_info(info):
    return VaultMaterializationContext.from_session_info(info)


def _validate_context(participant_hex, context):
    if not isinstance(context, VaultMaterializationContext):
        raise TypeError(
            "Vault operations require a VaultMaterializationContext; "
            f"got {type(context).__name__}"
        )
    if context.participant_hex != participant_hex:
        raise ValueError(
            "Vault materialization context participant "
            f"{context.participant_hex!r} does not match requested "
            f"participant {participant_hex!r}"
        )
    return context


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
    return pathlib.Path(vault_root) / "participants" / participant_hex


def _checkouts_db_path(vault_root, participant_hex):
    return _participant_dir(vault_root, participant_hex) / "checkouts.db"


def _team_dir(vault_root, context):
    return _participant_dir(vault_root, context.participant_hex) / "teams" / context.team_id


def _team_metadata_path(vault_root, context):
    return _team_dir(vault_root, context) / "metadata.json"


def _write_team_metadata(vault_root, context):
    path = _team_metadata_path(vault_root, context)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "team_id": context.team_id,
                "team_name": context.team_name,
                "app_name": context.app_name,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def materialize_team(vault_root, context):
    """Record a team as locally materialized.

    Writes metadata.json for the team so subsequent offline name→id resolution
    can find it. Called once at login time, after the Hub session has supplied
    team_id via session_info. Idempotent: rewriting with the same content is
    a no-op for path resolvers.
    """
    _validate_context(context.participant_hex, context)
    _write_team_metadata(vault_root, context)


def iter_materialized_teams(vault_root, participant_hex):
    """Yield VaultMaterializationContext for every team materialized for this participant.

    Entries whose metadata.json is missing, malformed, or fails Vault's
    integrity rules (app_name must be SharedFileVault, team_id must match the
    directory name, team_name must be non-empty) are skipped silently.
    """
    teams_dir = _participant_dir(vault_root, participant_hex) / "teams"
    if not teams_dir.exists():
        return
    for team_dir in sorted(teams_dir.iterdir()):
        metadata_path = team_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            data = json.loads(metadata_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("app_name") != "SharedFileVault":
            continue
        if data.get("team_id") != team_dir.name:
            continue
        team_name = data.get("team_name")
        if not team_name:
            continue
        yield VaultMaterializationContext(
            participant_hex=participant_hex,
            team_id=team_dir.name,
            team_name=str(team_name),
            app_name="SharedFileVault",
        )


def _registry_git_dir(vault_root, context):
    return _team_dir(vault_root, context) / "registry" / "git"


def _registry_checkout_dir(vault_root, context):
    return _team_dir(vault_root, context) / "registry" / "checkout"


def _niche_git_dir(vault_root, context, niche_name):
    return _team_dir(vault_root, context) / "niches" / niche_name / "git"



def _bundle_tmp_dir(git_dir):
    """Bundle temp files live inside the git dir, off all work trees."""
    return pathlib.Path(git_dir) / "codsync-bundle-tmp"


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

_CHECKOUTS_DB_VERSION = 3

_CHECKOUTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS checkout (
    id            BLOB PRIMARY KEY,
    team_id      TEXT NOT NULL,
    niche_name    TEXT NOT NULL,
    checkout_path TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (team_id, niche_name)
);
CREATE TABLE IF NOT EXISTS peer_sync (
    team_id         TEXT NOT NULL,
    repo_kind        TEXT NOT NULL,
    niche_name       TEXT NOT NULL,
    member_id        TEXT NOT NULL,
    last_fetched_sha TEXT,
    last_merged_sha  TEXT,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (team_id, repo_kind, niche_name, member_id)
);
CREATE TABLE IF NOT EXISTS peer_signal_watermark (
    team_id   TEXT NOT NULL,
    member_id  TEXT NOT NULL,
    count      INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (team_id, member_id)
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
    context,
    repo_kind,
    niche_name,
    member_id,
    fetched_sha,
):
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        """
        INSERT INTO peer_sync (
            team_id, repo_kind, niche_name, member_id,
            last_fetched_sha, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(team_id, repo_kind, niche_name, member_id)
        DO UPDATE SET
            last_fetched_sha = excluded.last_fetched_sha,
            updated_at = excluded.updated_at
        """,
        (
            context.team_id,
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
    context,
    repo_kind,
    niche_name,
    member_id,
    merged_sha,
):
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        """
        INSERT INTO peer_sync (
            team_id, repo_kind, niche_name, member_id,
            last_fetched_sha, last_merged_sha, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(team_id, repo_kind, niche_name, member_id)
        DO UPDATE SET
            last_fetched_sha = excluded.last_fetched_sha,
            last_merged_sha = excluded.last_merged_sha,
            updated_at = excluded.updated_at
        """,
        (
            context.team_id,
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


def _peer_sync_row(vault_root, participant_hex, context, repo_kind, niche_name, member_id):
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    row = conn.execute(
        """
        SELECT team_id, repo_kind, niche_name, member_id, last_fetched_sha, last_merged_sha
        FROM peer_sync
        WHERE team_id = ? AND repo_kind = ? AND niche_name = ? AND member_id = ?
        """,
        (context.team_id, repo_kind, _peer_sync_niche_key(repo_kind, niche_name), member_id),
    ).fetchone()
    conn.close()
    return row


def get_peer_signal_watermark(vault_root, participant_hex, context, member_id):
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    row = conn.execute(
        "SELECT count FROM peer_signal_watermark WHERE team_id = ? AND member_id = ?",
        (context.team_id, member_id),
    ).fetchone()
    conn.close()
    return int(row["count"]) if row else 0


def set_peer_signal_watermark(vault_root, participant_hex, context, member_id, count):
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        """
        INSERT INTO peer_signal_watermark (team_id, member_id, count, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(team_id, member_id)
        DO UPDATE SET count = excluded.count, updated_at = excluded.updated_at
        """,
        (
            context.team_id,
            member_id,
            int(count),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def clear_peer_signal_watermark(vault_root, participant_hex, context, member_id):
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        "DELETE FROM peer_signal_watermark WHERE team_id = ? AND member_id = ?",
        (context.team_id, member_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Git work tree helpers
# ---------------------------------------------------------------------------

def _has_commits(git_dir):
    r = gitCmd(["--git-dir", str(git_dir), "rev-parse", "HEAD"], raise_on_error=False)
    return r.returncode == 0


def _make_work_tree(git_dir, dest):
    """Create dest and populate it from git_dir if the repo has commits.

    The checkout receives only user files; git metadata stays in git_dir.
    """
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
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

    Always called with the user's checkout_path.
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


def _resolve_ref(git_dir, ref_name):
    result = gitCmd(
        ["--git-dir", str(git_dir), "rev-parse", "--verify", ref_name],
        raise_on_error=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _is_ancestor(git_dir, maybe_ancestor, descendant="HEAD"):
    result = gitCmd(
        [
            "--git-dir", str(git_dir),
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


def _ensure_registry(vault_root, participant_hex, context):
    """Lazily create the registry git repo and checkout for a team."""
    context = _validate_context(participant_hex, context)
    _write_team_metadata(vault_root, context)
    git_dir = _registry_git_dir(vault_root, context)
    checkout = _registry_checkout_dir(vault_root, context)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)
    if not checkout.exists():
        _make_work_tree(git_dir, checkout)


# ---------------------------------------------------------------------------
# Cod Sync push/pull primitives
# ---------------------------------------------------------------------------

def _cod_push(git_dir, remote):
    """Push git_dir to remote via Cod Sync bundle transfer."""
    cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir), repo_dir=git_dir)
    cod.remote = remote
    cod.push_to_remote(["main"])


def _cod_pull(git_dir, checkout, remote):
    """Fetch from remote and merge into the user checkout.

    Uses repo_dir=git_dir for fetch (no work tree needed). The merge step
    uses explicit --git-dir/--work-tree flags; remotes set up on git_dir
    during fetch are visible here.
    """
    btd = _bundle_tmp_dir(git_dir)
    cod_fetch = CS.CodSync("cloud", bundle_tmp_dir=btd, repo_dir=git_dir)
    cod_fetch.remote = remote

    fetch_result = cod_fetch.fetch_from_remote(["main"])
    if fetch_result is None:
        raise RuntimeError("pull failed: could not fetch from remote")

    tmp_remote = "cloud-codsync-bundle-tmp"
    git_prefix = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]
    head_result = gitCmd(
        ["--git-dir", str(git_dir), "rev-parse", "--verify", "HEAD"],
        raise_on_error=False,
    )
    if head_result.returncode != 0:
        # Unborn branch — adopt fetched branch as initial local branch.
        result = gitCmd(
            git_prefix + ["checkout", "-B", "main", f"{tmp_remote}/main"],
            raise_on_error=False,
        )
        exit_code = result.returncode
    else:
        result = gitCmd(
            git_prefix + ["merge", f"{tmp_remote}/main"],
            raise_on_error=False,
        )
        exit_code = result.returncode
    if exit_code != 0:
        raise MergeConflictError(_conflict_paths(git_dir, checkout))


def _cod_fetch(git_dir, remote, pin_to_ref):
    """Fetch from remote and optionally pin the result to a local ref.

    Operates on git_dir directly — no work tree needed for fetch operations.
    Safe to call when no checkout is registered (CACHED state).
    """
    cod = CS.CodSync("cloud", bundle_tmp_dir=_bundle_tmp_dir(git_dir), repo_dir=git_dir)
    cod.remote = remote
    return cod.fetch_from_remote(["main"], pin_to_ref=pin_to_ref)


def _cod_merge_ref(git_dir, checkout, ref_name):
    """Merge a parked peer ref into the user checkout.

    Uses explicit --git-dir/--work-tree flags throughout.
    """
    git_prefix = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]
    if _has_commits(git_dir):
        result = gitCmd(git_prefix + ["merge", ref_name], raise_on_error=False)
        if result.returncode != 0:
            raise MergeConflictError(_conflict_paths(git_dir, checkout))
    else:
        # No local history: initialise the branch from the parked peer ref.
        # Content conflicts are impossible here; GitCmdFailed propagates as-is.
        gitCmd(git_prefix + ["checkout", "-B", "main", ref_name])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_vault(vault_root, participant_hex):
    """Create the vault directory and local checkout registry database."""
    pdir = _participant_dir(vault_root, participant_hex)
    pdir.mkdir(parents=True, exist_ok=True)
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.close()


def create_niche(vault_root, participant_hex, context, niche_name):
    """Create a niche and record it in the team's shared registry.

    Creates the niche git repo locally. The registry entry propagates to
    teammates on the next push_registry call.

    niche_name is canonicalized (NFC + casefold + slug) before use.
    """
    context = _validate_context(participant_hex, context)
    niche_name = _canonical_name(niche_name)
    _ensure_registry(vault_root, participant_hex, context)

    # Create niche git repo
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)

    # Write niche record to registry checkout and commit
    registry_git = _registry_git_dir(vault_root, context)
    registry_co = _registry_checkout_dir(vault_root, context)
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


def list_niches(vault_root, participant_hex, context):
    """List all niches known to this participant for a team.

    Reads from the local registry checkout, which reflects whatever has
    been pulled from the shared registry chain.

    Each returned dict includes a ``"residency"`` key with the string value
    of the niche's NicheResidency on this device, computed on read.
    """
    context = _validate_context(participant_hex, context)
    _ensure_registry(vault_root, participant_hex, context)
    registry_co = _registry_checkout_dir(vault_root, context)
    niches = []
    for f in sorted(registry_co.glob("*.json")):
        data = json.loads(f.read_text())
        if data:
            data["residency"] = niche_residency(
                vault_root, participant_hex, context, data["name"]
            ).value
            niches.append(data)
    return niches


def add_checkout(vault_root, participant_hex, context, niche_name, dest_path):
    """Register a local directory as the checkout of a niche.

    Each niche may have at most one checkout on a device. Raises
    DuplicateCheckoutError if one is already registered. Remove the existing
    checkout before attaching a new location.

    If the niche already has commits, dest_path is populated immediately.
    """
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    if not git_dir.exists():
        raise ValueError(f"Niche '{niche_name}' does not exist in team '{context.team_name}'")

    existing = get_checkout(vault_root, participant_hex, context, niche_name)
    if existing is not None:
        raise DuplicateCheckoutError(context.team_name, niche_name, existing)

    _make_work_tree(git_dir, dest_path)

    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        "INSERT INTO checkout (id, team_id, niche_name, checkout_path, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            uuid7(),
            context.team_id,
            niche_name,
            str(pathlib.Path(dest_path)),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def remove_checkout(vault_root, participant_hex, context, niche_name, checkout_path):
    """Unregister a checkout. Does not delete files in the directory."""
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    conn.execute(
        "DELETE FROM checkout WHERE team_id = ? AND niche_name = ? AND checkout_path = ?",
        (context.team_id, niche_name, str(pathlib.Path(checkout_path))),
    )
    conn.commit()
    conn.close()


def get_checkout(vault_root, participant_hex, context, niche_name):
    """Return the single registered checkout path for a niche, or None."""
    context = _validate_context(participant_hex, context)
    conn = _connect_checkouts(vault_root, participant_hex)
    row = conn.execute(
        "SELECT checkout_path FROM checkout WHERE team_id = ? AND niche_name = ?",
        (context.team_id, niche_name),
    ).fetchone()
    conn.close()
    return row["checkout_path"] if row else None


def list_checkouts(vault_root, participant_hex, context, niche_name):
    """Return list of checkout paths for a niche (at most one element)."""
    checkout = get_checkout(vault_root, participant_hex, context, niche_name)
    return [checkout] if checkout is not None else []


def niche_residency(vault_root, participant_hex, context, niche_name):
    """Return the NicheResidency for a niche on this device.

    REMOTE_ONLY  — no local git dir exists (niche known only from registry).
    CACHED       — local git dir exists but no checkout is registered.
    CHECKED_OUT  — local git dir exists and a checkout is registered.
    """
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    if not git_dir.exists():
        return NicheResidency.REMOTE_ONLY
    checkout = get_checkout(vault_root, participant_hex, context, niche_name)
    if checkout is None:
        return NicheResidency.CACHED
    return NicheResidency.CHECKED_OUT


def publish(vault_root, participant_hex, context, niche_name, checkout_path,
            files=None, message=None):
    """Stage changes in a checkout and commit. Returns commit hash."""
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
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


def status(vault_root, participant_hex, context, niche_name, checkout_path):
    """Get git status for a checkout. Returns list of {status, path} dicts."""
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
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


def log(vault_root, participant_hex, context, niche_name, limit=20):
    """Get commit log for a niche. Returns list of {hash, message} dicts."""
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
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


def push_registry(vault_root, participant_hex, context, remote):
    """Push the niche registry to cloud storage via Cod Sync."""
    context = _validate_context(participant_hex, context)
    _ensure_registry(vault_root, participant_hex, context)
    git_dir = _registry_git_dir(vault_root, context)
    _cod_push(git_dir, remote)


def pull_registry(vault_root, participant_hex, context, remote):
    """Pull the niche registry from cloud storage and merge."""
    context = _validate_context(participant_hex, context)
    _ensure_registry(vault_root, participant_hex, context)
    git_dir = _registry_git_dir(vault_root, context)
    checkout = _registry_checkout_dir(vault_root, context)
    _cod_pull(git_dir, checkout, remote)


def fetch_registry(vault_root, participant_hex, context, member_id, remote):
    """Fetch the registry from a peer and pin it to a durable local ref."""
    context = _validate_context(participant_hex, context)
    _ensure_registry(vault_root, participant_hex, context)
    git_dir = _registry_git_dir(vault_root, context)
    ref_name = _peer_ref_name(member_id)
    fetched_sha = _cod_fetch(git_dir, remote, ref_name)
    if fetched_sha is not None:
        _record_peer_fetch(
            vault_root, participant_hex, context, "registry", None, member_id, fetched_sha
        )
    return fetched_sha


def merge_registry(vault_root, participant_hex, context, member_id):
    """Merge a previously parked registry ref from a peer."""
    context = _validate_context(participant_hex, context)
    _ensure_registry(vault_root, participant_hex, context)
    git_dir = _registry_git_dir(vault_root, context)
    checkout = _registry_checkout_dir(vault_root, context)
    ref_name = _peer_ref_name(member_id)
    parked_sha = _resolve_ref(git_dir, ref_name)
    if parked_sha is None:
        return None
    if _has_commits(git_dir) and _is_ancestor(git_dir, parked_sha, "HEAD"):
        _record_peer_merge(
            vault_root, participant_hex, context, "registry", None, member_id, parked_sha
        )
        return parked_sha
    _cod_merge_ref(git_dir, checkout, ref_name)
    _record_peer_merge(
        vault_root, participant_hex, context, "registry", None, member_id, parked_sha
    )
    return parked_sha


def push_niche(vault_root, participant_hex, context, niche_name, remote):
    """Push a niche to cloud storage via Cod Sync."""
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    _cod_push(git_dir, remote)


def _require_clean_checkout(vault_root, participant_hex, context, niche_name):
    """Verify a checkout is attached, exists on disk, and is clean.

    Returns the checkout path. Raises NoCheckoutError, StaleCheckoutError, or
    DirtyCheckoutError as appropriate. Called by pull_niche and merge_niche
    before any merge step that writes into the user checkout.
    """
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    checkout = get_checkout(vault_root, participant_hex, context, niche_name)
    if checkout is None:
        residency = niche_residency(vault_root, participant_hex, context, niche_name)
        raise NoCheckoutError(context.team_name, niche_name, residency)
    if not pathlib.Path(checkout).exists():
        raise StaleCheckoutError(context.team_name, niche_name, checkout)
    if not _is_checkout_clean(checkout, git_dir):
        dirty = [e["path"] for e in status(vault_root, participant_hex, context, niche_name, checkout)]
        raise DirtyCheckoutError(dirty)
    return checkout


def pull_niche(vault_root, participant_hex, context, niche_name, remote):
    """Pull a niche from cloud storage and merge into the user checkout.

    Requires a checkout to be attached and clean. This keeps pull semantics
    consistent with merge: visible files are only updated through an explicit
    action, never automatically on a subsequent add_checkout call.

    For the initial join flow (no checkout yet), use fetch_niche to park the
    remote content, then add_checkout, then merge_niche.
    """
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)

    checkout = _require_clean_checkout(vault_root, participant_hex, context, niche_name)

    _cod_pull(git_dir, checkout, remote)


def fetch_niche(vault_root, participant_hex, context, niche_name, member_id, remote):
    """Fetch a niche from a peer and pin it to a durable local ref.

    Fetch does not modify the user's checkout, so no clean-checkout guard
    is needed here. The guard fires at merge time.  Works from CACHED state
    (no checkout registered).
    """
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    if not git_dir.exists():
        git_dir.mkdir(parents=True)
        _init_git_dir(git_dir)

    ref_name = _peer_ref_name(member_id)
    fetched_sha = _cod_fetch(git_dir, remote, ref_name)
    if fetched_sha is not None:
        _record_peer_fetch(
            vault_root, participant_hex, context, "niche", niche_name, member_id, fetched_sha
        )
    return fetched_sha


def merge_niche(vault_root, participant_hex, context, niche_name, member_id):
    """Merge a previously parked niche ref from a peer.

    Requires a checkout to be attached (raises NoCheckoutError if none).
    Requires the checkout to be clean (raises DirtyCheckoutError if not).
    The clean-checkout guard fires before any merge step that writes into
    the user checkout.
    """
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    checkout = _require_clean_checkout(vault_root, participant_hex, context, niche_name)

    ref_name = _peer_ref_name(member_id)
    parked_sha = _resolve_ref(git_dir, ref_name)
    if parked_sha is None:
        return None
    if _has_commits(git_dir) and _is_ancestor(git_dir, parked_sha, "HEAD"):
        _record_peer_merge(
            vault_root, participant_hex, context, "niche", niche_name, member_id, parked_sha
        )
        return parked_sha
    _cod_merge_ref(git_dir, checkout, ref_name)
    _record_peer_merge(
        vault_root, participant_hex, context, "niche", niche_name, member_id, parked_sha
    )
    return parked_sha


def registry_conflict_paths(vault_root, participant_hex, context):
    """Return unresolved conflict paths for the team's registry repo."""
    context = _validate_context(participant_hex, context)
    git_dir = _registry_git_dir(vault_root, context)
    checkout = _registry_checkout_dir(vault_root, context)
    return _conflict_paths(git_dir, checkout)


def niche_conflict_paths(vault_root, participant_hex, context, niche_name):
    """Return unresolved conflict paths for a niche's user checkout.

    Decision tree:
    1. No git dir (REMOTE_ONLY) → return []
    2. Usable checkout (registered and directory exists) → return conflict paths
    3. No usable checkout (CACHED, or checkout registered-but-deleted):
       - MERGE_HEAD present → raise StaleCheckoutError (stale registration) or
         NoCheckoutError (no registration); user must re-register before resolving
       - No MERGE_HEAD → return []

    Intentional API change: this function can now raise NoCheckoutError or
    StaleCheckoutError when MERGE_HEAD is present but no checkout is available.
    Audit every caller to confirm they handle or never reach these states.
    """
    context = _validate_context(participant_hex, context)
    git_dir = _niche_git_dir(vault_root, context, niche_name)
    if not git_dir.exists():
        return []

    checkout = get_checkout(vault_root, participant_hex, context, niche_name)
    if checkout is not None and pathlib.Path(checkout).exists():
        return _conflict_paths(git_dir, checkout)

    # No usable checkout — check for orphaned merge state in the git dir.
    if _resolve_ref(git_dir, "MERGE_HEAD") is not None:
        if checkout is not None:
            raise StaleCheckoutError(context.team_name, niche_name, checkout)
        raise NoCheckoutError(context.team_name, niche_name, NicheResidency.CACHED)
    return []


def peer_update_status(
    vault_root, participant_hex, context, repo_kind, niche_name, member_id
):
    """Return parked-ref status for one peer/repo pair."""
    context = _validate_context(participant_hex, context)
    if repo_kind == "registry":
        _ensure_registry(vault_root, participant_hex, context)
        git_dir = _registry_git_dir(vault_root, context)
        work_tree = _registry_checkout_dir(vault_root, context)
    else:
        git_dir = _niche_git_dir(vault_root, context, niche_name)
        if not git_dir.exists():
            row = _peer_sync_row(
                vault_root, participant_hex, context, repo_kind, niche_name, member_id
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
    parked_sha = _resolve_ref(git_dir, ref_name)
    row = _peer_sync_row(vault_root, participant_hex, context, repo_kind, niche_name, member_id)
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

    already_merged = _has_commits(git_dir) and _is_ancestor(git_dir, parked_sha, "HEAD")
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
