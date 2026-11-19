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
The first version included request and response bodies in `Scratch/tide-table-spike/<user>/requests.log`.
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

## 2026-11-18 — CalDAV review follow-up

`calendar-multiget` now applies the same event path checks as direct requests.
The stale `PUT` lookup accepts only ETags in the format the spike generates.

The server builds a sync report and reads its token while holding the store lock, so a concurrent write cannot advance the token past a change the report omits.
`PUT` and `DELETE` hold that same lock from the ETag check through the write, including any merge on save.

`requests.log` now records request and response metadata by default.
Pass `--log-bodies` to inspect payloads from synthetic calendars.
Existing logs from earlier runs can still contain plaintext event data.

## 2026-11-18 — Two teammates sync through the Hub

`tide_table/calendar_repo.py`: each teammate's calendar is a git repo, one `.ics` file per event; saving commits.
Sync uses Cod Sync directly: publish to your own store, fetch a teammate's history into `refs/peers/<teammate>/main`, merge.
Git does not merge event text: `.git/info/attributes` sets `*.ics -merge`, so any event both sides changed comes back as a conflict, and `merge` calls `merge_event` with git's base, ours and theirs.
If any event does not merge, `merge` aborts and changes nothing; the teammate's history stays parked in its ref.
A line-by-line git merge would have combined a new `DTSTART` from one side with a new `DTEND` from the other.

`tests/test_hub_sync.py` runs the demo with no calendar apps: Alice and Bob, separate MinIO servers, encrypted objects, a real invitation.
Alice creates the recorded event; Bob syncs; Alice applies Apple's move, Bob Thunderbird's location; both publish; Alice merges; Bob fast-forwards to her merge.
Both end with the move, the location, and `SEQUENCE:2`, at the same commit.
Shortcuts, mostly copied from the Files tests: one in-process Hub and data directory, sessions approved automatically, Bob's sender key and storage announcement handed to Alice directly (#138).
Calendar commits are unsigned and fetched history is not verified; the Files tests show how to sign through the Hub.

`tests/test_calendar_repo.py` covers the same with folder stores, plus:
- Both teammates merge at the same time: each merge takes the calendar header and zone from its own side, so the two merge commits differ; one more round of sync settles on a single history.
- A real conflict (two different locations) leaves both repos untouched.

## 2026-11-18 — Two CalDAV servers, synced on demand

The spike's event directory is now a `CalendarRepo`: every `PUT` and `DELETE` is a commit.
`spikes/two_teammates_demo.py` sets up the team (the Hub test's setup), starts Alice's server on 5232 and Bob's on 5233, and syncs a teammate when you type `a` or `b`.
A sync publishes that teammate's calendar, fetches the other's, merges, and publishes again if the merge made a new commit.
Events the sync changed or deleted advance the server's sync token, so the apps see them on their next refresh; merged bodies are kept as versions, so a later stale save still merges.
The server records those changes before publishing the merged head, so a publish failure leaves the local refresh working.
The demo reports Git and sync errors and keeps accepting commands so the user can retry.

Checked with HTTP requests in place of the apps:
create on Alice, sync both, Bob has it; Alice moves, Bob adds a location; Alice syncs (Bob has not published yet, so nothing new), Bob syncs and merges.
A stale save on Bob's server after that sync merged on save; the next syncs carried it to Alice with `SEQUENCE:3`.
A delete on Alice reached Bob.

Only a sync publishes, so a teammate sees your edits after you sync and then they sync.
Every run starts from scratch; the apps may need their accounts re-added after a restart.
Both teammates share one in-process Hub.

## 2026-11-18 — The demo with real apps

Apple Calendar as Alice (5232) and Thunderbird as Bob (5233), syncing by hand through the demo script.
The user reports that it worked; the steps and any surprises were not recorded here.

## 2026-11-18 — One Hub per teammate

The Hub test and the demo now run a separate Hub process for each teammate (`uvicorn small_sea_hub.server:app`), each over its own data directory, as on two computers.
Each teammate's sessions, publishes and fetches go to their own Hub over HTTP; the two Hubs never talk to each other, only to the two MinIO servers.
Manager steps (team creation, invitation, acceptance) still run as direct calls against each teammate's data directory.
The setup worked unchanged apart from that split, which is some evidence the invitation flow does not depend on a shared Hub.
Still shortcuts: sessions approved automatically, Bob's sender key and storage announcement handed to Alice directly (#138), unsigned calendar commits.

The demo deletes its data directory at start, so a second copy wipes a running one; `--data` picks another directory.

## 2026-11-18 — Apple delays uploads too

An event created in a local Apple calendar and then moved to the spike calendar reached the server noticeably later than the user expected; the server saw a single `PUT` with `If-None-Match: *` and no error.
So Apple chooses when to upload, not just when to fetch.
For the demo, refresh Apple (⌘R) and see its save in the console before syncing.

## 2026-11-18 — Signed calendar commits

Every calendar commit is now signed through the teammate's own Hub (`tide_table/hub_signer.py`), the same path Files uses: Git runs `small-sea-git-sign`, which asks the Hub to sign with the berth's key.
Each commit message carries a `Small-Sea-Work-Context` trailer with the team, berth, purpose `TideTable/calendar`, and the device's current transitional authority view.
Publish and fetch pass the history to the Hub's `/session/verify`; a fetched history with any commit the Hub does not authorize is refused, and the local calendar is left alone.
Setup asks each Hub for its signing key before the Core publishes, so each teammate's key delegation reaches the other.

The Hub test checks that every commit is signed and that an unsigned commit from Bob is refused when Alice fetches it.
The demo, driven with HTTP requests, still merges on sync and on save, with every commit signed.
The one-time prekey warning seen earlier in Alice's Hub log did not appear in this run.

## 2026-11-18 — Where a Constitution basis could go (not built)

Open design notes; nothing here is decided.
A basis (`architecture.md`, "Constitution Bases") could be carried in three places, each answering a different question:
- On a commit: the view the author held when making the change.
  Signed, survives merges and re-publishing; a merge commit records the view the merger accepted work under.
  Probably replaces `authority_view` in the work context.
- On a bundle or publication link: the view the publisher held when publishing.
  A teammate can read it before importing, and fetch Core first if it cites events they lack.
  It does not mean the publisher endorses older commits in the bundle under that view, and it is gone after import unless recorded.
  The work context's `publication-link` origin may be the slot.
- In app data: for uses the app cares about, such as a decision made while some teammate was a member.
  The app hands the bytes back to the Hub to ask questions rather than parsing them.
  For Tide Table, an `X-` property would face client rewrites and need a merge rule; commits are the easier place.

Open questions:
- A commit cites events the receiver lacks: pause and fetch Core, or accept and judge later?
- A merge-on-save commit carries the server owner's basis, not that of the app that made the stale edit.
- `.IN_PROGRESS/issue-300-constitution-spine/spikes/causal-refs` may already cover how app history depends on Constitution history; not yet read.

## 2026-11-18 — The demo keeps its team across restarts

The demo's data directory is now a sandbox workspace (`devtools/sandbox`).
The first run sets up the team as before and saves the teammate IDs in `tide-table.json`.
Later runs reuse the ports and MinIO credentials in `sandbox.json`, start whatever is not running, open new sessions, and carry on with the same calendars.
A Hub already running on a teammate's port, such as one started from the sandbox dashboard, is used as is; if it does not auto-approve sessions, the demo reads the PIN from the Hub's sandbox-only pending list.
`--fresh` resets a completed Tide Table demo workspace.
For an interrupted first run, use `--data` to choose an empty directory.

The sandbox changed to fit: each participant now has their own root directory, so each Hub and Manager sees only one participant's data, as on separate computers.
Before, all participants shared one root and any Hub would open a session for any of them.

The calendars live in the workspace's `tide-table/` folder, not in a participant's root: the Hub refuses to verify history in a git directory under its own root.

Checked by running the demo three times over one scratch workspace: set up and sync, restart and merge a move with a location change, restart again with Alice's Hub already running without auto-approve and sync a delete.
Not yet tried: the Manager UI over a demo workspace.

## 2026-11-19 — Sync on its own: a sync agent that parks, an app that merges later

Decided in discussion:
- The Hub stays a simple network utility: it moves bytes, reports that a teammate published, and judges signatures.
  Cod Sync chains and repos are the app's business.
- Inside the app, a thin layer talks to the Hub promptly: it fetches teammates' changes into parked refs as soon as the Hub reports them, and publishes local changes.
  The rest of the app merges, or gets the user's attention, when it is ready, by processing events.
- Colliding changes will need uncommon-case UI that we should stay open-minded about; near term, look for clever ways to work within existing clients.
- Code from these experimental apps should move into shared libraries once a second app wants it.
- An app may run the layer in its own process or separately; start in-process.

Built: `tide_table/sync_agent.py`.
One thread long-polls the Hub's existing `/notifications/watch` and fetches each teammate whose count rose into `refs/peers/<id>/main`; another publishes when asked, retrying failures.
Both report on an event queue; the agent never merges.
The demo's CalDAV store asks for a publish after every save and delete; the demo's event handler merges parked changes and asks for a publish when the merge moved `main`.
Typing `a` and `b` is gone.
Hubs the demo starts check teammates' signals every 5 seconds (`SMALL_SEA_WATCHER_INTERVAL`); no ntfy.

Checked by driving the demo with HTTP requests: a new event reached Bob in 1.5 s; a concurrent move and location change merged on both sides in about 5 s, with identical results; after a restart, a delete reached Alice in about 4 s.

Found along the way:
- A Hub's peer watcher reads teammates' storage synchronously on the event loop.
  While a teammate's storage is unreachable, boto retries and the Hub answers no requests for tens of seconds.
  The demo now starts all MinIO servers before any Hub; the Hub itself is unchanged.
- The Hub counts a teammate's publications in all berths together, so a Core publish also wakes the calendar's watch; the extra fetch finds nothing new.
- `TeamManager.wait_for_team_admission_signal` returns `"updated" in result`, which is true even when the watch timed out with nothing updated.

## 2026-11-19 — The sync agent goes async

Decided in discussion: Small Sea's Python goes async all the way down, with no threads where an event loop will do.
Async modules appear beside the sync ones (`cod_sync.aio`, `small_sea_client.aio`); callers move over one at a time, and each sync module goes away once nothing uses it.
After the async git runner and `Repo`, we took one path all the way through before mirroring more: the sync agent's watch, fetch and publish.

Built:
- `cod_sync.aio.store` (local folder, own Hub storage, a teammate's storage through the Hub) and `cod_sync.aio.protocol` (CodSync, generated from the sync class and diffed against it).
- `small_sea_client.aio`: only the three session calls the agent needs.
- `CalendarRepo.fetch` and `publish` are coroutines; `HubSigner.verify_history` is too.
- `SyncAgent` is two asyncio tasks with `start()` and an awaitable `stop()`.
  Hub stores and sessions share one `httpx.AsyncClient` per teammate, owned by whoever starts the agent.
- The demo runs its agents and event processing on one event loop.
  The CalDAV server still runs on threads and asks for publishes with `call_soon_threadsafe`; merges still run synchronously, on the loop.
  On `q`, the demo stops the CalDAV servers, then the agents, then the HTTP clients, and only then the Hubs, so no agent fails against a Hub that is shutting down.

The sync protocol micro tests also run against the async CodSync, through a blocking wrapper in `cod_sync_test_helpers.py`, rather than as a second copy.

Checked by driving the demo with HTTP requests over a scratch workspace: a new event reached Bob in about 5 s, a delete reached Alice in about 3 s, and `q` shut everything down with no errors; a restart synced a new event in about 5 s.

Still open: who owns the calendar's work tree.
Commits and merges are serialized by the CalDAV store's lock; once they are async, an `asyncio.Lock` in one owner should take over, and `commit_paths()` must not read `HEAD` after another task's commit.

## 2026-11-19 — One teammate's held write no longer stalls the other

The experiment: hold Alice's CalDAV write partway through its commit, where a real write waits for the Hub to sign, while both Alice and Bob have a teammate's changes parked to merge.
A good result is that Bob merges at once, and Alice merges only after her write finishes.
`test_a_held_write_stalls_only_its_own_teammate` runs it, releasing the write from a watchdog thread after 2 s.

Before the change, Bob's merge waited for the release.
Alice's event worker took her store's threading lock on the event loop, so waiting for her write blocked the loop, and with it every teammate in the process.

What changed:
- `CalendarRepo.merge` is a coroutine and runs git through the async runner.
- The demo's event worker takes the store's lock by polling it without blocking (`take_lock`), then merges.
  The lock still decides who owns the work tree: a CalDAV request on one of the server's threads, or a merge on the loop.
  Polling leaves no thread waiting on the lock if the worker is cancelled.
- The merge itself is shielded from cancellation, because a merge cancelled halfway would leave git mid-merge and break the next one.
  At shutdown, `stop_caldav` takes each lock with `take_lock`, so it waits for a merge in progress as it does for a write.

The test now passes: Bob merges while Alice's write is held, and Alice ends up with her own write and Bob's change.
In the demo, over a fresh scratch workspace, Alice moved an event while Bob gave it a location; both calendars had both changes, merged event by event, within about 6 s.

What still blocks the loop: a merge's commit message asks the Hub for the authority view through the synchronous client, and snapshots read event files synchronously.
Both are short.
Saving and deleting stay synchronous on the CalDAV threads until the server moves to ASGI; then the threading lock can become an `asyncio.Lock`.

Follow-up: shielding the merge let it finish after its worker was cancelled, but nobody saw how it ended.
If recording the CalDAV changes failed during shutdown, the demo quit normally and asyncio logged "Task exception was never retrieved".
Now a cancelled worker waits for its merge and ends with the merge's error; `stop_workers` collects those errors, and the demo raises the first one once everything has shut down.
A merge conflict during shutdown is still only reported, as it is at any other time.

## 2026-11-19 — A slow Hub during a merge commit no longer stalls the other teammate

The previous entry left one known block on the event loop: a merge commit's message carries the authority view, and `HubSigner.message` asked the Hub for it through the synchronous client.
We tested whether that mattered before deciding anything else.
`test_a_slow_authority_view_stalls_only_its_own_teammate` gives Alice the real `HubSigner`, pointed at a fake Hub that holds the authority-view request until a watchdog thread releases it after 2 s; commits are unsigned there, since the test is about that one request.
Alice and Bob each merge a teammate's independent history, so both merges make a merge commit.

Before the change, Bob's merge waited for Alice's Hub to answer, so one teammate's slow Hub held up every teammate in the process.
Now merges get the authority view through `HubSigner.message_async`, which uses the async session (`small_sea_client.aio` gained `authority_view`); Bob merges at once and Alice merges after her Hub answers.
Commits on the CalDAV threads still use the synchronous `message`.

In the demo, Alice moved an event while Bob gave it a location; both calendars had both changes within about 6 s, and both merge commits were signed, carried the work-context trailer, and passed the other teammate's Hub.

The 10 ms poll in `take_lock` stays: this was the last Hub request on the loop's merge path that we know of, and the poll has not shown up as a cost.
