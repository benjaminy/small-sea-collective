"""Core merge logic for SQLite databases."""

import sqlite3
import json
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
        col_types = {c["name"]: c["type"].upper() for c in col_info}

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


def json_to_sqlite(data, db_path, schema_sql):
    """Recreate a SQLite database from JSON data and a schema script.

    The schema is executed first, then rows from data["__tables__"] are
    inserted in the order the tables appear in the schema SQL (so that FK
    constraints are respected).
    """
    import pathlib
    pathlib.Path(db_path).unlink(missing_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    # Execute schema
    for statement in schema_sql.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
    conn.commit()

    # Determine table insertion order from schema (declaration order)
    tables_in_schema = []
    for statement in schema_sql.split(";"):
        s = statement.strip()
        upper = s.upper()
        if "CREATE TABLE" in upper:
            # Extract table name: CREATE TABLE [IF NOT EXISTS] <name>
            parts = s.split()
            idx = next(i for i, p in enumerate(parts) if p.upper() == "TABLE") + 1
            if parts[idx].upper() == "IF":
                idx += 3  # skip "IF NOT EXISTS"
            table_name = parts[idx].strip("(").strip('"').strip("'")
            tables_in_schema.append(table_name)

    tables_data = data.get("__tables__", {})

    for table_name in tables_in_schema:
        rows = tables_data.get(table_name, [])
        if not rows:
            continue
        col_names = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(col_names))
        cols = ", ".join(col_names)
        sql = f"INSERT INTO '{table_name}' ({cols}) VALUES ({placeholders})"
        for row in rows:
            values = []
            for col in col_names:
                val = row[col]
                if isinstance(val, dict) and "__blob__" in val:
                    val = bytes.fromhex(val["__blob__"])
                values.append(val)
            conn.execute(sql, values)
    conn.commit()

    # Restore pragmas
    pragmas = data.get("__pragmas__", {})
    uv = pragmas.get("user_version", 0)
    conn.execute(f"PRAGMA user_version = {int(uv)}")
    conn.commit()
    conn.close()


def merge_json_dbs(ancestor, ours, theirs):
    """Three-way merge of JSON-ified SQLite databases.

    Keyed on primary key (first column = 'id' in every table).
    Returns a merged dict in the same format as sqlite_to_json output.
    """
    merged_tables = {}

    all_table_names = set()
    all_table_names.update(ancestor.get("__tables__", {}).keys())
    all_table_names.update(ours.get("__tables__", {}).keys())
    all_table_names.update(theirs.get("__tables__", {}).keys())

    for table_name in sorted(all_table_names):
        a_rows = ancestor.get("__tables__", {}).get(table_name, [])
        o_rows = ours.get("__tables__", {}).get(table_name, [])
        t_rows = theirs.get("__tables__", {}).get(table_name, [])

        merged_tables[table_name] = _merge_table(a_rows, o_rows, t_rows, table_name)

    # Use ours' pragmas as base
    merged_pragmas = dict(ours.get("__pragmas__", {}))
    return {"__tables__": merged_tables, "__pragmas__": merged_pragmas}


def _row_key(row):
    """Extract the primary key from a row dict. Always 'id'."""
    return _normalize_key(row.get("id"))


def _normalize_key(val):
    """Normalise a key value for comparison."""
    if isinstance(val, dict) and "__blob__" in val:
        return ("blob", val["__blob__"])
    return ("val", val)


def _merge_table(a_rows, o_rows, t_rows, table_name):
    """Three-way merge of a single table's rows."""
    a_by_key = {_row_key(r): r for r in a_rows}
    o_by_key = {_row_key(r): r for r in o_rows}
    t_by_key = {_row_key(r): r for r in t_rows}

    all_keys = set()
    all_keys.update(a_by_key.keys())
    all_keys.update(o_by_key.keys())
    all_keys.update(t_by_key.keys())

    merged = []
    for key in sorted(all_keys, key=str):
        in_a = key in a_by_key
        in_o = key in o_by_key
        in_t = key in t_by_key

        if not in_a:
            # New row — added by one or both sides
            if in_o and in_t:
                # Both added same key — keep ours
                merged.append(o_by_key[key])
            elif in_o:
                merged.append(o_by_key[key])
            else:
                merged.append(t_by_key[key])
        elif in_a and not in_o and not in_t:
            # Deleted by both — gone
            pass
        elif in_a and not in_o and in_t:
            # Deleted by ours
            if t_by_key[key] != a_by_key[key]:
                # Theirs modified, ours deleted — keep deletion
                print(f"warning: delete/modify conflict in {table_name}, keeping deletion", file=sys.stderr)
            pass
        elif in_a and in_o and not in_t:
            # Deleted by theirs
            if o_by_key[key] != a_by_key[key]:
                # Ours modified, theirs deleted — keep ours
                print(f"warning: modify/delete conflict in {table_name}, keeping ours", file=sys.stderr)
                merged.append(o_by_key[key])
            pass
        else:
            # Present in all three
            o_row = o_by_key[key]
            t_row = t_by_key[key]
            a_row = a_by_key[key]

            if o_row == a_row and t_row == a_row:
                # No changes
                merged.append(o_row)
            elif o_row == a_row:
                # Only theirs changed
                merged.append(t_row)
            elif t_row == a_row:
                # Only ours changed
                merged.append(o_row)
            else:
                # Both changed — true conflict, ours wins
                if o_row != t_row:
                    print(f"warning: true conflict in {table_name}, keeping ours", file=sys.stderr)
                merged.append(o_row)

    return merged
