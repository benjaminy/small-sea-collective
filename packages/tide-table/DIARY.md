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

## 2026-11-19 — The CalDAV server moves to ASGI

The CalDAV spike was the last part of the demo on threads.
It is now an ASGI application (`CalDAVApp`, with Starlette's request and response) served by uvicorn on the demo's event loop.
The request handling itself barely changed: each request still gets a `Handler`, whose methods are now coroutines.

With no threads left, the rest followed:
- `CalendarRepo` is async throughout and uses only cod_sync's async `Repo`; creating, saving, deleting and committing are coroutines.
- `Store` opens with `await Store.open(...)`; `put` and `delete` are coroutines, and its lock is an `asyncio.Lock`.
  The lock still decides who owns the work tree, a CalDAV request or a merge, but nobody polls it any more.
- `HubSigner` opens with `await HubSigner.open(...)` and uses only the async session; `small_sea_client.aio` gained `session_info` and `signing_public_key`.
- The demo no longer needs `call_soon_threadsafe`: a commit asks its agent to publish directly.
  On `q`, setting `should_exit` stops each uvicorn server; uvicorn closes idle connections and waits for requests in progress, so a write that is committing still finishes while its Hub is up.
- tide-table now depends on starlette and uvicorn directly; both were already in the workspace through the Hub.

The held-write, slow-authority-view and shutdown micro tests carry over; a new one checks what uvicorn now handles for us: HEAD without a body, two `WWW-Authenticate` offers, and 405 for methods the spike does not support.
Driving the demo with curl: Digest-authenticated PROPFIND, a sync-collection REPORT that listed a new event from the other teammate, concurrent move and location edits merged on both sides within about 5 s, `q` and Ctrl-C both shut down cleanly.
Apple Calendar and Thunderbird have not been tried against the ASGI server yet.

Found along the way, not fixed: after Alice and Bob each merge the same concurrent edits, their histories cross, with two different merge commits.
If Bob then deletes the event before another round of sync makes the histories converge, git reports a delete-versus-modify conflict, `merge` raises `EventConflict` on both sides, and every later merge in either direction fails the same way, since each includes that history.
It reproduces with `CalendarRepo` and folder stores alone, so it belongs to the merge logic, not the server.

## 2026-11-19 — Step 0 of keeping the calendar in one SQLite file

The plan: keep the calendar in one SQLite file in git instead of one `.ics` file per event, and merge it with a Tide Table merge driver built on Splice Merge's row deltas, with `merge_event` for an event both sides edited.
Before building it, `spikes/sqlite_merge_spike.py` tested two things with plain git repos (git 2.55, no Cod Sync, no signing).
It runs the crossed-history delete from the previous entry twice, once with Bob deleting and once with Alice.

**Git calls a custom merge driver for crossed histories.**
When the merge of the delete finds two candidate bases, git first merges those bases (the move and the location change) by calling the driver, then merges against the result.
So Tide Table can keep using `git merge` and does not need to find bases itself.

**The delete merges only by luck of byte order.**
`merge_event` takes the calendar header and the time-zone blocks from whichever side is "ours", so the two teammates' merges hold the same event content in different bytes.
When git's merged base happens to come out byte-identical to the merging teammate's file, git does not even call the driver, and the delete goes through.
When it doesn't, the driver sees an event that looks edited on one side and deleted on the other, and refuses; from then on every merge refuses in both directions, the same wedge as with `.ics` files.
Which case happens varied between runs of the same script: Bob's delete went through in one run and wedged in the next, so the choice of "ours" in git's base merge depends on something like commit order or timestamps.

Two ways to fix it, not yet tried:
- Make `merge_event` give the same bytes whichever side is "ours", for example by choosing the header and time-zone block by a fixed rule instead of by side.
  The two teammates' merges would then store identical rows, and the comparison against git's merged base would see no edit.
- Have the driver compare events by content rather than bytes, so an event whose fields match the base counts as unchanged.

The first also lets the two merge commits hold identical rows, which may help histories converge; the second is more forgiving of clients that rewrite events without changing them.

A reviewer found a worse case in the same spike, now its third scenario.
Alice sets the location to Room A and Bob to Room B; both merges conflict, and each resolves by keeping their own room.
When Alice next merges Bob's history, git first merges the two candidate bases, where the driver hits the same conflict and exits 1.
Git does not stop: it uses the failed inner merge's "ours" (Room B) as the base, sees that only Alice changed the location relative to it, and keeps Room A without calling the driver again.
The merge succeeds and Alice never learns they still disagree.

The driver can tell these inner merges apart: git passes a conflict-marker size of 9 for them instead of 7 (it widens the markers by 2 per level).
So in an inner merge, the driver could write a placeholder for an event it cannot merge, meaning "no agreed base".
Both sides would then differ from the base, git would have to call the driver for the real merge, and the driver could compare the two sides directly: identical means settled, different means still in conflict.
Not tried yet.

Tried the placeholder, and it works.
When the marker size says git is merging candidate bases, the driver no longer fails on a conflict: it stores `NO_AGREED_BASE` as that event's version and exits 0.
In the real merge, an event whose base is `NO_AGREED_BASE` is settled only if both sides hold the same version; otherwise the merge fails as a conflict.
In the rooms scenario, Alice's merge of Bob's resolution now reports the conflict, and after Bob switches to Room A the next merge succeeds with Room A.
That held in five runs out of five.

"The same version" is a byte comparison for now.
That works here because both Room A versions come from the same text; two real clients that agree on a room will still differ in DTSTAMP and SEQUENCE, so the comparison has to be by content, the same need as the delete case.
The crossed-history delete is unchanged by this and still depends on luck: in five runs, it wedged in three of the ten merges of a delete.

Then content comparison, which fixes the delete case.
`tide_table.merge.same_event(a, b)` says whether two versions agree on everything `merge_event` compares: every event field except DTSTAMP, LAST-MODIFIED, X-MOZ-GENERATION and SEQUENCE, ignoring line folding, the calendar header and time-zone blocks.
The spike's driver uses it twice:
- Before diffing, a side whose version of an event is the same event as the base's counts as not having changed it.
  In the crossed-history delete, Alice's merged event and git's merged base differ only in header and zone, so Alice's side no longer looks edited, and Bob's delete applies.
- With `NO_AGREED_BASE` as the base, the two sides are settled if they are the same event, not the same bytes.
  The rooms scenario now has Bob's client stamp a new DTSTAMP and SEQUENCE when he switches to Room A; the merge still accepts it.

Six runs of all three scenarios: every delete merged on both sides and the histories converged, Bob's resolution was reported as a conflict every time, and his change of mind was accepted every time.
Micro tests for `same_event` cover merges made on either side, bookkeeping and folding, a changed field, and recurring events, which `merge_event` cannot parse and so compare byte for byte.

`merge_event` still takes the header and zone from "ours", so the two teammates' merges still differ in bytes; with content comparison that no longer matters for merging.

## 2026-11-19 — The calendar is one SQLite file

Step 1: `CalendarRepo` now keeps every event in `calendar.sqlite`, one row per event, and git merges that file with Tide Table's merge driver.

- Each row has a random UUIDv7 (`id`), the name CalDAV clients use, the event's UID, and its iCalendar text exactly as the client wrote it.
  A save keeps the event's ID when the name already exists.
  The UUIDv7 code is a copy of `small_sea_note_to_self.ids.uuid7`, as ssc-files also has; Python 3.12 has no `uuid.uuid7`.
- `tide_table/merge_driver.py` is the spike's driver made real.
  `CalendarRepo.create` registers it as `python -m tide_table.merge_driver %O %A %B %L %P`, using the interpreter that created the calendar.
  It writes the events it merged field by field and its conflicts to a JSON file that `merge` reads, so `EventConflict` now carries descriptions such as `update/update e.ics` or `delete/modify e.ics` instead of paths.
- Two events with one name or one UID after a merge are a conflict, as `.IN_PROGRESS/unification/design.md` suggests until unification exists.
- Reads open the file afresh each time, because a merge or checkout replaces it.
- The CalDAV store reads events through `CalendarRepo` and keeps its calendar in a `calendar/` directory.
  An older demo workspace's `events/` directory is left alone, so its events no longer show up; nothing converts them.

The crossed-history delete test passes and lost its expected-failure mark.
New micro tests: an edit against a delete conflicts; two events with one UID conflict; and a disagreement both teammates kept is still reported after their histories cross, then settles once they agree.
Disabling the no-agreed-base handling makes that last test fail.
All 39 Tide Table tests pass, including the signed run through two real Hubs.

The demo on a new scratch workspace (ports 5292/5293): an event reached Bob in 1.5 s, concurrent move and location edits merged on both sides in about 5 s, Bob's delete straight after reached Alice in under 4 s, and after a restart both calendars were still empty and a new event synced.
Apple Calendar and Thunderbird have not been tried against this storage yet.

## 2026-11-19 — Keeping your own copy is a disagreement too

A review found a merge that succeeded by deleting an invitation both teammates kept.
Alice and Bob added the same invitation under different names; both merges reported the duplicate UID, and each resolved it by keeping their own copy.
On the next sync git combined the two crossed bases, and the driver put both rows into the combined base unmarked.
Against that base each side seemed to have deleted the other's copy, so the merge deleted both.

`NO_AGREED_BASE` had only covered conflicts within one row.
Now, in a combined base, both rows of a duplicate name or UID get it too.
The real merge then reports each copy as kept on one side and deleted on the other, and succeeds once both sides hold the same copy.
A micro test covers this; it fails with the old driver.

The same review found that `event_uid` dropped the start of a UID that contains a colon (`UID:mailto:alice` read as `alice`), so unrelated events could look like duplicates.
It now takes everything after the first colon outside quoted parameters.

Not fixed: `same_event` ignores VTIMEZONE blocks, so two versions that define one TZID with different offsets count as the same event, and a concurrent delete can win silently.
Telling them apart needs a VTIMEZONE interpreter, which the planned parsed columns for `calendar-query` will also need.

## 2026-11-19 — Conflicts show up in the calendar

When a teammate's changes do not merge, the CalDAV server now adds a placeholder event for each conflict, the shape ROADMAP.md sketches for Phase 3.
The placeholder sits at the conflicting event's time, is titled "⚠️ CONFLICT: <summary>", carries an alarm a minute after the conflict appeared, and links (in URL and DESCRIPTION) to a page on the same server, `/conflicts/<secret>`.
The page shows each conflicting event's fields side by side, with the differing ones highlighted, and offers "Keep yours" or "Keep Bob's".

- The placeholders are not stored in calendar.sqlite or synced.
  The server makes them up from the conflicts it recorded, which it keeps in its state.json so a placeholder clients saw is withdrawn even after a restart.
- A choice reaches the merge driver as a resolutions file (event ID to "ours" or "theirs").
  Each event in the conflict takes that side's version, or is deleted if that side has none; for a duplicate UID, that keeps one side's copy and drops the other.
  The result is an ordinary merge commit, so the teammate fast-forwards to it and their own placeholder goes away.
- The form carries both histories' heads. If either moved since the page was shown, the choice is not applied, the conflicts are refreshed, and the page says to check again.
- The page needs the secret in its path, not the CalDAV password, because any web page the person visits can send requests to localhost.
- Deleting a placeholder in a client hides it until its conflict changes. Editing one is ignored, and the server reports it changed so the client fetches it back.
- This is option A from the discussion: a conflict still blocks all of that teammate's other changes until someone resolves it, and each teammate sees and can resolve it on their own device. Option B, merging everything else and recording the conflict in the shared data, is the likely next step if blocking hurts.

Micro tests cover choosing either side for an edit conflict, an edit against a delete, and a duplicate UID; a choice covering only some conflicts settles nothing; and, through HTTP, the placeholder, the page, a stale choice, resolution, and a client dismissing or editing a placeholder.
On a scratch workspace through two Hubs, a concurrent kitchen/garden edit produced a placeholder on both sides within 4 s; Alice chose Bob's room on the page, and Bob's placeholder was withdrawn about 5 s later.

Known unknowns, deferred:
- Apple Calendar and Thunderbird have not been pointed at the SQLite storage, let alone the placeholders: whether they show the link, fire the alarm, or behave when an edit to a placeholder is ignored.
- Conflicts are only re-checked when a teammate publishes. If the person edits the conflicting event locally so that it matches, the placeholder stays until the next merge or a visit to the page.
- The page shows yours and theirs but not the version both started from.

## 2026-11-19 — Applying a person's choices correctly

A review found two ways the conflict page applied the wrong thing.

- **Two conflicts could not be resolved.** Each conflict had its own buttons, but a merge needs every choice at once: a single choice settled nothing, and the page still reported success.
  Each teammate's conflicts now share one form, with a choice for each and one "Apply choices" button.
  A submission that still leaves conflicts applies nothing and says so.
  Fixing this exposed a second bug: a merge with only some choices recorded the smaller conflict list it reported, so the page dropped the conflict already chosen and the next submission never applied that choice. Only a merge without choices now records conflicts.
- **"Keep Bob's" could commit a version Alice never saw.** The sync agent can move Bob's parked ref at any time, even while Alice's merge holds the calendar lock.
  `Store.merge` now fixes the commit before merging and records conflicts against the exact commits that produced them, and a choice merges exactly the commit shown on the page.
  Whatever was fetched since merges afterwards as a change of its own.

Micro tests cover both cases. The live run on a scratch workspace still resolves a conflict on both sides.

## 2026-11-20 — The placeholder in Apple Calendar and Thunderbird

Real clients show the conflict placeholder, and resolving on one side clears it on the other.
We ran the two-teammate demo on a scratch workspace, with Apple Calendar connected as Alice (found from `/dav/principals/alice/`) and Thunderbird subscribed to Bob's calendar.
A script gave the same event different locations on both sides at once, because the sides sync within about 5 s, too fast for two people to clash by hand.

- Both clients showed "⚠️ CONFLICT: Team lunch".
  Thunderbird fetched its placeholder about 20 s after the conflict.
- Alice chose a side on the conflict page in a browser.
  Bob's server merged the result, and Thunderbird dropped the placeholder on its next poll.
- Thunderbird checks a calendar only every 30 minutes by default, so without a manual refresh a placeholder can take that long to appear.
- Right after the form was submitted, the browser sent a GET to the form's `/resolve` address and got a 404.
  It did no harm.
  The log showed the browser had already followed the redirect and loaded the page; the GET came about 190 ms later, from the browser itself.
  That address now answers GET with a redirect to the page.

- Clicking the link in the placeholder opened the conflict page from both Apple Calendar and Thunderbird.
  The person who clicked already knew the link was there, so this does not show whether a newcomer would find it.

Still not checked in real clients: whether the alarm fires, and what each client does when an edit to a placeholder is ignored or a placeholder is deleted.

## 2026-11-20 — Choices that contradict each other

An event can be in two conflicts at once: one over its filename and one over its UID.
The page offered a separate choice for each, and the server flattened the choices into one answer per event, so a later choice silently overwrote an earlier one.
In the case a review found, Alice chose her own version for the filename conflict and Bob's for the UID conflict; the page reported success and put Bob's event under the filename Alice had chosen to keep.
The server now rejects choices that disagree about any event, applies nothing, and the page names the events that link the conflicts.
A micro test covers this case.
