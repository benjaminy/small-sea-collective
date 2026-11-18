# Tide Table Diary

Terse dated notes on what we tried and learned.
Newest entries at the bottom.

## 2026-11-18 — Aim at a demo

Goal: two teammates share a calendar; one moves an event while the other edits its notes; the edits merge with no human help.
Clients: Apple Calendar and Thunderbird.
This skips ahead of `ROADMAP.md` Phases 1–2 on purpose, but keeps a short Phase 0 client spike first.

Known merge traps:
- Splice Merge reports any row changed on both sides as an update/update conflict, so one row per event cannot merge "moved" with "notes edited".
  Needs one row per property, or a column-level merge.
- Clients rewrite `SEQUENCE`, `DTSTAMP`, `LAST-MODIFIED` on every edit, so both sides always touch them.
  We need a rule for them (for example, take the larger `SEQUENCE` and recompute the rest).

## 2026-11-18 — Two-teammate sync already works

Files tests (`ssc-files/tests/test_hub_sync.py`, `test_scenarios.py`, `test_removed_teammate_history.py`; 25 pass) show two teammates syncing app data through the Hub:
a real invitation, a separate MinIO for each teammate, signed commits, encrypted objects in the cloud.
Shortcuts there: both teammates share one in-process Hub and one data directory; sessions are approved automatically; setup uses `Provisioning` helpers.
Tide Table can copy the Files sync pattern; the new work is CalDAV and the merge.

## 2026-11-18 — Spike server

`spikes/caldav_spike.py`: standard-library CalDAV server, one user, one calendar, events as files.
Logs every request and response in full to `Scratch/tide-table-spike/<user>/requests.log`.
Supports discovery (`/.well-known/caldav`, current-user-principal, calendar-home-set), PROPFIND, GET/PUT/DELETE with ETag preconditions, and the REPORTs calendar-multiget, calendar-query (filters ignored), and sync-collection.
PROPPATCH claims success but stores nothing.
Checked with curl only; real clients not tried yet.

## 2026-11-18 — First client contact

Thunderbird: account added with Basic auth over plain HTTP; discovered and synced the calendar.
Apple Calendar (macOS 27.0.1, `accountsd`): "Unable to verify account name or password."
The log shows Apple never sent credentials: each probe got a 401 with a Basic challenge and Apple moved on.
It also guessed a Google-style path, `/calendar/dav/alice/user/`.
Likely cause: Apple will not send a Basic password over plain HTTP, even to localhost.
Change: the server now offers Digest auth (MD5, qop=auth) alongside Basic.
If Apple still refuses, next try is HTTPS with a self-signed certificate.

## 2026-11-18 — Both clients create events

Digest worked: Apple Calendar added the account, found the calendar, and created an event.
Apple does not send credentials ahead of time, so each of its requests gets a 401 and a retry; harmless on localhost.
Each client sees the other's events.

Neither client learns about changes on its own quickly.
Thunderbird showed Apple's event only after a manual sync; the log shows no polling between them.
The server has no way to tell clients that something changed, so how soon they see changes depends on each client's refresh interval.
For the demo: set both to refresh every minute, or refresh by hand on camera.

What the clients wrote, which matters for merge:
- Same `TZID` (`America/Denver`), very different `VTIMEZONE` bodies: Thunderbird's spans the full history since 1883; Apple's has only the current rules.
  If we compare `VTIMEZONE` text, the same event edited in each client will always conflict.
  Treat `VTIMEZONE` as derived from `TZID`, not as event data.
- Apple writes `SEQUENCE:0`; Thunderbird writes no `SEQUENCE`.
- Apple adds a placeholder `VALARM` (`ACTION:NONE`, trigger in 1976) and `X-APPLE-*` properties.
  These must survive a round trip through the merge.
- Both set `CREATED`, `DTSTAMP`, `LAST-MODIFIED` from the device clock.

## 2026-11-18 — Concurrent edit against one server

Event created in Thunderbird (13:00–14:00).
Apple moved it to 15:15–16:15 and saved: `PUT` with `If-Match`, 204.
Thunderbird, without syncing, added `LOCATION` and saved: `PUT` with the stale `If-Match`, 412.
Thunderbird showed "item changed on server"; the user chose to submit anyway.
Thunderbird then sent the same event with no `If-Match`, 204.
Result: the event is back at 13:00–14:00 with the location. Apple's move is silently lost.

So a plain CalDAV server, given a stale write, can either refuse it, which leads to a scary dialog, or let the client overwrite the event, which loses the other edit.
That is exactly the problem Tide Table has to solve.

Idea: Tide Table keeps every version it has served, keyed by ETag (the git history gives us this).
A `PUT` whose `If-Match` names an older version is then a three-way merge (base = that version, ours = the current one, theirs = the request body), not a 412.
The client gets 204 and, on its next sync, the merged event.
Open: a forced `PUT` with no `If-Match` names no base; we would have to guess one (for example, the last version served to that client).

Other details from this run:
- Both clients set `SEQUENCE:1` on their edits, independently. "Take the larger" ties; the merged event probably needs `max + 1`.
- Thunderbird added `X-MOZ-GENERATION`, incremented on each save.
- Apple rewrote the whole event in its own property order.

## 2026-11-18 — Two places to merge

The demo runs two Tide Table servers, one per simulated teammate, syncing through the Hub when each user chooses.
One person running two calendar apps against one server is a second case.
Both need the same three-way merge (base, ours, theirs → merged event or conflict); only the base comes from different places:
- On save: a stale `PUT` within one server. Base = the version named by `If-Match`.
- On sync: pulling a teammate's history. Base = the git merge base.
The save case also arises with one app per person: a sync can bring in a teammate's edit while the app still holds the old copy.

## 2026-11-18 — Merge module and merge-on-save

`tide_table/merge.py`: `merge_event(base, ours, theirs)` returns `Merged(text)` or `Conflict(fields)`; raises `Unsupported` for recurrence or more than one event.
It merges field by field, without Splice Merge: Splice Merge merges SQLite rows, and we have not yet decided how events are stored.
Rules:
- Start, end, and duration are one field, `time`, so a move on one side and a resize on the other conflict.
- `VTIMEZONE` text is ignored; the merged object takes the block for each `TZID` from ours, else theirs.
- `DTSTAMP`/`LAST-MODIFIED` take the later value; `X-MOZ-GENERATION` the larger.
- `SEQUENCE` becomes the larger plus one when both sides contributed edits.
- Unknown properties and `VALARM`s are carried through.

Micro tests: `tests/test_merge.py`, 15 pass, including the recorded Apple/Thunderbird run (`tests/fixtures/move-vs-location/`).
On those fixtures the merge keeps Apple's move and Thunderbird's location and sets `SEQUENCE:2`.

The spike now keeps every body it stores under `versions/<etag>.ics`.
A `PUT` whose `If-Match` names an older stored version is merged instead of refused, and the reply has no `ETag`, so the client must refetch.
Replaying the recorded saves with curl: Thunderbird's stale save now gets 204 and the merged event; a conflicting location gets 412; an unknown base gets 412.
Not yet tried with the real apps.

## 2026-11-18 — Merge-on-save with the real apps

New event created in Thunderbird; Apple moved it; Thunderbird, without syncing, added a location.
Thunderbird's stale `PUT` was merged (204, no `ETag`); Thunderbird showed no alert and immediately refetched, picking up Apple's time change.
Apple did not show the location until a manual refresh.
Its earlier automatic check had seen the change tag move (6 → 7) without fetching the event; after ⌘R it ran sync-collection and multiget and showed the merged event.
So Apple sometimes checks the calendar list without fetching events; when it fetches events seems to be up to Apple.
For the demo, refresh by hand.
