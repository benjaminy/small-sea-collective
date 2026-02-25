"""Git merge driver entry point for SQLite files.

Git invokes this as:
    harmonic-sqlite-merge %O %A %B %L %P

Where %O=ancestor, %A=ours (result written here), %B=theirs,
%L=conflict-marker-size, %P=pathname.

The schema SQL path is read from HARMONIC_MERGE_SCHEMA env var.
"""

import os
import sys

from .core import sqlite_to_json, json_to_sqlite, merge_json_dbs


def main():
    if len(sys.argv) < 4:
        print("usage: harmonic-sqlite-merge %O %A %B [%L] [%P]", file=sys.stderr)
        sys.exit(1)

    ancestor_path = sys.argv[1]
    ours_path = sys.argv[2]
    theirs_path = sys.argv[3]
    # %L and %P are optional / unused beyond logging
    pathname = sys.argv[5] if len(sys.argv) > 5 else "<unknown>"

    schema_path = os.environ.get("HARMONIC_MERGE_SCHEMA")
    if not schema_path:
        print("error: HARMONIC_MERGE_SCHEMA env var not set", file=sys.stderr)
        sys.exit(1)

    with open(schema_path, "r") as f:
        schema_sql = f.read()

    try:
        ancestor = sqlite_to_json(ancestor_path)
        ours = sqlite_to_json(ours_path)
        theirs = sqlite_to_json(theirs_path)

        merged = merge_json_dbs(ancestor, ours, theirs)

        # Write merged result back to %A (ours) — git expects this
        json_to_sqlite(merged, ours_path, schema_sql)
    except Exception as e:
        print(f"harmonic-sqlite-merge failed for {pathname}: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
