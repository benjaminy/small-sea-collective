"""Micro tests for splice_merge.core (delta-based merge)."""

import pathlib
import shutil
import sqlite3
import tempfile

import pytest

from splice_merge.core import (
    AmbiguousRowKeyError,
    apply_delta,
    compute_delta,
    reconcile_deltas,
    sqlite_to_json,
)

SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "small-sea-manager"
    / "small_sea_manager"
    / "sql"
    / "core_other_team.sql"
)

SCHEMA_SQL = SCHEMA_PATH.read_text()


def _make_db(tmp, name, teammates=None, invitations=None):
    """Create a small team DB and return its path."""
    db_path = pathlib.Path(tmp) / name
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    for stmt in SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.execute("PRAGMA user_version = 44")

    for m in teammates or []:
        conn.execute("INSERT INTO teammate (id) VALUES (?)", (m,))

    for inv in invitations or []:
        conn.execute(
            "INSERT INTO invitation (id, nonce, status, invitee_label, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            inv,
        )
    conn.commit()
    conn.close()
    return str(db_path)


def _query_table(db_path, table, columns="*"):
    """Helper to query a table and return list of dicts."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT {columns} FROM {table}").fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


def test_merge_both_insert():
    """Non-conflicting insertions from both sides are kept."""
    with tempfile.TemporaryDirectory() as tmp:
        teammate_a = b"\x01" * 16

        ancestor = _make_db(tmp, "ancestor.db", teammates=[teammate_a])
        ours_db = _make_db(
            tmp,
            "ours.db",
            teammates=[teammate_a],
            invitations=[(b"\x10" * 16, b"\xaa" * 16, "pending", "Bob", "2025-01-01")],
        )
        theirs_db = _make_db(
            tmp,
            "theirs.db",
            teammates=[teammate_a],
            invitations=[
                (b"\x20" * 16, b"\xbb" * 16, "pending", "Carol", "2025-01-01")
            ],
        )

        a_json = sqlite_to_json(ancestor)
        o_json = sqlite_to_json(ours_db)
        t_json = sqlite_to_json(theirs_db)

        ours_delta = compute_delta(a_json, o_json)
        theirs_delta = compute_delta(a_json, t_json)
        cleaned, _conflicts = reconcile_deltas(ours_delta, theirs_delta)
        apply_delta(ours_db, cleaned)

        inv_rows = _query_table(ours_db, "invitation")
        labels = {r["invitee_label"] for r in inv_rows}
        assert "Bob" in labels
        assert "Carol" in labels
        assert len(inv_rows) == 2


def test_merge_one_side_modification():
    """One-side modification (ours) is kept."""
    inv_id = b"\x10" * 16
    nonce = b"\xaa" * 16

    with tempfile.TemporaryDirectory() as tmp:
        teammate_a = b"\x01" * 16

        ancestor = _make_db(
            tmp,
            "ancestor.db",
            teammates=[teammate_a],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01")],
        )
        # Ours: changed status to accepted
        ours_db = pathlib.Path(tmp) / "ours.db"
        shutil.copy(ancestor, str(ours_db))
        conn = sqlite3.connect(str(ours_db))
        conn.execute("UPDATE invitation SET status='accepted' WHERE id=?", (inv_id,))
        conn.commit()
        conn.close()

        # Theirs: unchanged
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        shutil.copy(ancestor, str(theirs_db))

        a_json = sqlite_to_json(ancestor)
        o_json = sqlite_to_json(str(ours_db))
        t_json = sqlite_to_json(str(theirs_db))

        ours_delta = compute_delta(a_json, o_json)
        theirs_delta = compute_delta(a_json, t_json)
        cleaned, _conflicts = reconcile_deltas(ours_delta, theirs_delta)
        apply_delta(str(ours_db), cleaned)

        inv_rows = _query_table(str(ours_db), "invitation")
        assert len(inv_rows) == 1
        assert inv_rows[0]["status"] == "accepted"


def test_theirs_only_modification():
    """Theirs changes a row, ours doesn't — update is applied to ours DB."""
    inv_id = b"\x10" * 16
    nonce = b"\xaa" * 16

    with tempfile.TemporaryDirectory() as tmp:
        teammate_a = b"\x01" * 16

        ancestor = _make_db(
            tmp,
            "ancestor.db",
            teammates=[teammate_a],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01")],
        )
        # Ours: unchanged
        ours_db = pathlib.Path(tmp) / "ours.db"
        shutil.copy(ancestor, str(ours_db))

        # Theirs: changed status to accepted
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        shutil.copy(ancestor, str(theirs_db))
        conn = sqlite3.connect(str(theirs_db))
        conn.execute("UPDATE invitation SET status='accepted' WHERE id=?", (inv_id,))
        conn.commit()
        conn.close()

        a_json = sqlite_to_json(ancestor)
        o_json = sqlite_to_json(str(ours_db))
        t_json = sqlite_to_json(str(theirs_db))

        ours_delta = compute_delta(a_json, o_json)
        theirs_delta = compute_delta(a_json, t_json)
        cleaned, _conflicts = reconcile_deltas(ours_delta, theirs_delta)
        apply_delta(str(ours_db), cleaned)

        inv_rows = _query_table(str(ours_db), "invitation")
        assert len(inv_rows) == 1
        assert inv_rows[0]["status"] == "accepted"


def test_merge_deletion():
    """Deletion on one side removes the row."""
    inv_id = b"\x10" * 16
    nonce = b"\xaa" * 16

    with tempfile.TemporaryDirectory() as tmp:
        teammate_a = b"\x01" * 16

        ancestor = _make_db(
            tmp,
            "ancestor.db",
            teammates=[teammate_a],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01")],
        )

        # Ours: deleted the invitation
        ours_db = pathlib.Path(tmp) / "ours.db"
        shutil.copy(ancestor, str(ours_db))
        conn = sqlite3.connect(str(ours_db))
        conn.execute("DELETE FROM invitation WHERE id=?", (inv_id,))
        conn.commit()
        conn.close()

        # Theirs: unchanged
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        shutil.copy(ancestor, str(theirs_db))

        a_json = sqlite_to_json(ancestor)
        o_json = sqlite_to_json(str(ours_db))
        t_json = sqlite_to_json(str(theirs_db))

        ours_delta = compute_delta(a_json, o_json)
        theirs_delta = compute_delta(a_json, t_json)
        cleaned, _conflicts = reconcile_deltas(ours_delta, theirs_delta)
        apply_delta(str(ours_db), cleaned)

        inv_rows = _query_table(str(ours_db), "invitation")
        assert len(inv_rows) == 0


def test_merge_true_conflict_ours_wins():
    """True conflict (both sides change same row) — ours wins."""
    inv_id = b"\x10" * 16
    nonce = b"\xaa" * 16

    with tempfile.TemporaryDirectory() as tmp:
        teammate_a = b"\x01" * 16

        ancestor = _make_db(
            tmp,
            "ancestor.db",
            teammates=[teammate_a],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01")],
        )

        ours_db = pathlib.Path(tmp) / "ours.db"
        shutil.copy(ancestor, str(ours_db))
        conn = sqlite3.connect(str(ours_db))
        conn.execute("UPDATE invitation SET status='accepted' WHERE id=?", (inv_id,))
        conn.commit()
        conn.close()

        theirs_db = pathlib.Path(tmp) / "theirs.db"
        shutil.copy(ancestor, str(theirs_db))
        conn = sqlite3.connect(str(theirs_db))
        conn.execute("UPDATE invitation SET status='rejected' WHERE id=?", (inv_id,))
        conn.commit()
        conn.close()

        a_json = sqlite_to_json(ancestor)
        o_json = sqlite_to_json(str(ours_db))
        t_json = sqlite_to_json(str(theirs_db))

        ours_delta = compute_delta(a_json, o_json)
        theirs_delta = compute_delta(a_json, t_json)
        cleaned, _conflicts = reconcile_deltas(ours_delta, theirs_delta)
        apply_delta(str(ours_db), cleaned)

        inv_rows = _query_table(str(ours_db), "invitation")
        assert len(inv_rows) == 1
        assert inv_rows[0]["status"] == "accepted"  # ours wins


def test_merge_non_id_primary_key_rows():
    """Non-id primary keys merge cleanly when both sides insert different rows."""
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(tmp, "ancestor.db", teammates=[b"\x01" * 16])
        ours_db = pathlib.Path(tmp) / "ours.db"
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        shutil.copy(ancestor, str(ours_db))
        shutil.copy(ancestor, str(theirs_db))

        for db_path, device_key_id, payload in (
            (ours_db, b"a" * 16, '{"row":"a"}'),
            (theirs_db, b"b" * 16, '{"row":"b"}'),
        ):
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                INSERT INTO device_prekey_bundle
                (device_key_id, prekey_bundle_json, published_at)
                VALUES (?, ?, ?)
                """,
                (device_key_id, payload, "2026-04-13T00:00:00+00:00"),
            )
            conn.commit()
            conn.close()

        a_json = sqlite_to_json(ancestor)
        o_json = sqlite_to_json(str(ours_db))
        t_json = sqlite_to_json(str(theirs_db))

        ours_delta = compute_delta(a_json, o_json)
        theirs_delta = compute_delta(a_json, t_json)
        cleaned, _conflicts = reconcile_deltas(ours_delta, theirs_delta)
        apply_delta(str(ours_db), cleaned)

        bundle_rows = _query_table(
            str(ours_db),
            "device_prekey_bundle",
            "hex(device_key_id) AS device_key_id",
        )
        device_key_ids = {row["device_key_id"] for row in bundle_rows}
        assert (b"a" * 16).hex().upper() in device_key_ids
        assert (b"b" * 16).hex().upper() in device_key_ids


def _insert_announcement(db_path, *, announcement_id, location, signature):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO teammate_berth_storage_announcement "
        "(announcement_id, teammate_id, berth_id, protocol, url, location, "
        "announced_at, signer_key_id, signature) "
        "VALUES (?, ?, ?, 's3', 'http://example', ?, '2026-01-01', ?, ?)",
        (
            announcement_id,
            b"\x01" * 16,
            b"\x02" * 16,
            location,
            b"\x03" * 16,
            signature,
        ),
    )
    conn.commit()
    conn.close()


def test_identical_insert_from_both_sides_is_not_a_conflict():
    """A signed row can arrive by courier and again through its author's history.

    After issue #183 every routed invitation produces exactly this case: the
    invitee commits the announcement and the inviter inserts the couriered copy.
    """
    announcement_id = b"\x20" * 16
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(tmp, "ancestor.db", teammates=[b"\x01" * 16])

        ours_db = pathlib.Path(tmp) / "ours.db"
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        shutil.copy(ancestor, str(ours_db))
        shutil.copy(ancestor, str(theirs_db))
        for db in (ours_db, theirs_db):
            _insert_announcement(
                db,
                announcement_id=announcement_id,
                location="bucket-a",
                signature=b"\xee" * 64,
            )

        a_json = sqlite_to_json(ancestor)
        cleaned, conflicts = reconcile_deltas(
            compute_delta(a_json, sqlite_to_json(str(ours_db))),
            compute_delta(a_json, sqlite_to_json(str(theirs_db))),
        )
        apply_delta(str(ours_db), cleaned)

        assert conflicts == []
        rows = _query_table(str(ours_db), "teammate_berth_storage_announcement")
        assert len(rows) == 1
        assert rows[0]["location"] == "bucket-a"
        assert rows[0]["signature"] == b"\xee" * 64


def test_divergent_insert_under_one_id_conflicts_and_keeps_ours():
    announcement_id = b"\x20" * 16
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(tmp, "ancestor.db", teammates=[b"\x01" * 16])

        ours_db = pathlib.Path(tmp) / "ours.db"
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        shutil.copy(ancestor, str(ours_db))
        shutil.copy(ancestor, str(theirs_db))
        _insert_announcement(
            ours_db,
            announcement_id=announcement_id,
            location="bucket-a",
            signature=b"\xee" * 64,
        )
        _insert_announcement(
            theirs_db,
            announcement_id=announcement_id,
            location="bucket-b",
            signature=b"\xff" * 64,
        )

        a_json = sqlite_to_json(ancestor)
        cleaned, conflicts = reconcile_deltas(
            compute_delta(a_json, sqlite_to_json(str(ours_db))),
            compute_delta(a_json, sqlite_to_json(str(theirs_db))),
        )
        apply_delta(str(ours_db), cleaned)

        assert [(c.table, c.kind) for c in conflicts] == [
            ("teammate_berth_storage_announcement", "insert/insert")
        ]
        rows = _query_table(str(ours_db), "teammate_berth_storage_announcement")
        assert len(rows) == 1
        assert rows[0]["location"] == "bucket-a"


def test_identical_update_from_both_sides_is_not_a_conflict():
    """A retry after an interrupted merge sees the source's update on both sides.

    Nothing is left to apply and nothing is reported, which is what makes the
    Manager's "re-run the same operation" repair story produce no second effect.
    """
    inv_id = b"\x10" * 16
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(
            tmp,
            "ancestor.db",
            teammates=[b"\x01" * 16],
            invitations=[(inv_id, b"\xaa" * 16, "pending", "Bob", "2025-01-01")],
        )
        ours_db = pathlib.Path(tmp) / "ours.db"
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        for db in (ours_db, theirs_db):
            shutil.copy(ancestor, str(db))
            conn = sqlite3.connect(str(db))
            conn.execute("UPDATE invitation SET status='accepted' WHERE id=?", (inv_id,))
            conn.commit()
            conn.close()

        a_json = sqlite_to_json(ancestor)
        cleaned, conflicts = reconcile_deltas(
            compute_delta(a_json, sqlite_to_json(str(ours_db))),
            compute_delta(a_json, sqlite_to_json(str(theirs_db))),
        )

        assert conflicts == []
        assert cleaned == {}


def test_identical_delete_from_both_sides_is_not_a_conflict():
    inv_id = b"\x10" * 16
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(
            tmp,
            "ancestor.db",
            teammates=[b"\x01" * 16],
            invitations=[(inv_id, b"\xaa" * 16, "pending", "Bob", "2025-01-01")],
        )
        ours_db = pathlib.Path(tmp) / "ours.db"
        theirs_db = pathlib.Path(tmp) / "theirs.db"
        for db in (ours_db, theirs_db):
            shutil.copy(ancestor, str(db))
            conn = sqlite3.connect(str(db))
            conn.execute("DELETE FROM invitation WHERE id=?", (inv_id,))
            conn.commit()
            conn.close()

        a_json = sqlite_to_json(ancestor)
        cleaned, conflicts = reconcile_deltas(
            compute_delta(a_json, sqlite_to_json(str(ours_db))),
            compute_delta(a_json, sqlite_to_json(str(theirs_db))),
        )

        assert conflicts == []
        assert cleaned == {}


def _conflict_case(tmp, ours_sql, theirs_sql):
    """Build ancestor/ours/theirs around one invitation and reconcile them."""
    inv_id = b"\x10" * 16
    ancestor = _make_db(
        tmp,
        "ancestor.db",
        teammates=[b"\x01" * 16],
        invitations=[(inv_id, b"\xaa" * 16, "pending", "Bob", "2025-01-01")],
    )
    ours_db = pathlib.Path(tmp) / "ours.db"
    theirs_db = pathlib.Path(tmp) / "theirs.db"
    for db, statement in ((ours_db, ours_sql), (theirs_db, theirs_sql)):
        shutil.copy(ancestor, str(db))
        conn = sqlite3.connect(str(db))
        conn.execute(statement, (inv_id,))
        conn.commit()
        conn.close()

    a_json = sqlite_to_json(ancestor)
    cleaned, conflicts = reconcile_deltas(
        compute_delta(a_json, sqlite_to_json(str(ours_db))),
        compute_delta(a_json, sqlite_to_json(str(theirs_db))),
    )
    return inv_id, cleaned, conflicts


def test_reconcile_names_table_key_and_kind_for_every_conflict():
    """All four conflict kinds come back identified, and none reaches the delta."""
    delete = "DELETE FROM invitation WHERE id=?"
    accept = "UPDATE invitation SET status='accepted' WHERE id=?"
    reject = "UPDATE invitation SET status='rejected' WHERE id=?"

    cases = {
        "delete/modify": (accept, delete),
        "modify/delete": (delete, reject),
        "update/update": (accept, reject),
    }
    for kind, (ours_sql, theirs_sql) in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            inv_id, cleaned, conflicts = _conflict_case(tmp, ours_sql, theirs_sql)
            assert cleaned == {}, kind
            assert len(conflicts) == 1, kind
            conflict = conflicts[0]
            assert conflict.table == "invitation"
            assert conflict.kind == kind
            assert conflict.key == (("blob", inv_id.hex()),)


def test_sqlite_to_json_borrows_a_connection_without_disturbing_it():
    """Reading through a caller's connection returns its uncommitted rows."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_db(tmp, "live.db", teammates=[b"\x01" * 16])
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO teammate (id) VALUES (?)", (b"\x02" * 16,))

        live = sqlite_to_json(conn)

        assert len(live["__tables__"]["teammate"]) == 2
        assert conn.in_transaction, "the borrowed transaction must still be open"
        assert conn.row_factory is sqlite3.Row
        conn.execute("ROLLBACK")
        # Still usable, so nothing closed it.
        assert conn.execute("SELECT COUNT(*) FROM teammate").fetchone()[0] == 1
        conn.close()


def test_apply_delta_on_a_borrowed_connection_neither_commits_nor_closes():
    """The caller's transaction decides whether a delta takes effect."""
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(tmp, "ancestor.db", teammates=[b"\x01" * 16])
        theirs_db = _make_db(
            tmp,
            "theirs.db",
            teammates=[b"\x01" * 16],
            invitations=[(b"\x20" * 16, b"\xbb" * 16, "pending", "Carol", "2025-01-01")],
        )
        live_path = pathlib.Path(tmp) / "live.db"
        shutil.copy(ancestor, str(live_path))

        a_json = sqlite_to_json(ancestor)
        cleaned, conflicts = reconcile_deltas(
            compute_delta(a_json, sqlite_to_json(str(live_path))),
            compute_delta(a_json, sqlite_to_json(theirs_db)),
        )
        assert conflicts == []

        conn = sqlite3.connect(str(live_path))
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        apply_delta(conn, cleaned)
        assert conn.in_transaction
        conn.execute("ROLLBACK")
        assert conn.execute("SELECT COUNT(*) FROM invitation").fetchone()[0] == 0

        conn.execute("BEGIN IMMEDIATE")
        apply_delta(conn, cleaned)
        conn.execute("COMMIT")
        conn.close()

        assert len(_query_table(str(live_path), "invitation")) == 1


def test_duplicate_nullable_primary_keys_are_rejected_instead_of_collapsed():
    """SQLite permits this state, but a row merge cannot identify both rows."""
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(tmp, "ancestor.db", teammates=[b"\x01" * 16])
        version = pathlib.Path(tmp) / "version.db"
        shutil.copy(ancestor, version)
        with sqlite3.connect(version) as conn:
            conn.execute(
                "INSERT INTO invitation (id, nonce, invitee_label, created_at) "
                "VALUES (NULL, ?, 'Alice', '2025-01-01')",
                (b"\xaa" * 16,),
            )
            conn.execute(
                "INSERT INTO invitation (id, nonce, invitee_label, created_at) "
                "VALUES (NULL, ?, 'Bob', '2025-01-01')",
                (b"\xbb" * 16,),
            )

        with pytest.raises(AmbiguousRowKeyError) as exc:
            compute_delta(sqlite_to_json(ancestor), sqlite_to_json(version))

        assert exc.value.table == "invitation"
        assert exc.value.snapshot == "version"
        assert exc.value.key == (("val", None),)


def test_one_nullable_primary_key_can_be_updated_losslessly():
    with tempfile.TemporaryDirectory() as tmp:
        ancestor = _make_db(tmp, "ancestor.db", teammates=[b"\x01" * 16])
        with sqlite3.connect(ancestor) as conn:
            conn.execute(
                "INSERT INTO invitation (id, nonce, status, invitee_label, created_at) "
                "VALUES (NULL, ?, 'pending', 'Alice', '2025-01-01')",
                (b"\xaa" * 16,),
            )
        version = pathlib.Path(tmp) / "version.db"
        live = pathlib.Path(tmp) / "live.db"
        shutil.copy(ancestor, version)
        shutil.copy(ancestor, live)
        with sqlite3.connect(version) as conn:
            conn.execute("UPDATE invitation SET status='accepted' WHERE id IS NULL")

        delta = compute_delta(sqlite_to_json(ancestor), sqlite_to_json(version))
        apply_delta(live, delta)

        assert _query_table(live, "invitation")[0]["status"] == "accepted"
