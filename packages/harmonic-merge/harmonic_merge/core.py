"""Core merge logic for SQLite databases."""

import sqlite3
import sys


def sqlite_to_json(db_path):
    """Convert a SQLite database to a JSON-serialisable dict.

    BLOB columns are encoded as {"__blob__": "<hex>"} so the round-trip
    through JSON is lossless.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Grab user_version pragma
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    # Discover tables (skip internal ones)
    tables_raw = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    tables = {}
    for tbl in tables_raw:
        table_name = tbl["name"]
        col_info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        col_names = [c["name"] for c in col_info]

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

    conn.close()
    return {"__tables__": tables, "__pragmas__": {"user_version": user_version}}


def _row_key(row):
    """Extract the primary key from a row dict. Always 'id'."""
    return _normalize_key(row.get("id"))


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

    all_table_names = set(a_tables.keys()) | set(v_tables.keys())
    delta = {}

    for table_name in sorted(all_table_names):
        a_rows = a_tables.get(table_name, [])
        v_rows = v_tables.get(table_name, [])

        a_by_key = {_row_key(r): r for r in a_rows}
        v_by_key = {_row_key(r): r for r in v_rows}

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


def reconcile_deltas(ours_delta, theirs_delta):
    """Reconcile theirs_delta against ours_delta. Ours wins on conflicts.

    Returns a cleaned copy of theirs_delta with conflicts removed.
    """
    cleaned = {}

    for table_name, t_ops in theirs_delta.items():
        o_ops = ours_delta.get(table_name, {"inserts": {}, "deletes": {}, "updates": {}})

        new_inserts = {}
        new_deletes = {}
        new_updates = {}

        for key, row in t_ops.get("inserts", {}).items():
            if key in o_ops.get("inserts", {}):
                print(f"warning: insert/insert conflict in {table_name}, keeping ours", file=sys.stderr)
            else:
                new_inserts[key] = row

        for key, row in t_ops.get("deletes", {}).items():
            if key in o_ops.get("deletes", {}):
                # Both deleted — redundant, drop
                pass
            elif key in o_ops.get("updates", {}):
                print(f"warning: delete/modify conflict in {table_name}, keeping ours", file=sys.stderr)
            else:
                new_deletes[key] = row

        for key, row in t_ops.get("updates", {}).items():
            if key in o_ops.get("deletes", {}):
                print(f"warning: modify/delete conflict in {table_name}, keeping ours", file=sys.stderr)
            elif key in o_ops.get("updates", {}):
                print(f"warning: true conflict in {table_name}, keeping ours", file=sys.stderr)
            else:
                new_updates[key] = row

        if new_inserts or new_deletes or new_updates:
            cleaned[table_name] = {
                "inserts": new_inserts,
                "deletes": new_deletes,
                "updates": new_updates,
            }

    return cleaned


def apply_delta(db_path, delta):
    """Apply a reconciled delta to a SQLite database in-place.

    Only touches rows that actually changed — preserves SQLite page stability.
    """
    if not delta:
        return

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")

    for table_name, ops in delta.items():
        # Get column names from the actual DB
        col_info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        col_names = [row[1] for row in col_info]

        # DELETEs
        for key, row in ops.get("deletes", {}).items():
            id_val = _decode_value(row["id"])
            conn.execute(f"DELETE FROM '{table_name}' WHERE id = ?", (id_val,))

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
            id_val = _decode_value(row["id"])
            set_clauses = []
            set_values = []
            for c in col_names:
                if c == "id":
                    continue
                set_clauses.append(f"{c} = ?")
                set_values.append(_decode_value(row.get(c)))
            set_values.append(id_val)
            conn.execute(
                f"UPDATE '{table_name}' SET {', '.join(set_clauses)} WHERE id = ?",
                set_values,
            )

    conn.commit()
    conn.close()
