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
