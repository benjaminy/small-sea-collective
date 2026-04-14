# Branch Plan: NoteToSelf Sync and Team Discovery

**Branch:** `codex-issue-48-note-to-self-sync-discovery`  
**Base:** `main`  
**Primary issue:** #48 "Multi-device NoteToSelf sync and team discovery"  
**Related issues:** #59, #69, #43, #61  
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`,
`packages/small-sea-hub/spec.md`  
**Related archive plans:** `Archive/branch-plan-note-to-self-shared-device-local-split.md`,
`Archive/branch-plan-joining-device-bootstrap.md`,
`Archive/branch-plan-issue-69-linked-device-encrypted-team-bootstrap.md`,
`Archive/branch-plan-issue-59-peer-routing-watches.md`,
`Archive/branch-plan-issue-59-peer-device-model.md`

## Context

Several prerequisite branches have landed:

1. NoteToSelf shared and device-local storage are now split cleanly.
2. A newly linked device can bootstrap into the identity and pull shared
   NoteToSelf state.
3. Team runtime and shared team DB modeling are now much more device-aware.

What is still missing is the steady-state same-identity refresh path after that
initial bootstrap.

Today, the repo still lacks one honest implementation slice for:

- learning that shared NoteToSelf changed on another device
- pulling and adopting the updated shared NoteToSelf state through the Hub
- discovering teams created elsewhere by the same identity
- doing all of that without syncing device-local secrets or sneaking in
  automatic team admission

That missing slice keeps showing up as deferred follow-up work in the recent
identity/runtime/crypto branches. It is now the clearest next dependency.

## Proposed Goal

After this branch lands:

1. a second device can refresh shared NoteToSelf through Hub-mediated sync and
   discover teams created on another device of the same identity
2. the Hub can surface a coarse "NoteToSelf changed" signal to the Manager
   instead of leaving refresh entirely blind
3. the Manager can adopt refreshed shared NoteToSelf state and rescan newly
   visible teams without inventing a shadow registry
4. discovery remains distinct from team access: seeing a team in NoteToSelf is
   not the same thing as having a local clone or local team-device participation
5. device-local NoteToSelf secrets and runtime state remain local and unsynced
6. the branch reuses the normal steady-state NoteToSelf Hub transport that
   already exists for push, instead of inventing a second bootstrap-only path

## Why This Slice

This branch should finish the identity-level discovery plumbing, not reopen the
crypto/runtime branches.

The earlier branches already established:

- how sender-key runtime works across devices
- how linked devices bootstrap into existing teams
- how shared team state distinguishes members from devices

What they all kept deferring is the steady-state path that lets one device find
out what another device already added to shared NoteToSelf.

That makes `#48` the right next branch boundary:

- smaller than revocation or periodic rotation policy
- more foundational than more routing/watch tweaks
- directly aligned with the repo's own "what is still missing" notes

## Scope Decisions

### S1. Shared NoteToSelf remains the only synced source of identity-level discovery

This branch should not invent a second synced discovery registry.

Discovery comes from the shared NoteToSelf DB and repo:

- `team`
- shared locator metadata already meant to sync across devices

Important factual boundary for this branch:

- user-team `app` and `team_app_berth` rows do not live in NoteToSelf today
- `team_app_berth` in NoteToSelf currently serves the NoteToSelf meta-team, not
  arbitrary user teams
- this branch therefore treats user-team discovery as discovery of `team` rows,
  not synced per-team app/berth structure

If later work wants cross-device discovery of user-team app/berth metadata via
NoteToSelf, that should be a separate explicit schema decision rather than an
accident of this branch.

### S2. Reuse the normal NoteToSelf Hub transport, not the bootstrap helper

This branch should not treat `_push_note_to_self_to_local_remote(...)` as the
steady-state transport seam. That helper is for identity bootstrap.

The steady-state NoteToSelf sync path for this branch should build on the
already-landed normal session transport:

- Manager opens a normal `NoteToSelf` Hub session
- Manager uses `SmallSeaRemote` over `/cloud_file`
- refresh/pull and push both go through the Hub-owned cloud transport for that
  session
- push is already wired today; this branch makes refresh/pull a first-class
  Manager seam on top of the same transport, adding a small CodSync fetch/adopt
  seam only if needed

This keeps same-identity NoteToSelf sync on the same transport model as other
steady-state Hub-mediated sync instead of inventing a NoteToSelf-specific proxy
path or reusing peer semantics that do not fit.

Conflict resolution for concurrent same-identity push/pull should continue to
rely on existing CodSync git fetch/merge/push semantics. This branch does not
introduce a new merge policy.

### S3. Hub owns signal detection; Manager owns refresh/adoption and writes

This branch should keep the existing architectural seam honest:

- Hub detects that shared NoteToSelf may have changed remotely
- Hub handles the actual cloud transport for pull/push
- Manager remains the only writer of NoteToSelf DBs
- Manager decides when to refresh, how to adopt the refreshed state, and what
  newly visible teams mean locally
- adoption includes explicit DB-handle lifecycle management: NoteToSelf
  connections/engines used before refresh must not be silently reused across
  fetch/adopt

### S4. User-initiated refresh remains the default

The Hub should help with update-awareness, but this branch should not turn
NoteToSelf sync into a magical always-on auto-adopt system.

The intended model is:

- Hub compares coarse remote signal state and surfaces "there may be new shared
  NoteToSelf data"
- Hub does not prefetch and stage NoteToSelf bytes for later adoption
- Manager-triggered refresh performs the actual fetch/adopt when the user or
  test asks for it
- update-awareness requires an active `NoteToSelf` Manager/Hub session; it is
  not a background daemon for one-shot CLI use
- tests may auto-trigger refresh for local proof, but production semantics stay
  user-initiated by default

### S5. Discovery is not team join

This branch should be explicit about the boundary:

- discovering a team in shared NoteToSelf means "this identity knows about this
  team"
- it does not automatically create a local team clone
- it does not automatically mint local team-device keys
- it does not automatically complete encrypted team bootstrap/admission

That follow-through remains separate work already owned by the team-join and
runtime branches.

### S6. Device-local NoteToSelf state must remain out of sync

This branch must not regress the shared/local split.

Shared NoteToSelf refresh may move:

- team pointers
- app registrations
- berth metadata
- cloud locator metadata that is already intentionally shared

It must not sync:

- credentials
- local key refs
- sender-key runtime state
- notification tokens or other local-only auth/runtime material

### S7. Reuse existing signal/watch machinery instead of inventing a mailbox table

The Manager spec uses "mailbox" language, but the concrete repo machinery today
is:

- `signals.yaml` counters in cloud storage
- Hub watcher polling / optional ntfy-triggered wakeups
- berth events and `/notifications/watch`

This branch should use that existing shape for a coarse NoteToSelf update
signal instead of inventing a durable mailbox table.

Recommended concrete seam:

- detection trigger: the current session's NoteToSelf berth counter in the
  shared `signals.yaml` changes
- there is one shared signal file per berth in cloud storage; "self" here means
  the current NoteToSelf berth/session, not a device-private signal file
- Hub watcher tracks that self-signal only for live NoteToSelf sessions
- the Hub-side same-session self-echo filter is deliberate race protection for
  the narrow case where a push succeeds but the Manager crashes before it can
  persist the new adopted counter locally
- Hub exposes the coarse change through the existing notification/watch seam,
  adding a self-update axis rather than pretending the NoteToSelf berth has a
  peer-member dimension
- self-update reporting is opt-in: callers that do not supply a
  `known_self_count`-style value should continue to get the current peer-only
  behavior

### S8. "Known team" and "joined team" are derived from existing state

This should not stay an open question.

For this branch:

- known/discovered team = row exists in shared NoteToSelf `team`
- joined/adopted team = local team repo/DB exists at
  `Participants/{participant_hex}/{team_name}/Sync/core.db`

This branch should update the code paths that currently assume "team row exists
in NoteToSelf" implies "local team DB exists on disk." In particular, team
detail/read paths should either guard and return a clear not-joined-local state
or stay out of the discovery UI entirely.

### S9. Persist the last adopted NoteToSelf signal count locally

This branch should add the smallest honest local baseline seam so update
awareness behaves sensibly across bootstrap, refresh, push, and restart.

Recommended rule:

- store the last adopted NoteToSelf berth counter in the NoteToSelf
  device-local DB, not in an ad-hoc state file
- add a tiny dedicated local sync-state table rather than overloading an
  unrelated existing table
- bump `LOCAL_SCHEMA_VERSION` as part of that addition
- seed it at bootstrap completion when that counter is known
- otherwise seed it on first successful NoteToSelf session open/refresh without
  surfacing a stale "new data" prompt
- update it after every successful NoteToSelf refresh/adoption
- update it after this same device successfully pushes NoteToSelf so the Hub
  does not immediately report that push back as a self-update

The persisted baseline is the primary correctness mechanism. The Hub-side
same-session echo filter above is only cheap defense-in-depth for the crash/race
window before that baseline is updated.

### S10. Pre-alpha rules apply

Prefer a clean, reviewable implementation over backward-compatibility shims for
old sandboxes.

## In Scope

### 1. Shared NoteToSelf refresh path

Add or tighten the explicit Manager/Hub seam for refreshing shared NoteToSelf
through a normal NoteToSelf Hub session.

Minimum behavior:

- a device can fetch shared NoteToSelf updates through `SmallSeaRemote` over
  `/cloud_file`
- the refreshed shared DB/repo becomes the source of truth immediately after
  adoption, with explicit close/reopen or invalidate/reopen behavior around any
  NoteToSelf DB handles used by the refresh path
- refresh and discovery remain separate entry points: refresh fetches/adopts,
  while discovery reads current shared NoteToSelf state even when offline
- refresh works in local micro-test setups without internet dependence

### 2. Update-awareness for shared NoteToSelf

Add the smallest honest update-awareness path so the Manager is not blind to
remote NoteToSelf changes.

Minimum behavior:

- Hub detects NoteToSelf changes by observing the current session's NoteToSelf
  berth counter in shared `signals.yaml`
- Hub surfaces a coarse self-update signal through the existing watch/notification
  seam
- Manager has a clean seam for reacting to that coarse signal without automatic
  byte staging or automatic adoption
- the first watch cycle after bootstrap/session-open does not produce a bogus
  self-update solely because the local known counter started at zero
- this device's own successful NoteToSelf push does not come back immediately
  as a self-update prompt

### 3. Team discovery after refresh

After a successful shared NoteToSelf refresh, the Manager should rescan and
surface newly visible shared metadata, including:

- teams created on another device

The implementation should avoid shadow copies of discovery state when the
shared NoteToSelf `team` table already contains the truth.

This branch does not need to sync or discover user-team `team_app_berth`
structure through NoteToSelf.

### 4. Coherent local semantics for "known remotely, not adopted locally"

This branch should make the local semantics honest when a team is visible in
NoteToSelf but has not been fully joined on this device.

At minimum, the code should distinguish between:

- team is known in NoteToSelf
- team has a local clone / active local participation

That distinction should be explicit enough that later branches do not have to
infer it from accidents.

### 5. Specs and micro tests

Update the Manager/Hub specs and add skeptical micro tests that prove the new
refresh/discovery path is real.

Minimum expected coverage:

- two-device same-identity setup where device A records a team in shared
  NoteToSelf, pushes NoteToSelf through the existing normal Hub path, and
  device B does not see it until refresh
- after shared NoteToSelf refresh, device B discovers the team cleanly
- device B still does not auto-create local team participation for that team
- shared NoteToSelf refresh does not sync device-local secrets/runtime state
- Hub update-awareness path produces a coarse self-update signal instead of
  silent magic or hidden byte staging

## Out Of Scope

- automatic team join / local team clone after discovery
- syncing user-team `app` / `team_app_berth` metadata through NoteToSelf
- genuine concurrent writes to the same shared NoteToSelf row; this branch only
  aims to cover non-overlapping inserts plus normal stale-writer rejection
- sender-key crypto redesign
- revocation or device-removal semantics
- periodic sender-key rotation policy
- broad UI/UX redesign
- generalized multi-remote arbitration beyond the current practical assumption
- redesigning the whole Hub notification/watch system beyond what this branch
  actually needs
- redesigning NoteToSelf push transport; this branch may rely on the existing
  `push_note_to_self()` path

## Concrete Change Areas

### 1. Manager refresh/adoption seams

- `packages/small-sea-manager/small_sea_manager/manager.py`
- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- any narrow helper layer already responsible for NoteToSelf sync/discovery
- `packages/small-sea-note-to-self/small_sea_note_to_self/db.py`
- `packages/small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`

Likely work:

- explicit `refresh_note_to_self()`-style entry point using a normal
  `NoteToSelf` session and `SmallSeaRemote`
- explicit refresh adoption lifecycle:
  close/dispose old NoteToSelf DB handles, fetch/adopt, reopen fresh handles
- separate `list_known_teams()`/discovery read seam that reads current shared
  `team` rows without implicitly fetching
- clear distinction between "discovered in NoteToSelf" and "locally joined"
- guards or explicit semantics for team-detail/read paths that currently assume
  the local team DB exists
- persist/update the last adopted NoteToSelf berth counter in device-local
  DB state
- bump `LOCAL_SCHEMA_VERSION` for the new NoteToSelf local sync-state table

### 2. Hub update-awareness / transport seams

- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/small-sea-hub/small_sea_hub/server.py`
- existing watch/signal helpers already used for incoming reminders

Likely work:

- reuse the current normal Hub session transport for steady-state NoteToSelf
  fetches
- track coarse NoteToSelf self-signal changes for NoteToSelf sessions
- extend the existing watch/notification seam with a self-update field/axis
  while preserving existing peer-watch behavior for non-NoteToSelf team
  sessions
- filter out self-signal bumps caused by the same session's own push
- treat detection as session-bounded rather than as a persistent background
  daemon when no NoteToSelf session is open

### 3. Specs

- `packages/small-sea-manager/spec.md`
- `packages/small-sea-hub/spec.md`

Likely work:

- replace stale "mailbox" wording in the Manager spec with the actual
  self-signal/watch seam this branch implements
- document the self-update field as additive and opt-in on the Hub watch API

### 4. Tests

- focused Manager micro tests for refresh/discovery semantics
- focused Hub micro tests for the self-update signal seam
- one end-to-end local two-installation micro test proving same-identity team
  discovery after refresh

Recommended test split:

- the end-to-end two-installation proof should call the explicit refresh path
  directly rather than depending on watcher polling timing
- self-signal/watch behavior should be proven in focused Hub micro tests with a
  short controlled watcher interval

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- there is one explicit code path for shared NoteToSelf refresh instead of
  ad-hoc local DB poking
- no unrelated code path silently performs a NoteToSelf git pull as a side
  effect of listing, rescanning, or opening team detail
- the refresh path uses the normal NoteToSelf session transport (`SmallSeaRemote`
  over `/cloud_file`), not the bootstrap helper and not peer transport
- the Hub, not the Manager, still owns the actual cloud transport and coarse
  signal detection
- the Manager, not the Hub, still owns NoteToSelf DB writes/adoption
- after refresh, freshly opened NoteToSelf connections see the new state and
  the refresh path does not rely on stale pre-refresh DB handles
- device B in a two-installation same-identity test does not see a newly added
  team before refresh
- after refresh, device B does see the team in NoteToSelf-backed discovery
- a stale NoteToSelf writer cannot clobber a newer remote state silently;
  non-fast-forward push is rejected until refresh/adoption occurs
- after refresh, device B still has no local team clone or local team-device
  participation unless separate join/bootstrap work runs
- a team row in NoteToSelf is enough for discovery, but team-detail operations
  that require a local clone do not crash or silently assume the clone exists
- shared/device-local NoteToSelf boundaries remain intact after refresh
- the Hub's self-update signal is driven by NoteToSelf's own `signals.yaml`
  counter rather than by hidden byte staging or a new mailbox table
- the first watch after bootstrap/session-open does not spuriously report a
  self-update when the device is already in sync
- this device's own NoteToSelf push does not immediately echo back as a
  self-update prompt
- existing peer-watch behavior for ordinary team sessions still works after the
  self-update field/axis is added
- the implementation makes later runtime/team-join branches simpler instead of
  introducing another shadow source of truth

## Open Questions

### Q1. Exact watch API shape for the self-update field

The branch should keep the signal coarse, but the exact HTTP shape still needs
to be chosen explicitly during review.

Preferred direction:

- keep the watch API count-based, not etag-based
- add a `known_self_count`-style request field and a `self_updated_count`-style
  response field/value
- this matches the existing watch endpoint's counter semantics better than
  introducing a second etag-shaped contract

Likely shape:

- reuse `/notifications/watch`
- request includes a known self-count for NoteToSelf sessions
- response includes a self-updated count/value when the current session's
  NoteToSelf berth counter increased
- callers that omit the self-count field keep today's peer-only semantics

This branch should not broaden that into rich semantic diff delivery.
