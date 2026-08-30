"""Core merge logic for SQLite databases."""

import contextlib
import sqlite3
from dataclasses import dataclass
from typing import Tuple


#: The four ways ours and theirs can both change one row. Every one of them is
#: reported rather than resolved: `reconcile_deltas` keeps ours, and the caller
#: decides whether that silent choice is acceptable for its data.
CONFLICT_KINDS = ("insert/insert", "delete/modify", "modify/delete", "update/update")


@dataclass(frozen=True)
class RowConflict:
    """One row both sides changed incompatibly.

    `key` is the normalised row key `compute_delta` compares on, so it is
    comparable across the three versions but is not a display string.
    """

    table: str
    key: tuple
    kind: str


class AmbiguousRowKeyError(ValueError):
    """A SQLite snapshot contains two rows with the same merge identity."""

    def __init__(self, table: str, key: tuple, snapshot: str):
        super().__init__(
            f"{snapshot} snapshot of {table} contains more than one row "
            f"with merge key {key!r}"
        )
        self.table = table
        self.key = key
        self.snapshot = snapshot


@contextlib.contextmanager
def _connection(db):
    """Yield a connection for db, which is a path or an open connection.

    A passed connection is borrowed: it is not committed, rolled back, or
    closed here, and no setting on it is left changed.
    """
    if isinstance(db, sqlite3.Connection):
        yield db
        return
    conn = sqlite3.connect(str(db))
    try:
        yield conn
    finally:
        conn.close()


def sqlite_to_json(db):
    """Convert a SQLite database to a JSON-serialisable dict.

    db is a path or an open sqlite3.Connection. Reading through a caller's
    connection is what lets a merge see live rows inside that connection's
    transaction instead of a separate snapshot of the file.

    BLOB columns are encoded as {"__blob__": "<hex>"} so the round-trip
    through JSON is lossless.
    """
    with _connection(db) as conn:
        previous_row_factory = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            return _read_tables(conn)
        finally:
            conn.row_factory = previous_row_factory


def _read_tables(conn):
    # Grab user_version pragma
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    # Discover tables (skip internal ones)
    tables_raw = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    tables = {}
    primary_keys = {}
    for tbl in tables_raw:
        table_name = tbl["name"]
        col_info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        col_names = [c["name"] for c in col_info]
        pk_columns = [c["name"] for c in sorted(col_info, key=lambda c: c["pk"]) if c["pk"]]

        rows = conn.execute(f"SELECT * FROM '{table_name}'").fetchall()
        row_dicts = []
        for row in rows:
            d = {}
            for col in col_names:
                val = row[col]
                if isinstance(val, bytes):
                    d[col] = {"__blob__": val.hex()}
                else:
                    d[col] = val
            row_dicts.append(d)
        tables[table_name] = row_dicts
        primary_keys[table_name] = pk_columns

    return {
        "__tables__": tables,
        "__pragmas__": {"user_version": user_version},
        "__primary_keys__": primary_keys,
    }


def _row_key(row, pk_columns):
    """Extract a stable comparison key for a row dict."""
    if pk_columns:
        return tuple(_normalize_key(row.get(column)) for column in pk_columns)
    if "id" in row:
        return (_normalize_key(row.get("id")),)
    return tuple((column, _normalize_key(value)) for column, value in sorted(row.items()))


def _normalize_key(val):
    """Normalise a key value for comparison."""
    if isinstance(val, dict) and "__blob__" in val:
        return ("blob", val["__blob__"])
    return ("val", val)


def _decode_value(val):
    """Convert {"__blob__": "hex"} back to bytes, pass other values through."""
    if isinstance(val, dict) and "__blob__" in val:
        return bytes.fromhex(val["__blob__"])
    return val


def compute_delta(ancestor_json, version_json):
    """Compute row-level delta between ancestor and version.

    Returns dict keyed by table name, each containing:
        inserts: {normalized_key: row_dict}
        deletes: {normalized_key: row_dict}
        updates: {normalized_key: row_dict}  (new values)
    """
    a_tables = ancestor_json.get("__tables__", {})
    v_tables = version_json.get("__tables__", {})
    a_primary_keys = ancestor_json.get("__primary_keys__", {})
    v_primary_keys = version_json.get("__primary_keys__", {})

    all_table_names = set(a_tables.keys()) | set(v_tables.keys())
    delta = {}

    for table_name in sorted(all_table_names):
        a_rows = a_tables.get(table_name, [])
        v_rows = v_tables.get(table_name, [])
        pk_columns = a_primary_keys.get(table_name) or v_primary_keys.get(table_name) or []

        a_by_key = _rows_by_key(a_rows, pk_columns, table_name, "ancestor")
        v_by_key = _rows_by_key(v_rows, pk_columns, table_name, "version")

        inserts = {}
        deletes = {}
        updates = {}

        for key, row in v_by_key.items():
            if key not in a_by_key:
                inserts[key] = row
            elif row != a_by_key[key]:
                updates[key] = row

        for key, row in a_by_key.items():
            if key not in v_by_key:
                deletes[key] = row

        if inserts or deletes or updates:
            delta[table_name] = {
                "inserts": inserts,
                "deletes": deletes,
                "updates": updates,
            }

    return delta


def _rows_by_key(rows, pk_columns, table_name, snapshot):
    """Index rows without silently collapsing an ambiguous SQLite identity.

    SQLite permits duplicate NULL values even in a non-INTEGER PRIMARY KEY,
    and tables without a declared key can contain duplicate rows. Neither case
    has a stable row identity that a three-way merge can choose between, so a
    loud refusal is the only lossless answer.
    """
    indexed = {}
    for row in rows:
        key = _row_key(row, pk_columns)
        if key in indexed:
            raise AmbiguousRowKeyError(table_name, key, snapshot)
        indexed[key] = row
    return indexed


def reconcile_deltas(ours_delta, theirs_delta) -> Tuple[dict, list]:
    """Reconcile theirs_delta against ours_delta. Ours wins on conflicts.

    Returns (cleaned, conflicts): a copy of theirs_delta with the conflicting
    operations removed, and a RowConflict for each one removed. Nothing is
    printed. A caller that wants ours-wins applies the cleaned delta and
    reports the conflicts however suits it; a caller that cannot accept a
    silent ours-wins refuses on a non-empty conflict list instead.

    Identical work on both sides is redundant rather than conflicting, for
    inserts, deletes and updates alike. That is what makes re-running a merge
    against the same ancestor produce nothing the second time.
    """
    cleaned = {}
    conflicts = []

    for table_name, t_ops in theirs_delta.items():
        o_ops = ours_delta.get(
            table_name, {"inserts": {}, "deletes": {}, "updates": {}}
        )

        new_inserts = {}
        new_deletes = {}
        new_updates = {}

        for key, row in t_ops.get("inserts", {}).items():
            ours = o_ops.get("inserts", {})
            if key in ours:
                # Both sides inserting the identical row is ordinary, not a
                # conflict: a signed row can reach one peer by courier and the
                # other through its author's own history. Rows are plain dicts
                # here and `sqlite_to_json` renders BLOBs as {"__blob__": hex},
                # so equality compares the stored bytes.
                if ours[key] != row:
                    conflicts.append(RowConflict(table_name, key, "insert/insert"))
            else:
                new_inserts[key] = row

        for key, row in t_ops.get("deletes", {}).items():
            if key in o_ops.get("deletes", {}):
                # Both deleted — redundant, drop
                pass
            elif key in o_ops.get("updates", {}):
                conflicts.append(RowConflict(table_name, key, "delete/modify"))
            else:
                new_deletes[key] = row

        for key, row in t_ops.get("updates", {}).items():
            if key in o_ops.get("deletes", {}):
                conflicts.append(RowConflict(table_name, key, "modify/delete"))
            elif key in o_ops.get("updates", {}):
                # An update both sides already made to the same values is the
                # state a re-run of an interrupted merge sees on both sides.
                if o_ops["updates"][key] != row:
                    conflicts.append(RowConflict(table_name, key, "update/update"))
            else:
                new_updates[key] = row

        if new_inserts or new_deletes or new_updates:
            cleaned[table_name] = {
                "inserts": new_inserts,
                "deletes": new_deletes,
                "updates": new_updates,
            }

    return cleaned, conflicts


def apply_delta(db, delta):
    """Apply a reconciled delta to a SQLite database in-place.

    db is a path or an open sqlite3.Connection. A borrowed connection is left
    uncommitted and open with its settings untouched, so the caller's
    transaction decides whether the delta takes effect at all; a path is opened
    with foreign keys off, committed, and closed here.

    Only touches rows that actually changed — preserves SQLite page stability.
    """
    if not delta:
        return

    borrowed = isinstance(db, sqlite3.Connection)
    conn = db if borrowed else sqlite3.connect(str(db))
    try:
        if not borrowed:
            conn.execute("PRAGMA foreign_keys = OFF")
        _apply_to_connection(conn, delta)
        if not borrowed:
            conn.commit()
    finally:
        if not borrowed:
            conn.close()


def _apply_to_connection(conn, delta):
    for table_name, ops in delta.items():
        # Get column names from the actual DB
        col_info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        col_names = [row[1] for row in col_info]
        pk_columns = [row[1] for row in sorted(col_info, key=lambda row: row[5]) if row[5]]
        if not pk_columns and "id" in col_names:
            pk_columns = ["id"]
        # `IS ?` has ordinary equality semantics for non-NULL values and also
        # lets a single nullable SQLite key be updated or deleted correctly.
        # Multiple rows under that key are rejected by `_rows_by_key`.
        where_clause = " AND ".join(f"{column} IS ?" for column in pk_columns)

        # DELETEs
        for key, row in ops.get("deletes", {}).items():
            key_values = tuple(_decode_value(row[column]) for column in pk_columns)
            conn.execute(f"DELETE FROM '{table_name}' WHERE {where_clause}", key_values)

        # INSERTs
        for key, row in ops.get("inserts", {}).items():
            placeholders = ", ".join(["?"] * len(col_names))
            cols = ", ".join(col_names)
            values = [_decode_value(row.get(c)) for c in col_names]
            conn.execute(
                f"INSERT INTO '{table_name}' ({cols}) VALUES ({placeholders})",
                values,
            )

        # UPDATEs
        for key, row in ops.get("updates", {}).items():
            set_clauses = []
            set_values = []
            for c in col_names:
                if c in pk_columns:
                    continue
                set_clauses.append(f"{c} = ?")
                set_values.append(_decode_value(row.get(c)))
            if not set_clauses:
                continue
            set_values.extend(_decode_value(row[column]) for column in pk_columns)
            conn.execute(
                f"UPDATE '{table_name}' SET {', '.join(set_clauses)} WHERE {where_clause}",
                set_values,
            )
