"""Three-way merge of one calendar event.

`merge_event(base, ours, theirs)` takes three versions of one iCalendar object
(the version both sides started from, and each side's edit) and returns either
the merged object or the fields both sides changed in different ways.

The merge compares the event field by field:
- A field one side changed and the other did not takes the changed value.
- A field both sides changed differently is a conflict.
- DTSTART, DTEND and DURATION form one field, "time", so a move on one side
  and a resize on the other conflict instead of producing a nonsense interval.
- Subcomponents such as VALARM form one field per component type.
- Bookkeeping fields never conflict: DTSTAMP and LAST-MODIFIED take the later
  value, X-MOZ-GENERATION the larger one. When both sides contributed edits,
  SEQUENCE becomes one more than the larger side's.

VTIMEZONE blocks are not compared. Clients attach very different rule text for
the same TZID, so the merged object carries, for each TZID it refers to, the
block from ours (or from theirs if ours lacks it).

Field order and line folding follow the side each line came from; the
calendar's own properties (PRODID, VERSION, ...) come from ours.

`same_event(a, b)` says whether two versions agree on everything the merge
compares, so two versions that differ only in bookkeeping or in calendar
header count as one.

Not handled: recurrence (RRULE, RDATE, EXDATE, RECURRENCE-ID), more than one
VEVENT, and components other than VEVENT and VTIMEZONE. These raise
`Unsupported`.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Merged:
    text: bytes


@dataclass(frozen=True)
class Conflict:
    fields: tuple[str, ...]


class Unsupported(ValueError):
    """The objects use iCalendar features this merge does not handle."""


TIME_PROPS = {"DTSTART", "DTEND", "DURATION"}
RECURRENCE_PROPS = {"RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"}
LATEST = {"DTSTAMP", "LAST-MODIFIED"}
LARGEST = {"X-MOZ-GENERATION"}
BOOKKEEPING = LATEST | LARGEST | {"SEQUENCE"}


def _unfold(line):
    return re.sub(r"\r\n[ \t]", "", line)


def _name(line):
    return re.split(r"[;:]", _unfold(line), maxsplit=1)[0].upper()


def _value(line):
    # The value follows the first colon outside a quoted parameter.
    m = re.match(r'(?:[^:"]|"[^"]*")*:(.*)', _unfold(line), re.S)
    return m.group(1) if m else ""


def _lines(data):
    """Content lines, each kept in its original folded form."""
    out = []
    for raw in data.decode("utf-8").replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += "\r\n" + raw
        elif raw:
            out.append(raw)
    return out


def _split(lines):
    """Split a component's interior into its properties and its subcomponents."""
    props, blocks, depth, cur = [], [], 0, None
    for line in lines:
        name = _name(line)
        if depth == 0 and name != "BEGIN":
            props.append(line)
            continue
        if depth == 0:
            cur = []
        cur.append(line)
        depth += {"BEGIN": 1, "END": -1}.get(name, 0)
        if depth == 0:
            blocks.append((_value(cur[0]).upper(), cur))
    return props, blocks


@dataclass
class _Calendar:
    head: list
    timezones: dict
    fields: dict  # field key -> list of raw lines, in order of first appearance


def _parse(data):
    lines = _lines(data)
    if not lines or _unfold(lines[0]).upper() != "BEGIN:VCALENDAR" or _unfold(lines[-1]).upper() != "END:VCALENDAR":
        raise Unsupported("not a single VCALENDAR object")
    head, blocks = _split(lines[1:-1])
    events = [b for kind, b in blocks if kind == "VEVENT"]
    others = {kind for kind, _ in blocks} - {"VEVENT", "VTIMEZONE"}
    if len(events) != 1 or others:
        raise Unsupported("expected exactly one VEVENT and no other components")
    timezones = {}
    for kind, block in blocks:
        if kind == "VTIMEZONE":
            tzid = next((_value(l) for l in block if _name(l) == "TZID"), None)
            timezones[tzid] = block
    props, subs = _split(events[0][1:-1])
    fields = {}
    for line in props:
        name = _name(line)
        if name in RECURRENCE_PROPS:
            raise Unsupported(f"recurrence ({name})")
        fields.setdefault("time" if name in TIME_PROPS else name, []).append(line)
    for kind, block in subs:
        fields.setdefault(kind, []).extend(block)
    return _Calendar(head, timezones, fields)


def _key(lines):
    return None if lines is None else tuple(sorted(_unfold(l) for l in lines))


def _three_way(b, o, t):
    """Return (value, conflicted). Values are line lists or None for absent."""
    if _key(o) == _key(t) or _key(b) == _key(t):
        return o, False
    if _key(b) == _key(o):
        return t, False
    return None, True


def _resolve_bookkeeping(name, o, t):
    if o is None or t is None:
        return o or t
    if name in LATEST:
        return max(o, t, key=lambda ls: _value(ls[0]))
    return max(o, t, key=lambda ls: _int(ls))


def _int(lines):
    try:
        return int(_value(lines[0]).strip()) if lines else 0
    except ValueError:
        return 0


def same_event(a: bytes, b: bytes) -> bool:
    """Whether a and b say the same thing about one event.

    Compares what `merge_event` compares: every event field except the
    bookkeeping ones, ignoring line folding and the order of lines within a
    field. The calendar header and VTIMEZONE blocks are ignored too. Objects
    this module cannot parse are the same only if their bytes are.
    """
    try:
        x, y = _parse(a), _parse(b)
    except Unsupported:
        return a == b
    content = lambda c: {k: _key(v) for k, v in c.fields.items() if k not in BOOKKEEPING}
    return content(x) == content(y)


def merge_event(base: bytes, ours: bytes, theirs: bytes):
    b, o, t = _parse(base), _parse(ours), _parse(theirs)
    uids = {_key(c.fields.get("UID")) for c in (b, o, t)}
    if len(uids) != 1:
        raise Unsupported("the three versions are not the same event")

    keys = list(dict.fromkeys([*o.fields, *t.fields, *b.fields]))
    merged, conflicts = {}, []
    for k in keys:
        bv, ov, tv = b.fields.get(k), o.fields.get(k), t.fields.get(k)
        value, conflicted = _three_way(bv, ov, tv)
        if conflicted and k in BOOKKEEPING:
            value, conflicted = _resolve_bookkeeping(k, ov, tv), False
        if conflicted:
            conflicts.append(k)
        elif value is not None:
            merged[k] = value
    if conflicts:
        return Conflict(tuple(conflicts))

    content = [k for k in keys if k not in BOOKKEEPING]
    differs_from_ours = any(_key(merged.get(k)) != _key(o.fields.get(k)) for k in content)
    differs_from_theirs = any(_key(merged.get(k)) != _key(t.fields.get(k)) for k in content)
    if differs_from_ours and differs_from_theirs:
        seq = max(_int(o.fields.get("SEQUENCE")), _int(t.fields.get("SEQUENCE"))) + 1
        merged["SEQUENCE"] = [f"SEQUENCE:{seq}"]
        if "SEQUENCE" not in keys:
            keys.append("SEQUENCE")

    event_lines = [line for k in keys if k in merged for line in merged[k]]
    tzids = []
    for line in event_lines:
        for m in re.finditer(r'TZID=("?)([^";:]+)\1', _unfold(line)):
            if m.group(2) not in tzids:
                tzids.append(m.group(2))
    zones = []
    for tzid in tzids:
        block = o.timezones.get(tzid) or t.timezones.get(tzid) or b.timezones.get(tzid)
        if block:
            zones.extend(block)

    out = ["BEGIN:VCALENDAR", *o.head, *zones, "BEGIN:VEVENT", *event_lines, "END:VEVENT", "END:VCALENDAR"]
    return Merged(("\r\n".join(out) + "\r\n").encode("utf-8"))
