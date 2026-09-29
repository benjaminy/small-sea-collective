"""Characterization micro tests: what the core.db git merge driver does to each table.

These tests assert today's behavior of `splice-sqlite-merge`, not desired
behavior. Each scenario runs the driver's entry point on an ancestor and two
diverged copies of a core.db built from the real schema. The table classes come
from `.IN_PROGRESS/issue-226-core-integration/inventory.md`. Every test carries
a comment saying whether the outcome looks right for the table's class.
"""

import pathlib
import shutil
import sqlite3
import sys

import pytest
from splice_merge import cli

SCHEMA = (
    pathlib.Path(__file__).parents[1]
    / "small_sea_manager"
    / "sql"
    / "core_other_team.sql"
)


def b(n):
    return bytes([n]) * 16


# One full row per table, keyed by n. Different n gives a different primary key
# and a different value in every UNIQUE column.
ROWS = {
    "teammate": lambda n: dict(id=b(n), display_name=f"t{n}", identity_public_key=b(n)),
    "app": lambda n: dict(id=b(n), name=f"app{n}"),
    "team_app_berth": lambda n: dict(id=b(n), app_id=b(n)),
    "berth_role": lambda n: dict(
        id=b(n), teammate_id=b(n), berth_id=b(n), role="read-write"
    ),
    "invitation": lambda n: dict(
        id=b(n), nonce=b(n), status="pending", invitee_label=None,
        role="steward", created_at="2026-01-01", accepted_at=None, accepted_by=None,
    ),
    "team_setting": lambda n: dict(key=f"k{n}", value="v"),
    "admission_proposal": lambda n: dict(
        record_id=b(n), record_type="admission_proposal",
        author_teammate_id=b(n), author_device_key_id=b(n), created_at="2026-01-01",
        anchor_commit=None, constitution_digest=b(n), constitution_snapshot_json="{}",
        schema_version=1, nonce=b(n), team_id=b(n), invitee_teammate_id=b(n),
        invitee_label_commitment=None, expires_at="2026-02-01", mode_plan="{}",
        signature=b(n), invitee_label_payload=None,
    ),
    "admission_acceptance": lambda n: dict(
        record_id=b(n), record_type="admission_acceptance",
        author_teammate_id=b(n), author_device_key_id=b(n), created_at="2026-01-01",
        anchor_commit=None, constitution_digest=None, constitution_snapshot_json=None,
        schema_version=1, subject_record_id=b(n), nonce=b(n),
        invitee_device_public_key=b(n), invitee_bootstrap_key=b(n), signature=b(n),
    ),
    "endorsement": lambda n: dict(
        record_id=b(n), record_type="endorsement",
        author_teammate_id=b(n), author_device_key_id=b(n), created_at="2026-01-01",
        anchor_commit=None, constitution_digest=b(n), constitution_snapshot_json="{}",
        schema_version=1, subject_record_id=b(n), subject_digest=b(n), signature=b(n),
    ),
    "finalization": lambda n: dict(
        record_id=b(n), record_type="finalization",
        author_teammate_id=b(n), author_device_key_id=b(n), created_at="2026-01-01",
        anchor_commit=None, constitution_digest=b(n), constitution_snapshot_json="{}",
        schema_version=1, subject_record_id=b(n), subject_digest=b(n),
        endorsement_count=1, signature=b(n),
    ),
    "admission_revocation": lambda n: dict(
        subject_record_id=b(n), revoked_at="2026-01-01"
    ),
    "team_device": lambda n: dict(
        device_key_id=b(n), teammate_id=b(n), public_key=b(n), created_at="2026-01-01"
    ),
    "key_certificate": lambda n: dict(
        cert_id=b(n), cert_type="device_link", subject_key_id=b(n),
        subject_public_key=b(n), issuer_key_id=b(n), issuer_teammate_id=b(n),
        issued_at="2026-01-01", claims="{}", signature=b(n),
    ),
    "teammate_berth_storage_announcement": lambda n: dict(
        announcement_id=b(n), teammate_id=b(n), berth_id=b(n), protocol="s3",
        url="https://a", location="loc", announced_at="2026-01-01",
        signer_key_id=b(n), signature=b(n),
    ),
    "integration_mode_change": lambda n: dict(
        record_id=b(n), record_type="integration_mode_change",
        author_teammate_id=b(n), author_device_key_id=b(n), created_at="2026-01-01",
        anchor_commit=None, constitution_digest=b(n), constitution_snapshot_json="{}",
        schema_version=1, teammate_id=b(n), berth_id=b(n), mode="automatic",
        signature=b(n),
    ),
    "device_prekey_bundle": lambda n: dict(
        device_key_id=b(n), prekey_bundle_json="{}", published_at="2026-01-01"
    ),
    "workhorse_delegation": lambda n: dict(
        record_id=b(n), schema_version=1, berth_id=b(n), workhorse_public_key="w",
        delegator_teammate_id=b(n), delegator_public_key=b(n), signature=b(n),
    ),
    "constitution_event": lambda n: dict(
        event_id=b(n), event_type="key_certificate", encoded=b(n)
    ),
    "constitution_event_pending": lambda n: dict(event_id=b(n), encoded=b(n)),
}

# (table, primary key column, a non-key column, first new value, second new value).
# The column is one a writer could plausibly change; the values differ from the
# default in ROWS.
MUTABLE = {
    "teammate": ("id", "display_name", "alice", "bob"),
    "app": ("id", "name", "x", "y"),
    "team_app_berth": ("id", "app_id", b(90), b(91)),
    "berth_role": ("id", "teammate_id", b(90), b(91)),
    "invitation": ("id", "status", "accepted", "revoked"),
    "team_setting": ("key", "value", "2", "3"),
    "admission_proposal": ("record_id", "expires_at", "2026-03-01", "2026-04-01"),
    "admission_acceptance": ("record_id", "created_at", "2026-03-01", "2026-04-01"),
    "endorsement": ("record_id", "created_at", "2026-03-01", "2026-04-01"),
    "finalization": ("record_id", "endorsement_count", 2, 3),
    "admission_revocation": ("subject_record_id", "revoked_at", "2026-03-01", "2026-04-01"),
    "team_device": ("device_key_id", "public_key", b(90), b(91)),
    "key_certificate": ("cert_id", "claims", '{"a":1}', '{"a":2}'),
    "teammate_berth_storage_announcement": ("announcement_id", "url", "https://b", "https://c"),
    "integration_mode_change": ("record_id", "mode", "proposal-only", "proposal-only2"),
    "device_prekey_bundle": ("device_key_id", "prekey_bundle_json", "{1}", "{2}"),
    "workhorse_delegation": ("record_id", "workhorse_public_key", "w2", "w3"),
    "constitution_event": ("event_id", "encoded", b(90), b(91)),
    "constitution_event_pending": ("event_id", "encoded", b(90), b(91)),
}
# integration_mode_change.mode is CHECKed to two values, so it cannot take two
# different new values. Use a free column instead.
MUTABLE["integration_mode_change"] = (
    "record_id", "created_at", "2026-03-01", "2026-04-01",
)

CLASS = {
    "teammate": "unclear", "app": "unclear", "team_app_berth": "unclear",
    "berth_role": "projection", "invitation": "unclear", "team_setting": "unclear",
    "admission_proposal": "signed", "admission_acceptance": "signed",
    "endorsement": "signed", "finalization": "signed",
    "admission_revocation": "unclear", "team_device": "projection",
    "key_certificate": "signed", "teammate_berth_storage_announcement": "signed",
    "integration_mode_change": "signed", "device_prekey_bundle": "unclear",
    "workhorse_delegation": "signed", "constitution_event": "signed",
    "constitution_event_pending": "projection",
}

TABLES = sorted(ROWS)


def _insert(conn, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))


def _update(conn, table, pk, key, col, value):
    conn.execute(f"UPDATE {table} SET {col} = ? WHERE {pk} = ?", (value, key))


def _rows(path, table):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()


def _edit(path, fn, foreign_keys=False):
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
        fn(conn)
        conn.commit()
    finally:
        conn.close()


class Merge:
    """An ancestor and two diverged copies of a core.db."""

    def __init__(self, tmp_path, capsys, monkeypatch):
        self.ancestor = str(tmp_path / "ancestor.db")
        self.ours = str(tmp_path / "ours.db")
        self.theirs = str(tmp_path / "theirs.db")
        self.capsys = capsys
        self.monkeypatch = monkeypatch
        conn = sqlite3.connect(self.ancestor)
        conn.executescript(SCHEMA.read_text())
        conn.commit()
        conn.close()

    def seed(self, fn):
        """Change the ancestor before the copies are made."""
        _edit(self.ancestor, fn)

    def fork(self):
        shutil.copy(self.ancestor, self.ours)
        shutil.copy(self.ancestor, self.theirs)

    def run(self):
        """Run the driver as git would. Returns (exit code, stderr)."""
        self.capsys.readouterr()
        argv = ["splice-sqlite-merge", self.ancestor, self.ours, self.theirs, "0", "core.db"]
        self.monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        return exit_info.value.code, self.capsys.readouterr().err


@pytest.fixture
def merge(tmp_path, capsys, monkeypatch):
    return Merge(tmp_path, capsys, monkeypatch)


def test_every_schema_table_is_characterized(merge):
    conn = sqlite3.connect(merge.ancestor)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    conn.close()
    assert names == set(ROWS) == set(MUTABLE) == set(CLASS)


@pytest.mark.parametrize("table", TABLES)
def test_concurrent_inserts_with_different_keys_are_both_kept(merge, table):
    # Right for every class: distinct rows from two devices are simply unioned.
    merge.fork()
    _edit(merge.ours, lambda c: _insert(c, table, ROWS[table](1)))
    _edit(merge.theirs, lambda c: _insert(c, table, ROWS[table](2)))
    code, err = merge.run()
    assert code == 0 and err == ""
    expected = sorted(map(repr, [ROWS[table](1), ROWS[table](2)]))
    assert sorted(map(repr, _rows(merge.ours, table))) == expected


@pytest.mark.parametrize("table", TABLES)
def test_identical_insert_on_both_sides_is_not_a_conflict(merge, table):
    # Right for signed history and shared data: the same signed row arriving by two routes is one row.
    merge.fork()
    for path in (merge.ours, merge.theirs):
        _edit(path, lambda c: _insert(c, table, ROWS[table](1)))
    code, err = merge.run()
    assert code == 0 and err == ""
    assert _rows(merge.ours, table) == [ROWS[table](1)]


@pytest.mark.parametrize("table", TABLES)
def test_insert_with_same_key_and_different_content_keeps_ours(merge, table):
    pk, col, v1, v2 = MUTABLE[table]
    merge.fork()
    _edit(merge.ours, lambda c: _insert(c, table, {**ROWS[table](1), col: v1}))
    _edit(merge.theirs, lambda c: _insert(c, table, {**ROWS[table](1), col: v2}))
    code, err = merge.run()
    assert code == 0
    assert f"insert/insert conflict in {table}, keeping ours" in err
    assert _rows(merge.ours, table) == [{**ROWS[table](1), col: v1}]
    # Signed history: keeping one of two signed rows with one id should never happen, so a warning is right.
    # Projection and shared data: dropping theirs is acceptable only if the class is last-writer-wins, which is undecided.


@pytest.mark.parametrize("table", TABLES)
def test_conflicting_updates_keep_ours_and_warn(merge, table):
    pk, col, v1, v2 = MUTABLE[table]
    merge.seed(lambda c: _insert(c, table, ROWS[table](1)))
    merge.fork()
    key = ROWS[table](1)[pk]
    _edit(merge.ours, lambda c: _update(c, table, pk, key, col, v1))
    _edit(merge.theirs, lambda c: _update(c, table, pk, key, col, v2))
    code, err = merge.run()
    assert code == 0
    assert f"true conflict in {table}, keeping ours" in err
    assert _rows(merge.ours, table)[0][col] == v1
    # Signed history: updates should not exist, so this outcome only matters for tampering, and keeping ours is fine.
    # Projection: fine, because the projection can be recomputed. Shared data: theirs is lost silently apart from stderr.


@pytest.mark.parametrize("table", TABLES)
def test_update_on_one_side_applies_cleanly(merge, table):
    pk, col, v1, _ = MUTABLE[table]
    merge.seed(lambda c: _insert(c, table, ROWS[table](1)))
    merge.fork()
    key = ROWS[table](1)[pk]
    _edit(merge.theirs, lambda c: _update(c, table, pk, key, col, v1))
    code, err = merge.run()
    assert code == 0 and err == ""
    assert _rows(merge.ours, table)[0][col] == v1
    # Wrong for signed history: a peer can rewrite a signed row and the merge accepts it, so the signature no longer matches.
    # Right for projection and shared data: the peer's change is taken.


@pytest.mark.parametrize("table", TABLES)
def test_delete_on_one_side_applies_cleanly(merge, table):
    merge.seed(lambda c: _insert(c, table, ROWS[table](1)))
    merge.fork()
    _edit(merge.theirs, lambda c: c.execute(f"DELETE FROM {table}"))
    code, err = merge.run()
    assert code == 0 and err == ""
    assert _rows(merge.ours, table) == []
    # Wrong for signed history and the append-only tables: a peer can erase a signed row and the merge accepts it.
    # Right for projection tables (berth_role, team_device, constitution_event_pending), which legitimately lose rows.


@pytest.mark.parametrize("table", TABLES)
def test_their_delete_against_our_update_keeps_our_row(merge, table):
    pk, col, v1, _ = MUTABLE[table]
    merge.seed(lambda c: _insert(c, table, ROWS[table](1)))
    merge.fork()
    key = ROWS[table](1)[pk]
    _edit(merge.ours, lambda c: _update(c, table, pk, key, col, v1))
    _edit(merge.theirs, lambda c: c.execute(f"DELETE FROM {table}"))
    code, err = merge.run()
    assert code == 0
    assert f"delete/modify conflict in {table}, keeping ours" in err
    assert _rows(merge.ours, table)[0][col] == v1
    # Looks right for every class: the surviving row is not lost, and a warning is printed.


@pytest.mark.parametrize("table", TABLES)
def test_our_delete_against_their_update_keeps_the_deletion(merge, table):
    pk, col, v1, _ = MUTABLE[table]
    merge.seed(lambda c: _insert(c, table, ROWS[table](1)))
    merge.fork()
    key = ROWS[table](1)[pk]
    _edit(merge.ours, lambda c: c.execute(f"DELETE FROM {table}"))
    _edit(merge.theirs, lambda c: _update(c, table, pk, key, col, v1))
    code, err = merge.run()
    assert code == 0
    assert f"modify/delete conflict in {table}, keeping ours" in err
    assert _rows(merge.ours, table) == []
    # Same rule as above, but here ours is the delete, so the peer's edit is dropped.
    # Right for projection; for signed history the local deletion should not have happened.


def test_changes_to_different_columns_of_one_row_still_conflict(merge):
    # Wrong for shared data: the merge works on whole rows, so a peer's identity_public_key change is lost
    # because ours changed display_name. A column-level merge would keep both.
    merge.seed(lambda c: _insert(c, "teammate", ROWS["teammate"](1)))
    merge.fork()
    _edit(merge.ours, lambda c: _update(c, "teammate", "id", b(1), "display_name", "alice"))
    _edit(merge.theirs, lambda c: _update(c, "teammate", "id", b(1), "identity_public_key", b(9)))
    code, err = merge.run()
    assert code == 0
    assert "true conflict in teammate, keeping ours" in err
    row = _rows(merge.ours, "teammate")[0]
    assert row["display_name"] == "alice"
    assert row["identity_public_key"] == b(1)


def test_two_endorsements_by_one_teammate_make_the_merge_fail(merge):
    # Wrong for signed history: two devices of one steward each endorse the same proposal with distinct record ids.
    # Each insert is fine alone; together they break UNIQUE (subject_record_id, author_teammate_id),
    # so the driver exits nonzero and git treats the merge as conflicted.
    merge.fork()
    first = {**ROWS["endorsement"](1)}
    second = {**ROWS["endorsement"](2), "subject_record_id": b(1), "author_teammate_id": b(1)}
    _edit(merge.ours, lambda c: _insert(c, "endorsement", first))
    _edit(merge.theirs, lambda c: _insert(c, "endorsement", second))
    code, err = merge.run()
    assert code == 1
    assert "splice-sqlite-merge failed" in err and "UNIQUE" in err
    assert _rows(merge.ours, "endorsement") == [first]


def test_deleting_a_teammate_on_one_side_leaves_an_orphan_device_from_the_other(merge):
    # Wrong for projection and shared data: the merge applies deltas with foreign keys off,
    # so the result has a team_device whose teammate no longer exists.
    merge.seed(lambda c: _insert(c, "teammate", ROWS["teammate"](1)))
    merge.seed(lambda c: _insert(c, "team_device", ROWS["team_device"](1)))
    merge.fork()
    _edit(merge.ours, lambda c: c.execute("DELETE FROM teammate WHERE id = ?", (b(1),)), foreign_keys=True)
    assert _rows(merge.ours, "team_device") == []  # the cascade removed it on our side
    _edit(merge.theirs, lambda c: _insert(c, "team_device", {**ROWS["team_device"](2), "teammate_id": b(1)}))
    code, err = merge.run()
    assert code == 0 and err == ""
    assert _rows(merge.ours, "teammate") == []
    assert [r["device_key_id"] for r in _rows(merge.ours, "team_device")] == [b(2)]
    conn = sqlite3.connect(merge.ours)
    try:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() != []
    finally:
        conn.close()


def test_two_devices_activating_one_app_produce_duplicate_rows(merge):
    # Wrong for shared data: ids are random uuid7 values, so both devices' "app X" rows survive.
    # The Manager's `_single_app_id_by_name_sa` then raises "Multiple app rows found".
    merge.fork()
    _edit(merge.ours, lambda c: _insert(c, "app", dict(id=b(1), name="X")))
    _edit(merge.theirs, lambda c: _insert(c, "app", dict(id=b(2), name="X")))
    code, err = merge.run()
    assert code == 0 and err == ""
    assert sorted(r["id"] for r in _rows(merge.ours, "app")) == [b(1), b(2)]


def test_two_devices_projecting_one_berth_role_produce_duplicate_rows(merge):
    # Wrong for projection: each device inserts its own berth_role id for the same (teammate, berth),
    # and the schema has no UNIQUE on that pair, so both survive with different roles.
    merge.fork()
    pair = dict(teammate_id=b(5), berth_id=b(6))
    _edit(merge.ours, lambda c: _insert(c, "berth_role", dict(id=b(1), role="read-write", **pair)))
    _edit(merge.theirs, lambda c: _insert(c, "berth_role", dict(id=b(2), role="read-only", **pair)))
    code, err = merge.run()
    assert code == 0 and err == ""
    roles = {r["id"]: r["role"] for r in _rows(merge.ours, "berth_role")}
    assert roles == {b(1): "read-write", b(2): "read-only"}


def test_conflicting_quorum_settings_keep_ours(merge):
    # Unclear class: the quorum is a governance parameter, and last-writer-wins by merge order is probably wrong for it.
    merge.seed(lambda c: _insert(c, "team_setting", dict(key="admission_quorum", value="1")))
    merge.fork()
    _edit(merge.ours, lambda c: _update(c, "team_setting", "key", "admission_quorum", "value", "2"))
    _edit(merge.theirs, lambda c: _update(c, "team_setting", "key", "admission_quorum", "value", "3"))
    code, err = merge.run()
    assert code == 0
    assert "true conflict in team_setting, keeping ours" in err
    assert _rows(merge.ours, "team_setting") == [dict(key="admission_quorum", value="2")]
