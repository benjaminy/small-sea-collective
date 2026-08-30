"""Adopting stored NoteToSelf history into this device's live database.

One identity's devices each publish their own NoteToSelf `core.db` through Cod
Sync, so two of them can hold histories neither contains. Combining those is a
row merge applied to the live database inside one SQLite transaction, not a
file replacement: the Hub is a second long-lived process writing the same file
(`backend.py` inserts `cloud_storage` rows and updates
`berth_cloud_allocation.location`), and SQLite's own cross-process write lock is
the only serialization both processes already honour. Replacing the file would
leave the Hub writing into an unlinked inode.

Two consequences shape everything here.

The adopted bytes come from the live file rather than from a Git-merged blob,
so a recorded merge commit is captured from the work tree. Git reads `core.db`
as an ordinary file and honours neither SQLite's locks nor its rollback
journal, so every Git read of the live database runs while this module holds a
writer reservation on it.

Nothing is precomputed. The computation is always (base, source, live-now), so
an adoption interrupted anywhere is corrected by running it again: the second
attempt recomputes against the same base and finds the source's own work
already on both sides, which `reconcile_deltas` treats as redundant. The
failure contract is therefore retry, not repair, and no watermark, lease, or
recovery record exists.

What this module deliberately does not claim: Cod Sync proves a history's
structure, bundle contents and ancestry, not who authored it (#190). A source
that arrives here is a structurally valid stored history from the participant's
own configured NoteToSelf storage whose tree and database shape this Manager
can read. It is not evidence that a sibling device wrote it.
"""

import contextlib
import logging
import pathlib
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Optional

from cod_sync.protocol import MAIN_REF, outstanding_parked_heads, parked_ref_name
from cod_sync.repo import Repo, RepoError
from small_sea_note_to_self.db import (
    SHARED_DB_FILENAME,
    SHARED_SCHEMA_VERSION,
    attached_note_to_self_connection,
    initialize_shared_db,
    note_to_self_sync_db_path,
)
from splice_merge.core import (
    AmbiguousRowKeyError,
    apply_delta,
    compute_delta,
    reconcile_deltas,
    sqlite_to_json,
)

_LOG = logging.getLogger(__name__)

#: The commit message a recorded integration carries. It names what was
#: combined, not who wrote it.
INTEGRATION_COMMIT_MESSAGE = "Integrate stored NoteToSelf history"


@dataclass(frozen=True)
class NoteToSelfSource:
    """One outstanding stored head, derived from refs and ancestry at read time.

    Nothing here is remembered across processes: a freshly constructed Manager
    computes the same list from the same refs.
    """

    ref_name: str
    head_sha: str


@dataclass(frozen=True)
class SourceOutcome:
    """What adopting one source did, or why it did nothing.

    outcome is one of:
      "integrated"          rows adopted (possibly none) and history recorded
      "already_contained"   local history already holds the source
      "incompatible_source" the tree or database shape is not one we can merge
      "semantic_conflict"   both sides changed a row differently (D9)
      "constraint_refused"  the merged rows violate the live schema
      "recording_pending"   rows are adopted and committed, Git recording is not

    Every outcome but the last two leaves the database exactly as it was; a
    "recording_pending" is a retry state, not a claim that anything rolled back.
    """

    ref_name: str
    head_sha: str
    outcome: str
    detail: Optional[str] = None
    conflicts: tuple = ()
    recorded_head: Optional[str] = None
    kind: Optional[str] = None


@dataclass(frozen=True)
class IntegrationResult:
    """One run of the integration operation over every outstanding source.

    blocked names a condition that stopped the run before any source was
    considered, and leaves outcomes empty.
    """

    outcomes: tuple = ()
    blocked: Optional[str] = None

    @property
    def integrated(self) -> tuple:
        return tuple(o for o in self.outcomes if o.outcome == "integrated")

    @property
    def refusals(self) -> tuple:
        return tuple(
            o
            for o in self.outcomes
            if o.outcome in ("incompatible_source", "semantic_conflict", "constraint_refused")
        )

    @property
    def adopted_anything(self) -> bool:
        """True when local state moved, so callers know to re-read the DB."""
        return any(
            o.outcome in ("integrated", "recording_pending") for o in self.outcomes
        )


class _Refusal(Exception):
    """An internal, typed reason one source was not adopted."""

    def __init__(self, outcome: str, detail: str, conflicts: tuple = ()):
        super().__init__(detail)
        self.outcome = outcome
        self.detail = detail
        self.conflicts = conflicts


# ---------------------------------------------------------------------------
# Writer reservation (D12)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def write_reservation(root_dir, participant_hex):
    """Hold a SQLite writer reservation over the participant's NoteToSelf state.

    Git reads `core.db` as a plain file. In rollback-journal mode another
    process's uncommitted pages can already be in that file, so a raw read
    overlapping a writer can capture a mixture of two states — a live database
    that stays healthy while the blob published to another device does not.

    `BEGIN IMMEDIATE` waits for any current writer and prevents a new one from
    starting, which is a promise Git cannot make for itself but SQLite can make
    on its behalf. The reservation writes nothing, so it always ends in
    rollback, and it must be released before any Hub I/O: the Hub is the other
    writer this is protecting against.
    """
    conn = attached_note_to_self_connection(root_dir, participant_hex)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
    finally:
        conn.close()


def commit_core_db(root_dir, participant_hex, repo: Repo, message: str) -> Optional[str]:
    """Stage and commit the live `core.db` under a writer reservation.

    The stage-and-commit form of the rule that every Git read of the live
    NoteToSelf database happens under a reservation; publication and merge
    recording hold the same reservation over their own Git calls.

    The reservation covers only Git's raw read. The application mutation that
    produced these rows, and its own transaction, finished before this call.
    """
    with write_reservation(root_dir, participant_hex):
        repo.stage([SHARED_DB_FILENAME])
        return repo.commit(message)


def live_differs_from_head(repo: Repo, conn) -> bool:
    """True when the live rows differ from the ones committed at HEAD.

    Cleanliness here is logical, not byte-level. Adoption applies rows to the
    live database rather than checking out the source blob, so a database can
    be byte-different from HEAD while holding exactly the rows HEAD records.
    Treating that as dirty would publish a head with no change in it, and a
    second device would then have to integrate it.

    Reads live rows through the caller's connection, so a caller holding a
    writer reservation sees the same state its subsequent commit will capture.
    """
    with tempfile.TemporaryDirectory(prefix="nts-head-") as work:
        head_path = pathlib.Path(work) / "head.db"
        repo.blob_at("HEAD", SHARED_DB_FILENAME, head_path)
        with contextlib.closing(sqlite3.connect(str(head_path))) as head_conn:
            head_shape = _schema_shape(head_conn)
            head_json = sqlite_to_json(head_conn)
    if head_shape != _schema_shape(conn):
        # A schema change is a content change, whatever the rows say.
        return True
    return bool(compute_delta(head_json, sqlite_to_json(conn)))


# ---------------------------------------------------------------------------
# Source admissibility (D13, D2)
# ---------------------------------------------------------------------------


def _schema_shape(conn) -> dict:
    """Return {table: (ordered columns, primary key columns)} for `main`.

    Read from `PRAGMA table_info` rather than from `sqlite_to_json` output,
    which carries column names only inside row dicts: an empty table exposes
    none of them, so a source that dropped a column from an empty table would
    otherwise compare equal. Indexes and DDL text are deliberately not compared;
    a unique index the source violates is a constraint refusal, not an
    incompatible shape.
    """
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    shape = {}
    for table in tables:
        info = conn.execute(f"PRAGMA main.table_info('{table}')").fetchall()
        columns = tuple(row[1] for row in info)
        primary_key = tuple(
            row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])
        )
        shape[table] = (columns, primary_key)
    return shape


def _require_note_to_self_tree(repo: Repo, rev: str, label: str):
    """Require rev's root tree to be exactly one regular `core.db` blob.

    Cod Sync validates transport structure, not application tree contents, and
    #190 has not connected a stored history to an admitted device. So the shape
    of an incoming tree is assumed nowhere: a fast-forward adopts the whole
    source tree into the index and then into `main`, which makes this check
    load-bearing rather than defensive.
    """
    try:
        entries = repo.tree_entries(rev)
    except RepoError as exc:
        raise _Refusal(
            "incompatible_source", f"the {label} commit's tree is unreadable: {exc}"
        ) from exc
    described = sorted(
        f"{entry.mode} {entry.object_type} {entry.path!r}" for entry in entries
    )
    if len(entries) != 1:
        raise _Refusal(
            "incompatible_source",
            f"the {label} tree holds {len(entries)} entries instead of one "
            f"{SHARED_DB_FILENAME}: {described}",
        )
    entry = entries[0]
    if (
        entry.path != SHARED_DB_FILENAME.encode()
        or entry.object_type != "blob"
        or entry.mode != "100644"
    ):
        raise _Refusal(
            "incompatible_source",
            f"the {label} tree holds {described[0]} instead of a regular "
            f"{SHARED_DB_FILENAME} blob",
        )


def _read_side(path: pathlib.Path, label: str):
    """Return (rows, schema shape) for an extracted database, or refuse.

    Transport validity says nothing about the file a commit points at: it can
    be missing, corrupt, or a database this Manager does not understand. The
    delta algorithm reads a table absent from one version as deletion of the
    other's rows, so an unchecked shape mismatch is silent data loss rather
    than an error.
    """
    try:
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SHARED_SCHEMA_VERSION:
                raise _Refusal(
                    "incompatible_source",
                    f"the {label} database is NoteToSelf schema version {version}, "
                    f"and this Manager reads version {SHARED_SCHEMA_VERSION}",
                )
            return sqlite_to_json(conn), _schema_shape(conn)
    except sqlite3.DatabaseError as exc:
        raise _Refusal(
            "incompatible_source",
            f"the {label} {SHARED_DB_FILENAME} is not a readable SQLite database: {exc}",
        ) from exc


def _describe_shape_mismatch(expected: dict, observed: dict, label: str) -> str:
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    changed = sorted(
        table for table in set(expected) & set(observed) if expected[table] != observed[table]
    )
    parts = []
    if missing:
        parts.append(f"missing tables {missing}")
    if extra:
        parts.append(f"unexpected tables {extra}")
    if changed:
        parts.append(f"different columns or primary keys in {changed}")
    return f"the {label} database has " + "; ".join(parts)


def _require_initial_source_database(repo: Repo, source_sha: str):
    """Require an initial source to match this Manager's supported database.

    With no live database or shared ancestor, the bundled schema is the only
    local authority available. Build it in a temporary file so validation does
    not create the live path before the source has been accepted.
    """
    with tempfile.TemporaryDirectory(prefix="nts-initial-") as work:
        work = pathlib.Path(work)
        source_path = work / "source.db"
        expected_path = work / "expected.db"
        repo.blob_at(source_sha, SHARED_DB_FILENAME, source_path)
        source_json, source_shape = _read_side(source_path, "source")
        initialize_shared_db(expected_path)
        with contextlib.closing(sqlite3.connect(str(expected_path))) as expected_conn:
            expected_shape = _schema_shape(expected_conn)
        if source_shape != expected_shape:
            raise _Refusal(
                "incompatible_source",
                _describe_shape_mismatch(expected_shape, source_shape, "source"),
            )
        try:
            compute_delta(source_json, source_json)
        except AmbiguousRowKeyError as exc:
            raise _Refusal(
                "incompatible_source",
                f"the source database has ambiguous row identity: {exc}",
            ) from exc


# ---------------------------------------------------------------------------
# Adoption
# ---------------------------------------------------------------------------


def adopt_source(root_dir, participant_hex, repo: Repo, source: NoteToSelfSource) -> SourceOutcome:
    """Combine one stored NoteToSelf head into this device's live state.

    Everything that can refuse runs before a single live row changes, and the
    row application itself is one transaction that either commits whole or
    leaves `HEAD`, `core.db`, the index, the parked ref and the adopted counter
    exactly as they were.
    """
    try:
        return _adopt_source(root_dir, participant_hex, repo, source)
    except _Refusal as refusal:
        return SourceOutcome(
            source.ref_name,
            source.head_sha,
            refusal.outcome,
            detail=refusal.detail,
            conflicts=refusal.conflicts,
        )


def _adopt_source(root_dir, participant_hex, repo, source) -> SourceOutcome:
    _require_note_to_self_tree(repo, source.head_sha, "source")

    local_head = repo.head()
    if local_head is None:
        return _adopt_into_unborn_repo(root_dir, participant_hex, repo, source)
    if repo.is_ancestor(source.head_sha, local_head):
        return SourceOutcome(source.ref_name, source.head_sha, "already_contained")

    base = repo.merge_base(local_head, source.head_sha)
    if base is None:
        raise _Refusal(
            "incompatible_source",
            "the source shares no history with this device's NoteToSelf, so there "
            "is no common state to merge from",
        )
    _require_note_to_self_tree(repo, base, "shared ancestor")
    fast_forward = repo.is_ancestor(local_head, source.head_sha)

    with tempfile.TemporaryDirectory(prefix="nts-adopt-") as work:
        work = pathlib.Path(work)
        repo.blob_at(base, SHARED_DB_FILENAME, work / "base.db")
        repo.blob_at(source.head_sha, SHARED_DB_FILENAME, work / "source.db")
        base_json, base_shape = _read_side(work / "base.db", "shared ancestor")
        source_json, source_shape = _read_side(work / "source.db", "source")
        if source_shape != base_shape:
            raise _Refusal(
                "incompatible_source",
                _describe_shape_mismatch(base_shape, source_shape, "source"),
            )
        _apply_source_rows(root_dir, participant_hex, base_json, source_json, source_shape)

    # Past this point the rows are committed. Anything that fails now is a
    # recording failure, and re-running the operation records the same result.
    try:
        if fast_forward:
            recorded = _record_fast_forward(repo, source.head_sha)
        else:
            recorded = _record_merge(
                root_dir, participant_hex, repo, local_head, source.head_sha
            )
    except (RepoError, sqlite3.Error) as exc:
        _LOG.exception("Recording an adopted NoteToSelf head failed")
        return SourceOutcome(
            source.ref_name,
            source.head_sha,
            "recording_pending",
            detail=str(exc),
            kind="fast_forward" if fast_forward else "divergent",
        )
    return SourceOutcome(
        source.ref_name,
        source.head_sha,
        "integrated",
        recorded_head=recorded,
        kind="fast_forward" if fast_forward else "divergent",
    )


def _adopt_into_unborn_repo(root_dir, participant_hex, repo, source) -> SourceOutcome:
    """Adopt a source into a repository that has no history of its own.

    The only case where checking the source out is safe, because there is no
    live database for another process to hold open. A device that already has
    NoteToSelf rows and no shared history with the source has an incompatible
    source, not a clone.
    """
    if note_to_self_sync_db_path(root_dir, participant_hex).exists():
        raise _Refusal(
            "incompatible_source",
            "this device already has a NoteToSelf database but no committed "
            "history the source builds on",
        )
    _require_initial_source_database(repo, source.head_sha)
    repo.checkout_branch("main", start_point=source.head_sha)
    return SourceOutcome(
        source.ref_name,
        source.head_sha,
        "integrated",
        recorded_head=source.head_sha,
        kind="initial",
    )


def _apply_source_rows(root_dir, participant_hex, base_json, source_json, source_shape):
    """Apply the source's row delta to the live database in one transaction.

    Runs on the ordinary attached Manager connection, so SQLite's write lock is
    the boundary against the Hub, and both live reads — the schema check and
    the "ours" side of the merge — happen inside the transaction's snapshot.

    Foreign keys are disabled before `BEGIN` (the pragma is a no-op inside a
    transaction) and `foreign_key_check` runs before `COMMIT`, which gives
    deferred-constraint semantics without requiring the delta's rows to arrive
    in dependency order.
    """
    conn = attached_note_to_self_connection(root_dir, participant_hex)
    try:
        conn.isolation_level = None
        foreign_keys_were_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                live_shape = _schema_shape(conn)
                if live_shape != source_shape:
                    raise _Refusal(
                        "incompatible_source",
                        _describe_shape_mismatch(live_shape, source_shape, "source"),
                    )
                live_json = sqlite_to_json(conn)
                try:
                    ours_delta = compute_delta(base_json, live_json)
                except AmbiguousRowKeyError as exc:
                    outcome = (
                        "incompatible_source"
                        if exc.snapshot == "ancestor"
                        else "constraint_refused"
                    )
                    label = "shared ancestor" if exc.snapshot == "ancestor" else "live"
                    raise _Refusal(
                        outcome,
                        f"the {label} database has ambiguous row identity: {exc}",
                    ) from exc
                try:
                    theirs_delta = compute_delta(base_json, source_json)
                except AmbiguousRowKeyError as exc:
                    raise _Refusal(
                        "incompatible_source",
                        f"the source database has ambiguous row identity: {exc}",
                    ) from exc
                cleaned, conflicts = reconcile_deltas(ours_delta, theirs_delta)
                if conflicts:
                    raise _Refusal(
                        "semantic_conflict",
                        "both this device and the source changed the same rows in "
                        "different ways",
                        conflicts=tuple(conflicts),
                    )
                try:
                    apply_delta(conn, cleaned)
                except sqlite3.IntegrityError as exc:
                    raise _Refusal(
                        "constraint_refused",
                        f"the combined rows violate this database's constraints: {exc}",
                    ) from exc
                violations = conn.execute("PRAGMA main.foreign_key_check").fetchall()
                if violations:
                    raise _Refusal(
                        "constraint_refused",
                        "the combined rows leave "
                        f"{len(violations)} foreign-key violations",
                    )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.execute(
                f"PRAGMA foreign_keys = {'ON' if foreign_keys_were_on else 'OFF'}"
            )
    finally:
        conn.close()


def _record_fast_forward(repo: Repo, source_sha: str) -> str:
    """Advance `main` to a source that already contains local history.

    The source blob is never written over the live database: a clean Git work
    tree does not prove the Hub has no connection open on that inode, which is
    the hazard the SQLite adoption boundary removes. So the live database can
    be byte-different from the new `HEAD` while holding exactly its rows, and
    the Manager's cleanliness check is logical for that reason.

    The index is written before the ref on purpose: dying in between leaves the
    source staged against the old head, and re-running the same operation
    finishes the ref movement.
    """
    repo.read_tree(source_sha)
    advance = repo.advance_ref(MAIN_REF, source_sha)
    if advance.disposition == "stale":
        # Another writer already moved past the source; realign the index to
        # what `main` actually holds rather than leaving it behind.
        repo.read_tree(advance.current_sha)
    return advance.current_sha


def _record_merge(root_dir, participant_hex, repo: Repo, local_head: str, source_sha: str) -> str:
    """Record a two-parent commit from the live database's own bytes.

    The staged blob is captured under a writer reservation so no other process
    can spill pages into `core.db` while Git reads it. Commit construction and
    ref movement happen after the reservation is released; a Hub write that
    lands then is preserved as the next ordinary local change.
    """
    with write_reservation(root_dir, participant_hex):
        repo.stage([SHARED_DB_FILENAME])
        tree = repo.write_tree()
    commit = repo.commit_tree(tree, [local_head, source_sha], INTEGRATION_COMMIT_MESSAGE)
    return repo.advance_ref(MAIN_REF, commit).current_sha


# ---------------------------------------------------------------------------
# The operation
# ---------------------------------------------------------------------------


def outstanding_sources(repo: Repo) -> list:
    """Every stored head still worth integrating, read from refs alone."""
    return [
        NoteToSelfSource(ref_name=ref_name, head_sha=sha)
        for ref_name, sha in outstanding_parked_heads(repo)
    ]


def adopt_fetched_source(
    root_dir, participant_hex, repo: Repo, head_sha: str, link_uid: str
) -> SourceOutcome:
    """Park one fetched head, then adopt it through the ordinary source path."""
    ref_name = parked_ref_name(link_uid)
    repo.create_ref_immutable(ref_name, head_sha)
    return adopt_source(
        root_dir,
        participant_hex,
        repo,
        NoteToSelfSource(ref_name=ref_name, head_sha=head_sha),
    )


def integrate(root_dir, participant_hex, repo: Repo) -> IntegrationResult:
    """Adopt every outstanding stored NoteToSelf head into local state.

    Parked refs are left in place afterwards. Discovery filters by ancestry, so
    an integrated head stops being outstanding on its own, and deleting refs
    would add a failure mode without adding information.
    """
    if repo.resolve_ref("MERGE_HEAD") is not None:
        return IntegrationResult(
            blocked=(
                "an unfinished merge is still recorded in the NoteToSelf repository; "
                "resolve it before integrating"
            )
        )
    outcomes = []
    for source in outstanding_sources(repo):
        # Retested per source: one incomparable adoption can absorb another.
        if repo.has_commits() and repo.is_ancestor(source.head_sha, "HEAD"):
            continue
        outcomes.append(adopt_source(root_dir, participant_hex, repo, source))
    return IntegrationResult(outcomes=tuple(outcomes))
