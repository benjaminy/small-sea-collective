"""Micro tests for adopting stored NoteToSelf history into the live database.

These build the competing history with Git plumbing rather than a second
installation, so the adoption rules can be exercised one at a time. The
two-device round trip through real cloud storage lives in
test_note_to_self_refresh.py, which is what proves the combination is real
rather than merely locally plausible.

Nothing here asserts that Cod Sync established which device authored a source:
it does not, and the Manager must not claim otherwise (#190).
"""

import contextlib
import os
import pathlib
import sqlite3
import subprocess
import threading
import time

import pytest

from cod_sync.protocol import parked_ref_name
from cod_sync.repo import Repo, RepoError
from small_sea_manager import note_to_self_sync
from small_sea_manager.manager import TeamManager
from small_sea_manager.provisioning import create_new_participant
from small_sea_note_to_self.db import (
    SHARED_SCHEMA_VERSION,
    initialize_bootstrap_local_state,
    note_to_self_sync_db_path,
)

_CLOUD_ID = b"\xc1" * 16
_BERTH_ID = b"\xb0" * 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _participant(root):
    """Create an installation and return (participant_hex, repo)."""
    participant_hex = create_new_participant(root, "Alice")
    repo_dir = root / "Participants" / participant_hex / "NoteToSelf" / "Sync"
    return participant_hex, Repo(repo_dir / ".git", repo_dir)


def _shared_db(root, participant_hex):
    return note_to_self_sync_db_path(root, participant_hex)


@contextlib.contextmanager
def _live(root, participant_hex):
    """A plain connection to the shared DB, as a second process would open it."""
    conn = sqlite3.connect(str(_shared_db(root, participant_hex)))
    try:
        yield conn
    finally:
        conn.close()


def _apply_local(root, participant_hex, statements):
    with _live(root, participant_hex) as conn:
        for sql, args in statements:
            conn.execute(sql, args)
        conn.commit()


def _commit_local(root, participant_hex, repo, message="local"):
    return note_to_self_sync.commit_core_db(root, participant_hex, repo, message)


def _hash_object(repo, path):
    return subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "hash-object", "-w", str(path)],
        capture_output=True, check=True, text=True,
    ).stdout.strip()


def _mktree(repo, entries):
    """entries: iterable of (mode, type, oid, raw path bytes)."""
    payload = b"".join(
        f"{mode} {kind} {oid}\t".encode() + path + b"\x00"
        for mode, kind, oid, path in entries
    )
    return subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "mktree", "-z"],
        input=payload, capture_output=True, check=True,
    ).stdout.decode().strip()


def _source_commit(
    repo,
    base_sha,
    statements=(),
    *,
    parents=None,
    mode="100644",
    path=b"core.db",
    raw_bytes=None,
    extra_entries=(),
    message="stored history",
):
    """Build a commit on base_sha whose core.db is base's with statements applied.

    Bypasses the work tree entirely, so the local checkout is never disturbed
    and the tree can be given shapes provisioning would never create.
    """
    with contextlib.ExitStack() as stack:
        work = pathlib.Path(stack.enter_context(_tempdir()))
        db_path = work / "core.db"
        if raw_bytes is None:
            repo.blob_at(base_sha, "core.db", db_path)
            conn = sqlite3.connect(str(db_path))
            for sql, args in statements:
                conn.execute(sql, args)
            conn.commit()
            conn.close()
        else:
            db_path.write_bytes(raw_bytes)
        blob = _hash_object(repo, db_path)
    entries = [(mode, "blob", blob, path), *extra_entries]
    tree = _mktree(repo, entries)
    return repo.commit_tree(tree, list([base_sha] if parents is None else parents), message)


def _tempdir():
    import tempfile

    return tempfile.TemporaryDirectory(prefix="nts-test-")


def _park(repo, uid, sha):
    ref = parked_ref_name(uid)
    repo._run(["update-ref", ref, sha])
    return ref


def _team_names(root, participant_hex):
    with _live(root, participant_hex) as conn:
        return sorted(row[0] for row in conn.execute("SELECT name FROM team"))


def _insert_team(team_id, name):
    return (
        "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
        (team_id, name, b"\x01" * 16),
    )


def _rename_team(team_id, name):
    return ("UPDATE team SET name = ? WHERE id = ?", (name, team_id))


def _with_shared_cloud_row(root, participant_hex, repo):
    """Commit a cloud_storage row so both sides can allocate against it."""
    _apply_local(
        root,
        participant_hex,
        [(
            "INSERT INTO cloud_storage (id, protocol, url) VALUES (?, 's3', 'http://x')",
            (_CLOUD_ID,),
        )],
    )
    return _commit_local(root, participant_hex, repo, "cloud row")


def _allocation(allocation_id, location):
    return (
        "INSERT INTO berth_cloud_allocation "
        "(id, berth_id, cloud_storage_id, location, created_at) "
        "VALUES (?, ?, ?, ?, '2026-01-01')",
        (allocation_id, _BERTH_ID, _CLOUD_ID, location),
    )


def _durable_state(root, participant_hex, repo):
    """Everything a refusal must leave alone."""
    return (
        repo.head(),
        _shared_db(root, participant_hex).read_bytes(),
        repo._run(["ls-files", "--stage"]).stdout,
        repo.list_refs("refs/"),
    )


def _participant_files(root, participant_hex):
    """Every path under the participant directory, excluding Git internals."""
    base = root / "Participants" / participant_hex
    return sorted(
        str(p.relative_to(base))
        for p in base.rglob("*")
        if ".git" not in p.parts
    )


# ---------------------------------------------------------------------------
# Combining two histories
# ---------------------------------------------------------------------------


def test_integrate_combines_divergent_histories(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    base = repo.head()

    source = _source_commit(repo, base, [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    manager = TeamManager(root, participant_hex)
    manager.create_team("OnlyOnB")
    _commit_local(root, participant_hex, repo)
    local_head = repo.head()
    ref = _park(repo, "uid-a", source)

    result = manager.integrate_note_to_self()

    [outcome] = result.outcomes
    assert outcome.outcome == "integrated"
    assert outcome.ref_name == ref
    assert outcome.kind == "divergent"
    assert {"NoteToSelf", "OnlyOnA", "OnlyOnB"} <= set(_team_names(root, participant_hex))
    # A genuine divergence records both pre-merge heads as parents.
    parents = repo._run(["rev-list", "--parents", "-n", "1", "HEAD"]).stdout.split()
    assert parents[1:] == [local_head, source]


def test_a_fresh_manager_discovers_the_parked_head_from_refs_alone(playground_dir):
    """Restart safety: nothing but the ref records what is outstanding."""
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    _apply_local(root, participant_hex, [_insert_team(b"\xbb" * 16, "OnlyOnB")])
    _commit_local(root, participant_hex, repo)
    ref = _park(repo, "uid-a", source)

    status = TeamManager(root, participant_hex).note_to_self_conflict_status()

    assert [(s.ref_name, s.head_sha) for s in status] == [(ref, source)]


def test_integration_is_idempotent(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    manager = TeamManager(root, participant_hex)
    manager.create_team("OnlyOnB")
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)

    assert manager.integrate_note_to_self().integrated
    settled_head = repo.head()

    again = manager.integrate_note_to_self()

    assert again.outcomes == ()
    assert repo.head() == settled_head


def test_multiple_incomparable_parked_heads_are_reported_per_head(playground_dir):
    """One refusal does not hide the other head's success."""
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    base = _with_shared_cloud_row(root, participant_hex, repo)

    good = _source_commit(repo, base, [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    bad = _source_commit(
        repo, base, raw_bytes=b"not a database at all", message="corrupt"
    )
    _apply_local(root, participant_hex, [_insert_team(b"\xbb" * 16, "OnlyOnB")])
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-good", good)
    _park(repo, "uid-bad", bad)

    result = TeamManager(root, participant_hex).integrate_note_to_self()

    by_head = {o.head_sha: o.outcome for o in result.outcomes}
    assert by_head == {good: "integrated", bad: "incompatible_source"}
    assert "OnlyOnA" in _team_names(root, participant_hex)


# ---------------------------------------------------------------------------
# Fast-forward (D10)
# ---------------------------------------------------------------------------


def test_fast_forward_advances_main_without_materializing_the_source_blob(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    base = repo.head()
    source = _source_commit(repo, base, [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    _park(repo, "uid-a", source)
    live_path = _shared_db(root, participant_hex)
    inode_before = os.stat(live_path).st_ino
    early = sqlite3.connect(str(live_path), timeout=20)

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "integrated"
    assert outcome.kind == "fast_forward"
    assert repo.resolve_ref("refs/heads/main") == source
    assert os.stat(live_path).st_ino == inode_before
    assert "OnlyOnA" in _team_names(root, participant_hex)
    # Byte-different from the new HEAD is fine; holding different rows is not.
    with note_to_self_sync.write_reservation(root, participant_hex) as conn:
        assert not note_to_self_sync.live_differs_from_head(repo, conn)
    # A connection opened before the adoption still works afterwards, because
    # the file it holds open is the one that was updated.
    assert "OnlyOnA" in {row[0] for row in early.execute("SELECT name FROM team")}
    early.execute(*_insert_team(b"\xef" * 16, "AfterAdoption"))
    early.commit()
    early.close()
    assert "AfterAdoption" in _team_names(root, participant_hex)
    # The index holds the source tree, which is what makes an interrupted
    # ref move completable by re-running.
    assert repo.write_tree() == repo._run(
        ["rev-parse", f"{source}^{{tree}}"]
    ).stdout.strip()


def test_a_logically_equal_database_is_clean_even_when_its_bytes_differ(playground_dir):
    """Publication must not manufacture a commit out of SQLite page drift.

    Adoption applies rows to the live file rather than checking out the
    source's blob, so a logically clean database is routinely byte-dirty. Here
    the drift is forced -- inserting and deleting rows leaves free pages behind
    -- so the assertion does not depend on how SQLite happened to lay out one
    particular merge.
    """
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    padding = [
        ("INSERT INTO nickname (id, name) VALUES (?, ?)", (bytes([i]) * 16, f"n{i}"))
        for i in range(1, 200)
    ]
    _apply_local(root, participant_hex, padding)
    _apply_local(root, participant_hex, [("DELETE FROM nickname WHERE name LIKE 'n%'", ())])

    assert repo.work_tree_paths_differ_from_head(["core.db"]), (
        "this test is only meaningful while the bytes actually differ"
    )
    with note_to_self_sync.write_reservation(root, participant_hex) as conn:
        assert not note_to_self_sync.live_differs_from_head(repo, conn)

    # A local row change is a real difference again.
    _apply_local(root, participant_hex, [_insert_team(b"\xcc" * 16, "Later")])
    with note_to_self_sync.write_reservation(root, participant_hex) as conn:
        assert note_to_self_sync.live_differs_from_head(repo, conn)


# ---------------------------------------------------------------------------
# Concurrency (D2, D3, D12)
# ---------------------------------------------------------------------------


def test_a_row_written_between_base_capture_and_the_transaction_survives(
    playground_dir, monkeypatch
):
    """The delta is theirs-onto-live, never live-to-merged.

    A live-to-merged delta would delete anything absent from the merged
    snapshot, which is exactly this row.
    """
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    _park(repo, "uid-a", source)

    original = note_to_self_sync._apply_source_rows

    def insert_then_apply(*args, **kwargs):
        _apply_local(root, participant_hex, [_insert_team(b"\xee" * 16, "SnuckIn")])
        return original(*args, **kwargs)

    monkeypatch.setattr(note_to_self_sync, "_apply_source_rows", insert_then_apply)

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "integrated"
    assert {"OnlyOnA", "SnuckIn"} <= set(_team_names(root, participant_hex))


def test_a_competing_writer_is_serialized_by_the_transaction_not_lost(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    _park(repo, "uid-a", source)

    transaction_open = threading.Event()
    observed_mid_transaction = []

    def competing_writer():
        transaction_open.wait(5)
        conn = sqlite3.connect(str(_shared_db(root, participant_hex)), timeout=20)
        try:
            conn.execute(*_insert_team(b"\xdd" * 16, "FromTheHub"))
            conn.commit()
        finally:
            conn.close()

    writer = threading.Thread(target=competing_writer)
    original_apply = note_to_self_sync.apply_delta

    def apply_with_barrier(conn, delta):
        original_apply(conn, delta)
        transaction_open.set()
        # Long enough for the competing writer to reach SQLite and block.
        time.sleep(0.5)
        with _live(root, participant_hex) as reader:
            observed_mid_transaction.append(
                sorted(row[0] for row in reader.execute("SELECT name FROM team"))
            )

    monkeypatch.setattr(note_to_self_sync, "apply_delta", apply_with_barrier)
    writer.start()
    try:
        [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes
    finally:
        writer.join(30)

    assert outcome.outcome == "integrated"
    # A reader spanning the transaction saw the old complete state, never a
    # partially applied one.
    assert "OnlyOnA" not in observed_mid_transaction[0]
    # And the competing write was delayed, not dropped.
    assert {"OnlyOnA", "FromTheHub"} <= set(_team_names(root, participant_hex))


def test_git_captures_a_coherent_snapshot_while_a_writer_waits(
    playground_dir, monkeypatch
):
    """D12: no SQLite writer can overlap Git's raw read of core.db."""
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    manager = TeamManager(root, participant_hex)
    manager.create_team("OnlyOnB")
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)

    def competing_writer():
        conn = sqlite3.connect(str(_shared_db(root, participant_hex)), timeout=20)
        try:
            conn.execute(*_insert_team(b"\xdd" * 16, "FromTheHub"))
            conn.commit()
        finally:
            conn.close()

    writer = threading.Thread(target=competing_writer)
    original_write_tree = Repo.write_tree

    def write_tree_with_barrier(self):
        writer.start()
        # Long enough for the writer to reach SQLite and block on the
        # reservation this call is running inside.
        time.sleep(0.5)
        return original_write_tree(self)

    monkeypatch.setattr(Repo, "write_tree", write_tree_with_barrier)
    try:
        [outcome] = manager.integrate_note_to_self().outcomes
    finally:
        writer.join(30)

    assert outcome.outcome == "integrated"
    with _tempdir() as work:
        captured = pathlib.Path(work) / "captured.db"
        repo.blob_at(outcome.recorded_head, "core.db", captured)
        with contextlib.closing(sqlite3.connect(str(captured))) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            names = {row[0] for row in conn.execute("SELECT name FROM team")}
    assert {"OnlyOnA", "OnlyOnB"} <= names
    assert "FromTheHub" not in names, "a writer overlapped Git's read"
    # The delayed write completed and is now the next ordinary local change.
    assert "FromTheHub" in _team_names(root, participant_hex)


# ---------------------------------------------------------------------------
# Failure contract (D3)
# ---------------------------------------------------------------------------


def test_an_interrupted_adoption_is_completed_by_running_it_again(
    playground_dir, monkeypatch
):
    """The rows commit, the process dies, and a rerun records the same result.

    The source carries an update as well as an insert, so the rerun proves that
    an identical update on both sides is retry-neutral rather than a conflict.
    """
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    shared_team = b"\x5a" * 16
    _apply_local(root, participant_hex, [_insert_team(shared_team, "Shared")])
    base = _commit_local(root, participant_hex, repo, "shared team")

    source = _source_commit(
        repo,
        base,
        [_insert_team(b"\xaa" * 16, "OnlyOnA"), _rename_team(shared_team, "RenamedByA")],
    )
    _apply_local(root, participant_hex, [_insert_team(b"\xbb" * 16, "OnlyOnB")])
    local_head = _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)

    files_before = _participant_files(root, participant_hex)

    def die(*args, **kwargs):
        raise RuntimeError("process died before recording")

    monkeypatch.setattr(note_to_self_sync, "_record_merge", die)
    with pytest.raises(RuntimeError):
        TeamManager(root, participant_hex).integrate_note_to_self()

    assert repo.head() == local_head, "nothing was recorded"
    assert "OnlyOnA" in _team_names(root, participant_hex), "but the rows landed"
    monkeypatch.undo()

    manager = TeamManager(root, participant_hex)
    [outcome] = manager.integrate_note_to_self().outcomes

    assert outcome.outcome == "integrated"
    assert sorted(t["name"] for t in manager.list_known_teams()) == [
        "NoteToSelf", "OnlyOnA", "OnlyOnB", "RenamedByA",
    ]
    parents = repo._run(["rev-list", "--parents", "-n", "1", "HEAD"]).stdout.split()
    assert parents[1:] == [local_head, source]
    # No watermark, lease, or recovery record was invented along the way.
    assert _participant_files(root, participant_hex) == files_before


def test_an_interrupted_fast_forward_is_completed_by_running_it_again(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    base = repo.head()
    source = _source_commit(repo, base, [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    _park(repo, "uid-a", source)

    def die(self, ref_name, new_sha):
        raise RuntimeError("process died before the ref moved")

    monkeypatch.setattr(Repo, "advance_ref", die)
    with pytest.raises(RuntimeError):
        TeamManager(root, participant_hex).integrate_note_to_self()
    assert repo.resolve_ref("refs/heads/main") == base
    monkeypatch.undo()

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "integrated"
    assert repo.resolve_ref("refs/heads/main") == source
    assert repo.write_tree() == repo._run(
        ["rev-parse", f"{source}^{{tree}}"]
    ).stdout.strip()


def test_a_git_recording_failure_reports_pending_rather_than_rollback(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    manager = TeamManager(root, participant_hex)
    manager.create_team("OnlyOnB")
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)

    def refuse(self, tree, parents, message):
        raise RepoError("commit-tree failed")

    monkeypatch.setattr(Repo, "commit_tree", refuse)

    [outcome] = manager.integrate_note_to_self().outcomes

    assert outcome.outcome == "recording_pending"
    assert "OnlyOnA" in _team_names(root, participant_hex)


def test_a_recording_reservation_timeout_reports_pending(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    manager = TeamManager(root, participant_hex)
    manager.create_team("OnlyOnB")
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)

    @contextlib.contextmanager
    def time_out(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")
        yield

    monkeypatch.setattr(note_to_self_sync, "write_reservation", time_out)

    [outcome] = manager.integrate_note_to_self().outcomes

    assert outcome.outcome == "recording_pending"
    assert "database is locked" in outcome.detail
    assert "OnlyOnA" in _team_names(root, participant_hex)


def test_a_stale_fast_forward_realigns_the_index_to_the_current_head(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    base = repo.head()
    source = _source_commit(repo, base, [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    beyond = _source_commit(
        repo, source, [_insert_team(b"\xbb" * 16, "EvenLater")], message="beyond"
    )
    _park(repo, "uid-a", source)

    original_advance = Repo.advance_ref

    def advance_after_another_writer(self, ref_name, new_sha):
        # Another writer moved `main` past the source between the index write
        # and this compare-and-swap.
        self._run(["update-ref", ref_name, beyond])
        return original_advance(self, ref_name, new_sha)

    monkeypatch.setattr(Repo, "advance_ref", advance_after_another_writer)

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "integrated"
    assert outcome.recorded_head == beyond
    assert repo.write_tree() == repo._run(
        ["rev-parse", f"{beyond}^{{tree}}"]
    ).stdout.strip()


def test_an_unresolved_merge_head_blocks_integration(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    _park(repo, "uid-a", source)
    (repo.git_dir / "MERGE_HEAD").write_text(source + "\n")

    result = TeamManager(root, participant_hex).integrate_note_to_self()

    assert result.outcomes == ()
    assert "unfinished merge" in result.blocked
    assert "OnlyOnA" not in _team_names(root, participant_hex)


# ---------------------------------------------------------------------------
# Semantic conflict (D9)
# ---------------------------------------------------------------------------


def test_the_same_team_renamed_on_both_sides_is_a_typed_refusal(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    shared_team = b"\x5a" * 16
    _apply_local(root, participant_hex, [_insert_team(shared_team, "Shared")])
    base = _commit_local(root, participant_hex, repo, "shared team")

    source = _source_commit(repo, base, [_rename_team(shared_team, "RenamedByA")])
    _apply_local(root, participant_hex, [_rename_team(shared_team, "RenamedByB")])
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)
    before = _durable_state(root, participant_hex, repo)

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "semantic_conflict"
    [conflict] = outcome.conflicts
    assert conflict.table == "team"
    assert conflict.kind == "update/update"
    assert conflict.key == (("blob", shared_team.hex()),)
    assert _durable_state(root, participant_hex, repo) == before


def test_a_non_identical_insert_collision_refuses_under_the_same_rule(playground_dir):
    """An append-like table is more suspicious on collision, not safer."""
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    base = repo.head()
    device_id = b"\xd0" * 16

    source = _source_commit(
        repo,
        base,
        [(
            "INSERT INTO user_device (id, bootstrap_encryption_key, signing_key, label) "
            "VALUES (?, ?, ?, 'From A')",
            (device_id, b"\x01" * 32, b"\x02" * 32),
        )],
    )
    _apply_local(
        root,
        participant_hex,
        [(
            "INSERT INTO user_device (id, bootstrap_encryption_key, signing_key, label) "
            "VALUES (?, ?, ?, 'From B')",
            (device_id, b"\x03" * 32, b"\x04" * 32),
        )],
    )
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "semantic_conflict"
    [conflict] = outcome.conflicts
    assert (conflict.table, conflict.kind) == ("user_device", "insert/insert")


def test_identical_changes_on_both_sides_do_not_refuse(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    shared_team = b"\x5a" * 16
    _apply_local(root, participant_hex, [_insert_team(shared_team, "Shared")])
    base = _commit_local(root, participant_hex, repo, "shared team")

    same = [_rename_team(shared_team, "AgreedName"), _insert_team(b"\xaa" * 16, "Agreed")]
    source = _source_commit(repo, base, same)
    _apply_local(root, participant_hex, same)
    _apply_local(root, participant_hex, [_insert_team(b"\xbb" * 16, "OnlyOnB")])
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "integrated"
    assert outcome.conflicts == ()
    assert set(_team_names(root, participant_hex)) == {
        "NoteToSelf", "AgreedName", "Agreed", "OnlyOnB",
    }


def test_a_unique_index_violation_is_a_constraint_refusal(playground_dir):
    """Two devices allocating the same berth: different keys, one berth_id."""
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    base = _with_shared_cloud_row(root, participant_hex, repo)

    source = _source_commit(repo, base, [_allocation(b"\xa1" * 16, "loc-A")])
    _apply_local(root, participant_hex, [_allocation(b"\xb1" * 16, "loc-B")])
    _commit_local(root, participant_hex, repo)
    _park(repo, "uid-a", source)
    before = _durable_state(root, participant_hex, repo)

    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes

    assert outcome.outcome == "constraint_refused"
    assert "UNIQUE" in outcome.detail
    assert _durable_state(root, participant_hex, repo) == before


# ---------------------------------------------------------------------------
# Source admissibility (D13, D2)
# ---------------------------------------------------------------------------


def _refuses(root, participant_hex, repo, source, *, expected="incompatible_source"):
    _park(repo, "uid-bad", source)
    before = _durable_state(root, participant_hex, repo)
    [outcome] = TeamManager(root, participant_hex).integrate_note_to_self().outcomes
    assert outcome.outcome == expected, outcome
    assert _durable_state(root, participant_hex, repo) == before
    return outcome


def test_a_source_tree_with_an_extra_path_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    with _tempdir() as work:
        stray = pathlib.Path(work) / "stray"
        stray.write_text("x")
        stray_blob = _hash_object(repo, stray)
    source = _source_commit(
        repo,
        repo.head(),
        [_insert_team(b"\xaa" * 16, "OnlyOnA")],
        extra_entries=[("100644", "blob", stray_blob, b".gitattributes")],
    )

    outcome = _refuses(root, participant_hex, repo, source)

    assert "2 entries" in outcome.detail


def test_a_source_tree_whose_core_db_is_not_a_regular_blob_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(
        repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")], mode="100755"
    )

    outcome = _refuses(root, participant_hex, repo, source)

    assert "100755" in outcome.detail


def test_a_source_tree_with_no_core_db_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(
        repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")], path=b"other.db"
    )

    outcome = _refuses(root, participant_hex, repo, source)

    assert "other.db" in outcome.detail


def test_a_corrupt_source_database_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(repo, repo.head(), raw_bytes=b"SQLite format 3\x00garbage")

    outcome = _refuses(root, participant_hex, repo, source)

    assert "not a readable SQLite database" in outcome.detail


def test_an_initial_clone_refuses_a_corrupt_source_database(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex = "ab" * 16
    initialize_bootstrap_local_state(root, participant_hex)
    repo_dir = root / "Participants" / participant_hex / "NoteToSelf" / "Sync"
    repo = Repo.init(repo_dir / ".git").with_work_tree(repo_dir)
    source = _source_commit(
        repo,
        "unused",
        raw_bytes=b"SQLite format 3\x00garbage",
        parents=[],
    )

    outcome = note_to_self_sync.adopt_source(
        root,
        participant_hex,
        repo,
        note_to_self_sync.NoteToSelfSource("refs/test/source", source),
    )

    assert outcome.outcome == "incompatible_source"
    assert "not a readable SQLite database" in outcome.detail
    assert repo.head() is None
    assert not _shared_db(root, participant_hex).exists()


def test_a_source_with_duplicate_merge_keys_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(
        repo,
        repo.head(),
        [
            _insert_team(None, "FirstNullKey"),
            _insert_team(None, "SecondNullKey"),
        ],
    )

    outcome = _refuses(root, participant_hex, repo, source)

    assert "ambiguous row identity" in outcome.detail


def test_a_source_at_an_unsupported_schema_version_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    source = _source_commit(
        repo,
        repo.head(),
        [(f"PRAGMA user_version = {SHARED_SCHEMA_VERSION + 7}", ())],
    )

    outcome = _refuses(root, participant_hex, repo, source)

    assert f"version {SHARED_SCHEMA_VERSION + 7}" in outcome.detail


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("DROP TABLE notification_service", "missing tables"),
        ("CREATE TABLE extra_table (id BLOB PRIMARY KEY)", "unexpected tables"),
        (
            "CREATE TABLE t2 (id BLOB, name TEXT NOT NULL, self_in_team BLOB NOT NULL,"
            " PRIMARY KEY (id, name))",
            "different columns or primary keys",
        ),
    ],
)
def test_a_source_whose_schema_shape_differs_is_refused(
    playground_dir, statement, expected
):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    statements = [(statement, ())]
    if "t2" in statement:
        # Rebuild `team` with a different primary key.
        statements += [
            ("INSERT INTO t2 SELECT id, name, self_in_team FROM team", ()),
            ("DROP TABLE team", ()),
            ("ALTER TABLE t2 RENAME TO team", ()),
        ]
    source = _source_commit(repo, repo.head(), statements)

    outcome = _refuses(root, participant_hex, repo, source)

    assert expected in outcome.detail


def test_two_histories_with_no_common_ancestor_are_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    unrelated = _source_commit(
        repo, repo.head(), [_insert_team(b"\xaa" * 16, "OnlyOnA")], parents=[]
    )

    outcome = _refuses(root, participant_hex, repo, unrelated)

    assert "shares no history" in outcome.detail


# ---------------------------------------------------------------------------
# The publication capture (D12)
# ---------------------------------------------------------------------------


def test_a_push_style_capture_excludes_a_concurrent_writer(playground_dir):
    """The sequence push_note_to_self runs: compare, then commit, under one hold.

    The comparison and the committed bytes must be one stable SQLite state, and
    Git's raw read of core.db must not overlap another process's writer.
    """
    root = pathlib.Path(playground_dir)
    participant_hex, repo = _participant(root)
    _apply_local(root, participant_hex, [_insert_team(b"\xbb" * 16, "OnlyOnB")])

    def competing_writer():
        conn = sqlite3.connect(str(_shared_db(root, participant_hex)), timeout=20)
        try:
            conn.execute(*_insert_team(b"\xdd" * 16, "FromTheHub"))
            conn.commit()
        finally:
            conn.close()

    writer = threading.Thread(target=competing_writer)
    with note_to_self_sync.write_reservation(root, participant_hex) as conn:
        assert note_to_self_sync.live_differs_from_head(repo, conn)
        writer.start()
        # Long enough for the writer to reach SQLite and block on this hold.
        time.sleep(0.5)
        repo.commit_paths(["core.db"], "Update NoteToSelf")
    writer.join(30)

    with _tempdir() as work:
        captured = pathlib.Path(work) / "captured.db"
        repo.blob_at("HEAD", "core.db", captured)
        with contextlib.closing(sqlite3.connect(str(captured))) as check:
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            names = {row[0] for row in check.execute("SELECT name FROM team")}
    assert "OnlyOnB" in names
    assert "FromTheHub" not in names, "a writer overlapped Git's read"
    assert "FromTheHub" in _team_names(root, participant_hex)
