"""Git merge driver entry point for SQLite files.

Git invokes this as:
    harmonic-sqlite-merge %O %A %B %L %P

Where %O=ancestor, %A=ours (result written here), %B=theirs,
%L=conflict-marker-size, %P=pathname.
"""

import sys

from .core import sqlite_to_json, compute_delta, reconcile_deltas, apply_delta


def main():
    if len(sys.argv) < 4:
        print("usage: harmonic-sqlite-merge %O %A %B [%L] [%P]", file=sys.stderr)
        sys.exit(1)

    ancestor_path = sys.argv[1]
    ours_path = sys.argv[2]
    theirs_path = sys.argv[3]
    # %L and %P are optional / unused beyond logging
    pathname = sys.argv[5] if len(sys.argv) > 5 else "<unknown>"

    try:
        ancestor = sqlite_to_json(ancestor_path)
        ours = sqlite_to_json(ours_path)
        theirs = sqlite_to_json(theirs_path)

        ours_delta = compute_delta(ancestor, ours)
        theirs_delta = compute_delta(ancestor, theirs)
        cleaned = reconcile_deltas(ours_delta, theirs_delta)
        apply_delta(ours_path, cleaned)
    except Exception as e:
        print(f"harmonic-sqlite-merge failed for {pathname}: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
