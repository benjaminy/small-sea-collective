"""Chaos scenario S05: Git failure after SQLite commit is retryable.

Integrate the default unknown event from a parked source commit, but make
``Repo.commit`` raise once, after the SQLite transaction for the new event has
already committed. The integration must report
``integrated`` / ``git_record_failed`` / ``new_events=1``: the event must
persist in the reopened receiver database and the receiver's Git HEAD must
remain unchanged. Removing the patch, replaying the same SHA must return
``no_change`` with no error while recording the dirty Core database in Git,
and replaying a second time must change neither the event rows nor HEAD.

If the committed database is reported as rolled back, or the replay skips the
missing Git capture, the persistence or dirty-Core assertions fail.
"""

import sqlite3

from cod_sync.repo import Repo
from test_core_union import _fixture, _integrate
from wrasse_trust.events import encode_event


def _event_rows(sync):
    with sqlite3.connect(sync / "core.db") as conn:
        return conn.execute(
            "SELECT event_id, event_type, encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()


def test_s05_git_failure_after_sqlite_commit_is_retryable(playground_dir, monkeypatch):
    args = _fixture(playground_dir)
    root, alice_hex, sync, repo, sha, _ref, event = args

    # Step 1: capture the receiver's event rows and HEAD.
    rows_before = _event_rows(sync)
    head_before = repo.head()

    # Step 2: make Repo.commit raise once during integration.
    calls = {"n": 0}
    real_commit = Repo.commit

    def flaky_commit(self, message):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated git failure")
        return real_commit(self, message)

    monkeypatch.setattr(Repo, "commit", flaky_commit)

    # Step 3: integrate and reopen the receiver database.
    result1 = _integrate(args)
    assert result1 == {"outcome": "integrated", "code": "git_record_failed", "new_events": 1}, result1
    expected_rows = sorted(
        rows_before + [(event.event_id, event.event_type, encode_event(event))],
        key=lambda row: row[0],
    )
    assert _event_rows(sync) == expected_rows, (
        "event did not persist in the receiver database after the Git failure"
    )
    assert repo.head() == head_before, (
        "receiver HEAD moved even though the Git commit failed"
    )
    assert repo.work_tree_paths_differ_from_head(["core.db"]) is True, (
        "Core database is not dirty in the worktree after the Git failure; "
        "a retry would have nothing to record"
    )

    # Step 4: remove the patch, replay the same SHA, then replay once more.
    monkeypatch.undo()

    result2 = _integrate(args)
    assert result2 == {"outcome": "no_change", "code": None, "new_events": 0}, result2
    head_after_retry = repo.head()
    assert head_after_retry != head_before, (
        "retry did not record the dirty Core database in Git"
    )
    assert repo.work_tree_paths_differ_from_head(["core.db"]) is False, (
        "retry left the Core database dirty in Git"
    )
    assert _event_rows(sync) == expected_rows, (
        "retry changed the receiver's event rows"
    )

    result3 = _integrate(args)
    assert result3 == {"outcome": "no_change", "code": None, "new_events": 0}, result3
    assert repo.head() == head_after_retry, (
        "second retry moved receiver HEAD"
    )
    assert _event_rows(sync) == expected_rows, (
        "second retry changed the receiver's event rows"
    )
