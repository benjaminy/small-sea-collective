"""Unit tests for harmonic_merge.core."""

import sqlite3
import tempfile
import pathlib

from harmonic_merge.core import sqlite_to_json, json_to_sqlite, merge_json_dbs


SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "small-sea-team-manager" / "small_sea_team_manager" / "sql" / "core_other_team.sql"
)

SCHEMA_SQL = SCHEMA_PATH.read_text()


def _make_db(tmp, name, members=None, invitations=None):
    """Create a small team DB and return its path."""
    db_path = pathlib.Path(tmp) / name
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    for stmt in SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.execute("PRAGMA user_version = 44")

    for m in (members or []):
        conn.execute("INSERT INTO member (id) VALUES (?)", (m,))

    for inv in (invitations or []):
        conn.execute(
            "INSERT INTO invitation (id, nonce, status, invitee_label, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            inv,
        )
    conn.commit()
    conn.close()
    return str(db_path)


def test_roundtrip():
    """sqlite_to_json -> json_to_sqlite preserves all data."""
    with tempfile.TemporaryDirectory() as tmp:
        member_id = b"\x01" * 16
        inv_id = b"\x02" * 16
        nonce = b"\x03" * 16

        orig = _make_db(
            tmp, "orig.db",
            members=[member_id],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01T00:00:00Z")],
        )

        data = sqlite_to_json(orig)
        restored = pathlib.Path(tmp) / "restored.db"
        json_to_sqlite(data, str(restored), SCHEMA_SQL)

        conn = sqlite3.connect(str(restored))
        members = conn.execute("SELECT id FROM member").fetchall()
        assert len(members) == 1
        assert members[0][0] == member_id

        invs = conn.execute("SELECT id, nonce, status, invitee_label FROM invitation").fetchall()
        assert len(invs) == 1
        assert invs[0][0] == inv_id
        assert invs[0][1] == nonce
        assert invs[0][2] == "pending"
        assert invs[0][3] == "Bob"

        uv = conn.execute("PRAGMA user_version").fetchone()[0]
        assert uv == 44
        conn.close()


def test_blob_hex_encoding():
    """BLOB values round-trip through hex encoding."""
    with tempfile.TemporaryDirectory() as tmp:
        blob_val = bytes(range(256))
        db_path = _make_db(tmp, "blob.db", members=[blob_val])

        data = sqlite_to_json(db_path)
        member_rows = data["__tables__"]["member"]
        assert len(member_rows) == 1
        assert member_rows[0]["id"] == {"__blob__": blob_val.hex()}

        restored = pathlib.Path(tmp) / "blob_restored.db"
        json_to_sqlite(data, str(restored), SCHEMA_SQL)

        conn = sqlite3.connect(str(restored))
        rows = conn.execute("SELECT id FROM member").fetchall()
        assert rows[0][0] == blob_val
        conn.close()


def test_merge_both_insert():
    """Non-conflicting insertions from both sides are kept."""
    with tempfile.TemporaryDirectory() as tmp:
        member_a = b"\x01" * 16

        ancestor = _make_db(tmp, "ancestor.db", members=[member_a])
        ours_db = _make_db(
            tmp, "ours.db",
            members=[member_a],
            invitations=[(b"\x10" * 16, b"\xaa" * 16, "pending", "Bob", "2025-01-01")],
        )
        theirs_db = _make_db(
            tmp, "theirs.db",
            members=[member_a],
            invitations=[(b"\x20" * 16, b"\xbb" * 16, "pending", "Carol", "2025-01-01")],
        )

        a_json = sqlite_to_json(ancestor)
        o_json = sqlite_to_json(ours_db)
        t_json = sqlite_to_json(theirs_db)

        merged = merge_json_dbs(a_json, o_json, t_json)

        inv_rows = merged["__tables__"]["invitation"]
        labels = {r["invitee_label"] for r in inv_rows}
        assert "Bob" in labels
        assert "Carol" in labels
        assert len(inv_rows) == 2


def test_merge_one_side_modification():
    """One-side modification is kept."""
    inv_id = b"\x10" * 16
    nonce = b"\xaa" * 16

    with tempfile.TemporaryDirectory() as tmp:
        member_a = b"\x01" * 16

        ancestor = _make_db(
            tmp, "ancestor.db",
            members=[member_a],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01")],
        )
        # Ours: changed status to accepted
        ours_db = pathlib.Path(tmp) / "ours.db"
        import shutil
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

        merged = merge_json_dbs(a_json, o_json, t_json)
        inv_rows = merged["__tables__"]["invitation"]
        assert len(inv_rows) == 1
        assert inv_rows[0]["status"] == "accepted"


def test_merge_deletion():
    """Deletion on one side removes the row."""
    inv_id = b"\x10" * 16
    nonce = b"\xaa" * 16

    with tempfile.TemporaryDirectory() as tmp:
        member_a = b"\x01" * 16

        ancestor = _make_db(
            tmp, "ancestor.db",
            members=[member_a],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01")],
        )

        # Ours: deleted the invitation
        ours_db = pathlib.Path(tmp) / "ours.db"
        import shutil
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

        merged = merge_json_dbs(a_json, o_json, t_json)
        inv_rows = merged["__tables__"]["invitation"]
        assert len(inv_rows) == 0


def test_merge_true_conflict_ours_wins():
    """True conflict (both sides change same row) — ours wins."""
    inv_id = b"\x10" * 16
    nonce = b"\xaa" * 16

    with tempfile.TemporaryDirectory() as tmp:
        member_a = b"\x01" * 16

        ancestor = _make_db(
            tmp, "ancestor.db",
            members=[member_a],
            invitations=[(inv_id, nonce, "pending", "Bob", "2025-01-01")],
        )

        import shutil

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

        merged = merge_json_dbs(a_json, o_json, t_json)
        inv_rows = merged["__tables__"]["invitation"]
        assert len(inv_rows) == 1
        assert inv_rows[0]["status"] == "accepted"  # ours wins
