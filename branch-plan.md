# Split NoteToSelf Into Shared and Device-Local Storage

Branch plan for `note-to-self-shared-device-local-split`.
Primary tracker: #61.

Related trackers:

- #58 — joining-device bootstrap
- #48 — multi-device NoteToSelf sync and team discovery
- #59 — multi-device sender-key / peer-routing runtime
- #4 — future home for real vault / enclave-backed secret storage

## Problem

`core_note_to_self_schema.sql` currently mixes two very different kinds of
state in one SQLite file:

- **shared identity metadata** that ought to be safe to sync across a user's
  devices
- **device-local secrets and runtime state** that should never leave the
  current device

If NoteToSelf is ever synced across devices in earnest, the current shape would
leak:

- cloud credentials and OAuth refresh state
- notification tokens
- local private-key references
- sender-key private material
- other local runtime state that should not be cloned or merged across devices

This branch is a narrow prerequisite: make the NoteToSelf data model safe
enough that syncing the shared part later is not obviously wrong.

## Goal

Refactor NoteToSelf storage into:

1. **Shared state**
   - safe to store in `NoteToSelf/Sync/core.db`
   - safe to sync across devices
   - identity metadata, public keys, team/app discovery data, remote locators
2. **Device-local state**
   - stored outside the synced repo
   - never leaves the current device
   - credentials, bearer tokens, private-key refs, local crypto runtime state

The branch succeeds when:

- the shared DB no longer contains the moved secret/runtime fields
- the Hub and Manager still work using the split storage
- the Hub no longer depends on Manager for NoteToSelf storage helpers
- the split is mechanical and reviewable, not a broader redesign of identity
  or sync behavior

## Critical Design Constraints

### 1. The device-local DB must live outside `Sync/`

This branch should **not** put `device_local.db` next to `core.db` inside the
synced git repo and trust `.gitignore` or convention to keep it local.

Decided paths:

- shared DB stays at `Participants/{participant_hex}/NoteToSelf/Sync/core.db`
- device-local DB at
  `Participants/{participant_hex}/NoteToSelf/Local/device_local.db`

That keeps the local/shared boundary physically obvious and reduces the chance
of accidental sync or accidental `git add`.

### 2. Not every affected table wants the same kind of split

The first draft treated this mostly as a column move.
That is too optimistic.

There are really three categories:

- **Shared tables** that remain in the shared DB
- **Column-split tables** where public locator metadata stays shared but auth
  material moves local
- **Whole-table local runtime state** that should not be shared at all

### 2.5 Visibility does not imply local usability

After the split, some shared rows will intentionally be visible on a device
without being usable there.

This must be treated as a normal state, not an error in the data model.

Rules:

- shared `cloud_storage` row + no matching local credential row
  = this device knows the remote exists, but cannot authenticate to it
- shared `team_device_key` row + no matching local secret row
  = this device knows the key metadata exists, but cannot sign with it

Corollary:

- local capability checks must require both shared metadata and matching local
  secret state
- lookup helpers like "current team device key" must ignore shared rows that
  lack a matching local secret row

### 3. This branch changes the Manager ↔ Hub contract

The Hub currently assumes the NoteToSelf shared DB is where cloud and
notification credentials live.
After this branch, that is no longer true.

So the branch must explicitly include:

- Hub code changes
- Hub spec/doc changes
- test coverage for the new read/write paths

If the plan pretends this is only a Manager/UI refactor, it will under-scope
the work.

### 4. Fresh-schema-first is fine

Pre-alpha rules apply.
This branch should prefer a clean, correct split over elaborate backward
compatibility.

Default:

- define the new schemas cleanly
- keep version markers in place
- only do migration work if it is very cheap and directly helpful for tests
- do not spend the branch on preserving old local sandboxes

## Proposed Storage Classification

This branch should lock the intended classification early.

### Stays Shared

- `user_device`
- `nickname`
- `team`
- `app`
- `team_app_berth`
- public / discovery-facing parts of other split tables

### Column-Split

#### `cloud_storage`

Keep shared:

- `id`
- `protocol`
- `url`
- `path_metadata`
- `client_id`

Move local:

- `access_key`
- `secret_key`
- `client_secret`
- `refresh_token`
- `access_token`
- `token_expiry`

Rationale:

- the shared side names the remote and its object/path metadata
- the local side contains actual auth material and refresh state

#### `team_device_key`

Keep shared:

- `team_id`
- `device_id`
- `public_key`
- `created_at`
- `revoked_at`

Move local:

- `private_key_ref`

Rationale:

- public membership / participation metadata may be useful identity-wide
- the private key handle is inherently device-local

Note: the local side is asymmetric. The shared table has rows for ALL of this
user's devices across teams. The local table only has `private_key_ref` entries
for the *current device's* keys — other devices' rows have no corresponding
local record on this machine.

Note: team repos also carry `device_link` certs that record team-level device
admission. The shared `team_device_key` rows in NoteToSelf serve a different
purpose: personal bookkeeping ("what teams do my devices participate in?")
versus team-scoped trust proof. The redundancy is intentional but should be
watched for consistency issues as the identity model matures.

#### `notification_service`

Keep shared:

- `id`
- `protocol`
- `url`

Move local:

- `access_key`
- `access_token`

Reason:

- notification configuration is mostly device behavior
- but if a user sets up a Gotify server, the URL is not secret and could be
  useful as shared knowledge ("this identity uses this notification endpoint")
- the boundary rule is: shared = general service info, local = secrets needed
  to use it

### Whole-Table Local

#### `team_sender_key`

Default: move the whole table local.

Reason:

- this is local sender runtime state, not identity metadata
- `chain_key`, `iteration`, `skipped_message_keys`, and signing state should
  not be cloned or merged across devices

#### `peer_sender_key`

Default: move the whole table local.

Reason:

- receiver-chain state is also local runtime state
- sharing it would blur per-device encrypted runtime semantics and create more
  questions than this branch should answer

## Proposed Goal Slice

Land the smallest honest refactor that makes NoteToSelf safe to share later:

1. define a device-local NoteToSelf DB outside `Sync/`
2. move the selected secret/runtime state there
3. update Manager and Hub access paths to read/write the correct DB
4. keep current user-visible flows working
5. extract a narrow `small-sea-note-to-self` package so NoteToSelf storage
   stops living behind a Hub → Manager dependency
6. update docs and related GitHub issues so the repo stops claiming that auth
   secrets live in the shared NoteToSelf DB

## In Scope

- a new device-local NoteToSelf schema file
- schema changes to the shared NoteToSelf DB
- central access helpers for split storage in Manager/provisioning
- Hub changes so cloud and notification operations read/write device-local
  secrets from the local DB
- tests covering cloud storage, OAuth refresh, notifications, and team key
  retrieval through the split storage
- doc updates across Manager and Hub specs
- GitHub issue audit/update for the issues this branch materially changes

## Out Of Scope

- NoteToSelf sync itself (#48)
- joining-device bootstrap flow (#58)
- broader identity-model redesign
- sender-key runtime redesign (#59)
- moving secrets into OS keyrings / enclaves / Cuttlefish (#4)
- broad cloud-adapter abstraction redesign
- deep migration support for old local databases

## Key Questions To Lock Early

### Q1. Exact local DB path/name

**Decided:** `Participants/{participant_hex}/NoteToSelf/Local/device_local.db`

(See Constraint 1.)

### Q2. Does `client_id` stay shared?

**Decision:** yes.

It identifies the OAuth app, not the user's auth session.
If later a provider forces this to differ per device, that can be revisited.

### Q3. Does `path_metadata` stay shared?

**Decision:** yes.

Provider object IDs and similar path metadata are not auth secrets and may be
useful across devices.

### Q4. Do we need a separate version marker for the local DB?

**Decision:** yes.

Implementation shape:

- separate local schema file
- separate local schema version constant
- local DB create-if-missing on open
- narrow local migrations when the file exists but is older

The local DB must evolve independently from the shared DB without pretending
they are the same file.

(Q5 and Q6 from earlier drafts are now covered by the Decisions section and
Constraint 5.)

## Current Access Pattern (from repo audit)

NoteToSelf DB access is **scattered** across the codebase:

- **~50+ call sites** open the DB independently, with no central helper
- **Two connection styles** are used: SQLAlchemy `create_engine()` (~40 sites
  in provisioning.py and backend.py) and raw `sqlite3.connect()` (~10 sites
  in sender_keys.py, backend.py, server.py)
- **Path construction is ad-hoc** — each caller builds
  `root_dir / "Participants" / hex / "NoteToSelf" / "Sync" / "core.db"`
  locally
- **The Hub reads the DB directly from disk**, not through the Manager — both
  provisioning.py and backend.py open the same file independently
- **manager.py, web.py, cli.py do NOT open the DB directly** — they delegate
  to provisioning.py through `TeamManager`

This scattered pattern makes the split harder than it needs to be. This branch
should centralize DB access as part of the refactor.

## Implementation Strategy: SQLite ATTACH

Use SQLite's `ATTACH DATABASE` to present both DBs through a single connection.

A central helper opens the shared DB, ATTACHes the local DB, and returns a
connection where both are available via schema-qualified table names:

- `shared.cloud_storage` — general service info (shared DB)
- `local.cloud_storage_credential` — auth material (device-local DB)

Benefits:

- most query code does not need to manage two separate connections
- JOINs across shared and local tables work naturally
- the split is invisible to most call sites once they use the central helper
- both the Manager and Hub can use the same helper

Note on Constraint 2.5 (visibility ≠ usability): query code must choose the
right JOIN style depending on intent:

- **LEFT JOIN** for listing/discovery — show known remotes even without local
  credentials (e.g. "these cloud storage providers are configured for this
  identity")
- **INNER JOIN** for operational use — require matching local credentials
  (e.g. "give me the credentials I need to actually authenticate")

Architecture decision:

- for **NoteToSelf storage**, standardize on a SQLite-first access layer owned
  by `small-sea-note-to-self`
- the new package owns **connection management and schema** — not query helpers
- higher-level query helpers (e.g. `get_cloud_storage()`, `set_notification_service()`)
  stay in their current packages (provisioning.py, backend.py) and use the
  central connection helper. Hub and Manager may have similar query code — that
  duplication is acceptable for now; DRY-ing it can be future work.
- team DB access can stay on its current patterns for now; this branch is about
  NoteToSelf

This branch migrates all NoteToSelf access through the central helper. Whether
every existing SQLAlchemy `create_engine` call gets rewritten to raw sqlite3
is a pragmatic per-call-site decision during implementation. The requirement is
"all NoteToSelf access goes through the central helper"; the helper itself
standardizes on sqlite3 + ATTACH.

The central helper should:

- take a participant path (or root_dir + participant_hex)
- open the shared DB at `.../NoteToSelf/Sync/core.db`
- ATTACH the local DB at `.../NoteToSelf/Local/device_local.db`
- create the local DB and run its schema if it does not exist yet
- return a connection ready for use

The goal is not "support both existing NoteToSelf access styles forever."
The goal is to migrate NoteToSelf access onto this narrower storage layer so
Manager and Hub stop opening NoteToSelf ad hoc.

## Concrete Change Areas

### 1. New `small-sea-note-to-self` package

New files:

- `packages/small-sea-note-to-self/pyproject.toml`
- `packages/small-sea-note-to-self/small_sea_note_to_self/__init__.py`
- `packages/small-sea-note-to-self/small_sea_note_to_self/db.py` — ATTACH
  helper, path construction
- `packages/small-sea-note-to-self/small_sea_note_to_self/sql/shared_schema.sql`
- `packages/small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`

Moved from Manager:

- `sender_keys.py` → `small_sea_note_to_self.sender_keys`
- `uuid7` → `small_sea_note_to_self.ids` (or similar)

### 2. Manager changes

Likely files:

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `packages/small-sea-manager/pyproject.toml`

Expected work:

- add `small-sea-note-to-self` dependency
- delete `core_note_to_self_schema.sql` from the Manager package (it moves to
  the new package as `shared_schema.sql` + `device_local_schema.sql`)
- replace ad-hoc DB opens with central helper imports
- replace local `uuid7` / `sender_keys` imports with new package imports

### 3. Hub changes

Likely files:

- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/small-sea-hub/small_sea_hub/crypto.py`
- `packages/small-sea-hub/small_sea_hub/server.py`
- `packages/small-sea-hub/pyproject.toml`
- `packages/small-sea-hub/spec.md`

Expected work:

- add `small-sea-note-to-self` dependency, drop `small-sea-manager` dependency
- replace ad-hoc DB opens and Manager imports with central helper
- update OAuth refresh to write to local DB
- update notification lookup to read from local DB
- update docs

### 4. Tests

Likely test areas:

- Manager provisioning / cloud storage tests
- Hub cloud API tests
- Hub OAuth refresh tests
- Hub notification tests
- any test that reads current team device keys / sender-key private material

## Implementation Order

### Phase 0: Lock the model

Before coding much:

- lock the table classification above
- confirm the fresh-schema-first stance
- lock the SQLite-first NoteToSelf access decision
- audit docs/issues that still describe the one-file NoteToSelf model

### Phase 1: Create `small-sea-note-to-self` package + schemas

Stand up the new package with:

- split schemas (shared and device-local)
- the central ATTACH helper
- `uuid7` (moved from provisioning.py)
- sender key load/save (moved from sender_keys.py)

Target outcome:

- `small-sea-note-to-self` is a real installable package under `packages/`
- schemas and helpers are importable
- Manager and Hub pyproject.toml depend on it (Hub drops Manager dependency)

### Phase 2: Manager-side migration

Migrate provisioning.py to import from `small-sea-note-to-self`. Remove
ad-hoc DB path construction and direct DB opens.

Target outcome:

- `add_cloud_storage`, `get_cloud_storage`, notification setup, team key
  lookup, and sender key operations all go through the central helper
- no provisioning code opens the NoteToSelf DB ad-hoc
- `sender_keys` and `uuid7` imports come from the new package

### Phase 3: Hub-side migration

Migrate backend.py, crypto.py, and server.py to import from
`small-sea-note-to-self`. Remove Manager imports from Hub.

Target outcome:

- Hub cloud operations read local credentials through ATTACH
- OAuth refresh persists refreshed tokens to the local DB only
- notification operations read the local notification config
- no Hub code opens the NoteToSelf DB ad-hoc
- Hub no longer depends on Manager

### Phase 4: Test sweep

Run and fix the tests that prove the split is real and the app still works.

This phase should include both:

- schema / helper micro tests
- Hub integration-style tests for cloud API, OAuth refresh, and notifications

### Phase 5: Docs + issue audit

Before wrapping the branch:

- update Manager spec
- update Hub spec
- update any top-level docs that still imply secrets live in shared NoteToSelf
- audit and update at least #61, #58, and #48 if the meaning of those issues
  changed materially
- open a follow-up issue if the branch exposes an additional required cleanup

## Validation

### Data-shape validation

- the shared NoteToSelf schema no longer contains the moved secret/runtime
  columns
- the local NoteToSelf DB lives outside `Sync/`
- the local DB is not tracked by git and is not required for cloning the
  shared repo

### Behavioral validation

- existing participant setup still works
- cloud storage add/list/get/remove still work through the split storage
- current team device key lookup still works
- notification setup and Hub notification delivery still work
- OAuth refresh updates local storage only
- shared cloud rows without local credentials fail cleanly as "known but not
  usable on this device"
- shared team-device-key rows without local secret rows are ignored by
  current-key lookup

### Repo-integrity validation

- Manager docs and Hub docs agree on where secrets now live
- related issues have been audited so future branches (#58 / #48) do not plan
  against the old storage model
- Hub no longer depends on Manager for NoteToSelf storage helpers

## Risks

- **under-scoping the Hub impact**
  - Mitigation: treat Hub backend/spec updates as first-class scope, not
    optional follow-up
- **moving too little of the runtime crypto state**
  - Mitigation: keep `team_sender_key` / `peer_sender_key` whole-table local
- **accidentally placing the local DB inside the synced repo**
  - Mitigation: lock the path decision in Phase 0 and test it
- **branch turns into a secret-storage redesign**
  - Mitigation: keep this mechanical; no keyring/vault work here

## Decisions (formerly Open For Discussion)

- **`notification_service` shared counterpart:** yes — same column-split pattern
  as `cloud_storage`. Shared side has general service info (id, protocol, url),
  local side has credentials. Consistent boundary rule across all service tables.
- **`team_device_key` public metadata shared:** yes — it serves as personal
  bookkeeping distinct from team-scoped certs. Redundancy with team-repo certs
  is intentional but flagged for future consistency review.
- **Helper layer location:** the central ATTACH helper lives in the new
  `small-sea-note-to-self` package. Both Manager and Hub depend on it. This
  fixes the existing Hub → Manager dependency rather than deepening it.
- **NoteToSelf access style:** standardize on a SQLite-first storage layer in
  `small-sea-note-to-self` rather than trying to preserve mixed ad hoc
  SQLAlchemy + raw sqlite NoteToSelf access patterns.
- **Local DB versioning:** separate schema file and separate version constant;
  create if missing, migrate narrowly if present but older.
- **Local DB git history:** no. The device-local DB is not tracked in git,
  including local-only git repos, because secrets and auth/runtime state should
  not accumulate in version history.
