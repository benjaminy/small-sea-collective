"""The git merge driver for a Tide Table calendar file.

Git runs it as `python -m tide_table.merge_driver %O %A %B %L %P` with the
base, ours and theirs versions of calendar.sqlite, and expects the merged
calendar written over ours. CalendarRepo registers it when creating a
calendar.

The merge works on rows, through Splice Merge's export and row deltas:
- An event a side holds in a version that is the same event as the base's
  (`same_event`) counts as unchanged on that side.
- An event both sides edited goes to `merge_event`.
- Any other row both sides changed, such as an edit against a delete, is a
  conflict. So are two events with one name or one UID afterwards.

When histories cross, git first merges their candidate bases, and passes a
conflict-marker size above the default 7 to say so. A conflict there must
not fail: git would carry on with one side as the base and quietly settle the
disagreement. Instead each row in the conflict, including both rows of a
duplicate name or UID, gets NO_AGREED_BASE as its version in the merged base.
In the real merge, an event with that base is settled only if both sides hold
the same event, or neither holds it.

If $TIDE_TABLE_MERGE_REPORT names a file, the real merge writes there, as
JSON, the names of the events merged field by field and each conflict with
both sides' versions. The driver exits 1 on conflict.

If $TIDE_TABLE_MERGE_RESOLUTIONS names a file, it holds a person's choices
for conflicts, as JSON mapping event IDs to "ours" or "theirs" (see `merge`).
"""

import json
import os
import pathlib
import sqlite3
import sys

from splice_merge.core import apply_delta, compute_delta, reconcile_deltas, sqlite_to_json

from tide_table.merge import Merged, Unsupported, merge_event, same_event

NO_AGREED_BASE = b"TIDE-TABLE:NO-AGREED-BASE"
DEFAULT_MARKER_SIZE = 7


def _ics(row):
    return bytes.fromhex(row["ics"]["__blob__"])


def _with_ics(row, data):
    return {**row, "ics": {"__blob__": data.hex()}}


def _events(export):
    """The export's event rows by normalised key, the form Splice Merge's deltas use."""
    return {(("blob", r["id"]["__blob__"]),): r for r in export["__tables__"].get("events", [])}


def _unchanged_as_base(base, side):
    """In side's export, put back the base's row for each event that is the same event as the base's."""
    base_rows = {r["id"]["__blob__"]: r for r in base["__tables__"].get("events", [])}
    rows = side["__tables__"].get("events", [])
    for i, row in enumerate(rows):
        was = base_rows.get(row["id"]["__blob__"])
        if was and _ics(was) != NO_AGREED_BASE and same_event(_ics(was), _ics(row)):
            rows[i] = was


def _hex(key):
    return key[0][1]


def merge(base_path, ours_path, theirs_path, building_base, resolutions=None):
    """Merge theirs into ours in place. Returns (names merged field by field, conflicts).

    Each conflict is a dict: a "description", and "rows", the events involved,
    each with its "id" (hex), "name", and its "ours" and "theirs" iCalendar
    text (None where that side has no such event).

    resolutions maps event IDs (hex) to "ours" or "theirs". A conflict whose
    events all have the same choice is settled: each of its events takes that
    side's version, or is deleted if that side has none. Resolutions apply
    only to the real merge, not while building a base.

    With conflicts and not building_base, ours is left unchanged.
    """
    resolutions = {} if building_base else (resolutions or {})
    base, ours, theirs = (sqlite_to_json(p) for p in (base_path, ours_path, theirs_path))
    for side in (ours, theirs):
        _unchanged_as_base(base, side)
    cleaned, row_conflicts = reconcile_deltas(compute_delta(base, ours), compute_delta(base, theirs))
    b_rows, o_rows, t_rows = _events(base), _events(ours), _events(theirs)

    chosen = {}  # key -> the row it ends up as, or None for deleted

    def resolve(keys):
        sides = {resolutions.get(_hex(k)) for k in keys}
        if len(sides) != 1 or sides & {None}:
            return False
        side_rows = o_rows if sides == {"ours"} else t_rows
        for k in keys:
            chosen[k] = side_rows.get(k)
        return True

    def conflict(description, keys):
        text = lambda row: None if row is None else _ics(row).decode("utf-8", "replace")
        return {"description": description,
                "rows": [{"id": _hex(k), "name": (o_rows.get(k) or t_rows.get(k) or b_rows[k])["name"],
                          "ours": text(o_rows.get(k)), "theirs": text(t_rows.get(k))} for k in keys]}

    merged, conflicts, unsettled = [], [], {}
    for c in row_conflicts:
        o, t = o_rows.get(c.key), t_rows.get(c.key)
        name = (o or t or b_rows.get(c.key))["name"]
        if c.table == "events" and c.kind == "update/update" and (o["name"], o["uid"]) == (t["name"], t["uid"]):
            b = b_rows[c.key]
            if _ics(b) == NO_AGREED_BASE:
                if same_event(_ics(o), _ics(t)):
                    continue  # both sides settled on the same event; ours already holds it
            else:
                try:
                    result = merge_event(_ics(b), _ics(o), _ics(t))
                except Unsupported:
                    result = None
                if isinstance(result, Merged):
                    updates = cleaned.setdefault("events", {"inserts": {}, "deletes": {}, "updates": {}})["updates"]
                    updates[c.key] = _with_ics(o, result.text)
                    merged.append(name)
                    continue
        if resolve([c.key]):
            continue
        conflicts.append(conflict(f"{c.kind} {name}", [c.key]))
        unsettled[c.key] = o or t

    # Two events with one name or UID are a disagreement about which row is the
    # event, so in a merged base both rows get NO_AGREED_BASE too. Otherwise
    # each side's choice of its own row would look like deleting the other's.
    events_delta = cleaned.get("events", {})
    for _, rows in _duplicates(_result(o_rows, events_delta, chosen)):
        resolve([_key_of(row) for row in rows])
    for description, rows in _duplicates(_result(o_rows, events_delta, chosen)):
        conflicts.append(conflict(description, [_key_of(row) for row in rows]))
        for row in rows:
            unsettled[_key_of(row)] = row
    if conflicts and not building_base:
        return merged, conflicts
    for section in events_delta.values():
        for key in chosen:
            section.pop(key, None)
    apply_delta(ours_path, cleaned)
    if unsettled or chosen:
        with sqlite3.connect(ours_path) as conn:
            for key, row in chosen.items():
                conn.execute("DELETE FROM events WHERE id = ?", (bytes.fromhex(_hex(key)),))
                if row is not None:
                    conn.execute("INSERT INTO events (id, name, uid, ics) VALUES (?, ?, ?, ?)",
                                 (bytes.fromhex(_hex(key)), row["name"], row["uid"], _ics(row)))
            for row in unsettled.values():
                conn.execute("INSERT OR REPLACE INTO events (id, name, uid, ics) VALUES (?, ?, ?, ?)",
                             (bytes.fromhex(row["id"]["__blob__"]), row["name"], row["uid"], NO_AGREED_BASE))
        conn.close()
    return merged, conflicts


def _key_of(row):
    return (("blob", row["id"]["__blob__"]),)


def _result(ours_rows, delta, chosen):
    """The event rows ours would hold once delta and the chosen rows are applied."""
    rows = dict(ours_rows)
    for key in delta.get("deletes", {}):
        rows.pop(key, None)
    rows.update(delta.get("inserts", {}))
    rows.update(delta.get("updates", {}))
    for key, row in chosen.items():
        if row is None:
            rows.pop(key, None)
        else:
            rows[key] = row
    return rows


def _duplicates(rows):
    """Two events among rows that share a name or a UID.

    Returns a (description, (row, row)) pair for each.
    """
    out = []
    for column in ("name", "uid"):
        seen = {}
        for row in rows.values():
            value = row[column]
            if value is None:
                continue
            if value in seen:
                out.append((f"two events with {column} {value}: {seen[value]['name']} and {row['name']}",
                            (seen[value], row)))
            seen.setdefault(value, row)
    return out


def main():
    base, ours, theirs, marker = sys.argv[1:5]
    building_base = int(marker) > DEFAULT_MARKER_SIZE
    try:
        resolutions_path = os.environ.get("TIDE_TABLE_MERGE_RESOLUTIONS")
        resolutions = json.loads(pathlib.Path(resolutions_path).read_text()) if resolutions_path else None
        merged, conflicts = merge(base, ours, theirs, building_base, resolutions)
    except Exception as e:
        print(f"tide-table merge driver failed: {e!r}", file=sys.stderr)
        sys.exit(2)
    if building_base:
        return
    report = os.environ.get("TIDE_TABLE_MERGE_REPORT")
    if report:
        with open(report, "w") as f:
            json.dump({"merged": merged, "conflicts": conflicts}, f)
    if conflicts:
        for c in conflicts:
            print(f"tide-table: {c['description']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
