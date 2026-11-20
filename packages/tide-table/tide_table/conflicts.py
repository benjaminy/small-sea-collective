"""What a person sees of a merge conflict: a placeholder event and a web page.

When a teammate's changes do not merge, the CalDAV server adds a placeholder
event to the calendar for each conflict. The placeholder sits at the time of
the event in conflict, says CONFLICT in its title, and links to a local page
where the person picks a side. The placeholder is not stored or synced; the
server makes it up from the conflicts it knows about.

A conflict is a dict as merge_driver.merge reports it.
"""

import datetime
import html
import re

HIDDEN_FIELDS = {"DTSTAMP", "LAST-MODIFIED", "SEQUENCE", "CREATED", "UID"}


def _unfolded_lines(text):
    return [line for line in re.sub(r"\n[ \t]", "", text.replace("\r\n", "\n")).split("\n") if line]


def _name(line):
    return re.split(r"[;:]", line, maxsplit=1)[0].upper()


def _value(line):
    m = re.match(r'(?:[^:"]|"[^"]*")*:(.*)', line)
    return m.group(1) if m else ""


def _event_fields(text):
    """The VEVENT's own properties, outside any subcomponent: name -> list of lines without the name."""
    fields, depth, in_event = {}, 0, False
    for line in _unfolded_lines(text):
        name = _name(line)
        if name == "BEGIN":
            depth += 1
            in_event = in_event or (depth == 2 and _value(line).upper() == "VEVENT")
        elif name == "END":
            depth -= 1
            in_event = in_event and depth >= 2
        elif in_event and depth == 2:
            fields.setdefault(name, []).append(line[len(name):].lstrip(":"))
    return fields


def _timezones(text):
    out, block = [], None
    for line in _unfolded_lines(text):
        if line.upper() == "BEGIN:VTIMEZONE":
            block = []
        if block is not None:
            block.append(line)
            if line.upper() == "END:VTIMEZONE":
                out += block
                block = None
    return out


def _escape_text(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    # 60 characters keeps each line under 75 octets for most text, as RFC 5545 asks.
    return "\r\n ".join(line[i:i + 60] for i in range(0, len(line), 60)) or line


def _summary(conflict):
    for row in conflict["rows"]:
        for text in (row["ours"], row["theirs"]):
            if text:
                summary = _event_fields(text).get("SUMMARY")
                if summary:
                    value = _value(summary[0]) if summary[0].startswith(";") else summary[0]
                    return value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\\\", "\\")
    return conflict["rows"][0]["name"]


def placeholder_name(teammate_id, conflict):
    return f"tide-table-conflict-{teammate_id[:8]}-{conflict['rows'][0]['id']}.ics"


def placeholder(teammate_id, teammate_name, conflict, link, since):
    """The placeholder event for one conflict, as iCalendar bytes.

    since is when the conflict appeared (an aware datetime); the placeholder
    raises an alarm a minute later.
    """
    source = next(text for row in conflict["rows"] for text in (row["ours"], row["theirs"]) if text)
    times = [f"{name}{line}" if line.startswith(";") else f"{name}:{line}"
             for name, lines in _event_fields(source).items() if name in ("DTSTART", "DTEND", "DURATION")
             for line in lines]
    if not any(t.startswith("DTSTART") for t in times):
        times = [f"DTSTART;VALUE=DATE:{since:%Y%m%d}"]
    stamp = since.astimezone(datetime.timezone.utc)
    alarm = stamp + datetime.timedelta(minutes=1)
    message = (f"Your changes and {teammate_name}'s do not merge ({conflict['description']}). "
               f"Choose which to keep: {link}")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Tide Table//Conflict placeholder//EN",
        *_timezones(source),
        "BEGIN:VEVENT",
        f"UID:{placeholder_name(teammate_id, conflict)[:-4]}",
        f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
        *times,
        f"SUMMARY:{_escape_text('⚠️ CONFLICT: ' + _summary(conflict))}",
        f"DESCRIPTION:{_escape_text(message)}",
        f"URL:{link}",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape_text('Calendar conflict: ' + _summary(conflict))}",
        f"TRIGGER;VALUE=DATE-TIME:{alarm:%Y%m%dT%H%M%SZ}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode("utf-8")


def _field_rows(row, teammate_name):
    sides = {side: None if row[side] is None else _event_fields(row[side]) for side in ("ours", "theirs")}
    if None in sides.values():
        gone = "yours" if sides["ours"] is None else f"{teammate_name}'s"
        note = f"<p>Deleted in {html.escape(gone)}.</p>"
    else:
        note = ""
    names = [n for side in sides.values() if side for n in side if n not in HIDDEN_FIELDS and not n.startswith("X-")]
    out = []
    for name in dict.fromkeys(names):
        cells = []
        for side in sides.values():
            if side is None:
                cells.append("<td class=gone>—</td>")
            else:
                cells.append(f"<td>{'<br>'.join(html.escape(v) for v in side.get(name, [])) or '—'}</td>")
        differs = sides["ours"] is None or sides["theirs"] is None or sides["ours"].get(name) != sides["theirs"].get(name)
        out.append(f"<tr{' class=differs' if differs else ''}><th>{html.escape(name)}</th>{''.join(cells)}</tr>")
    return note, "".join(out)


def page(conflicts, names, action, stale=False):
    """The resolution page.

    conflicts maps teammate IDs to {"details": [...], "heads": [ours, theirs]};
    names maps teammate IDs to display names; action is the URL the choices
    are posted to.
    """
    sections = []
    for teammate_id, entry in conflicts.items():
        who = names.get(teammate_id, teammate_id[:8])
        for conflict in entry["details"]:
            tables = []
            for row in conflict["rows"]:
                note, rows = _field_rows(row, who)
                tables.append(f"<h3>{html.escape(row['name'])}</h3>{note}"
                              f"<table><tr><th></th><th>Yours</th><th>{html.escape(who)}'s</th></tr>{rows}</table>")
            hidden = "".join(f'<input type=hidden name={k} value="{html.escape(v)}">' for k, v in (
                ("teammate", teammate_id), ("ours_head", entry["heads"][0] or ""), ("theirs_head", entry["heads"][1]),
                ("ids", ",".join(r["id"] for r in conflict["rows"]))))
            sections.append(
                f"<section><h2>⚠️ {html.escape(_summary(conflict))}</h2>"
                f"<p class=what>{html.escape(conflict['description'])}</p>{''.join(tables)}"
                f'<form method=post action="{html.escape(action)}">{hidden}'
                f"<button name=choice value=ours>Keep yours</button> "
                f"<button name=choice value=theirs>Keep {html.escape(who)}'s</button></form></section>")
    body = "".join(sections) or "<p>No conflicts. Everything merged.</p>"
    notice = "<p class=stale>Something changed since you loaded this page. Check the choices again.</p>" if stale else ""
    return f"""<!doctype html>
<html><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>Calendar conflicts</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; color: #222; background: #fff; }}
section {{ border: 2px solid #c62828; border-radius: 8px; padding: 0 1rem 1rem; margin: 1rem 0; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #ddd; vertical-align: top; overflow-wrap: anywhere; }}
tr.differs td {{ background: #fff3cd; font-weight: 600; }}
td.gone {{ color: #888; }}
button {{ font-size: 1rem; padding: 6px 12px; margin-top: 1rem; }}
.what {{ color: #666; }} .stale {{ background: #fff3cd; padding: 8px; }}
</style></head>
<body><h1>Calendar conflicts</h1>{notice}{body}</body></html>
""".encode("utf-8")
