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
- the split is mechanical and reviewable, not a broader redesign of identity
  or sync behavior

## Critical Design Constraints

### 1. The device-local DB must live outside `Sync/`

This branch should **not** put `device_local.db` next to `core.db` inside the
synced git repo and trust `.gitignore` or convention to keep it local.

Default:

- shared DB stays at `Participants/{participant_hex}/NoteToSelf/Sync/core.db`
- device-local DB lives somewhere outside `Sync/`, e.g.
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
- likely `client_id`

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

### Whole-Table Local

#### `notification_service`

Default: move the whole table local.

Reason:

- notification configuration is device behavior, not shared identity metadata
- different devices may legitimately use different notification setups
- keeping only `url` shared buys very little and muddies the boundary

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
5. update docs and related GitHub issues so the repo stops claiming that auth
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

**Default:** outside `Sync/`.

Need to choose one path and use it consistently in code, tests, and docs.

### Q2. Does `client_id` stay shared?

**Default:** yes.

It identifies the OAuth app, not the user's auth session.
If later a provider forces this to differ per device, that can be revisited.

### Q3. Does `path_metadata` stay shared?

**Default:** yes.

Provider object IDs and similar path metadata are not auth secrets and may be
useful across devices.

### Q4. Do we need a separate version marker for the local DB?

**Default:** yes, but keep it simple.

The local DB should have its own schema version constant or equivalent so it
can evolve independently from the shared DB without pretending they are the
same file.

### Q5. How does the Hub read the local DB?

**Default:** directly, just like it already reads the shared DB.

This branch should not invent a new API boundary for secret lookup.
The Hub remains a local peer process reading Manager-owned SQLite files.

## Concrete Change Areas

### 1. Shared NoteToSelf schema

Likely files:

- `packages/small-sea-manager/small_sea_manager/sql/core_note_to_self_schema.sql`
- `packages/small-sea-manager/small_sea_manager/provisioning.py`

Expected work:

- remove the moved columns / tables from the shared schema
- keep version markers honest
- adjust any read/write helpers that currently assume one-file NoteToSelf

### 2. Device-local schema

Likely files:

- new SQL schema file under `packages/small-sea-manager/small_sea_manager/sql/`
- `packages/small-sea-manager/small_sea_manager/provisioning.py`

Expected work:

- create the local NoteToSelf DB on participant setup
- add the split local tables:
  - `cloud_storage_credential` (or chosen name)
  - `team_device_key_secret` (or chosen name)
  - local `notification_service`
  - local `team_sender_key`
  - local `peer_sender_key`

### 3. Manager/provisioning access layer

Likely files:

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `packages/small-sea-manager/small_sea_manager/manager.py`
- `packages/small-sea-manager/small_sea_manager/web.py`
- `packages/small-sea-manager/small_sea_manager/cli.py`

Expected work:

- centralize shared/local DB access behind helpers where possible
- avoid scattering dual-DB lookup logic at every call site
- keep public Manager operations stable where practical

### 4. Hub backend and specs

Likely files:

- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/small-sea-hub/spec.md`

Expected work:

- update Hub-side ORM / query assumptions
- make OAuth refresh write back to the **local** DB, not the shared DB
- make notification lookup read from the local DB
- update docs that currently say cloud credentials live in shared NoteToSelf

### 5. Tests

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
- choose the local DB path/name
- confirm the fresh-schema-first stance
- audit docs/issues that still describe the one-file NoteToSelf model

### Phase 1: Shared/local schema split

Implement the shared schema cleanup and new local schema side by side.

Target outcome:

- participant setup creates both DBs
- the shared DB no longer contains the moved secret/runtime state

### Phase 2: Manager-side helper refactor

Update provisioning and Manager accessors first.

Target outcome:

- `add_cloud_storage`, `get_cloud_storage`, notification setup, and team key
  lookup go through clear shared/local helpers

### Phase 3: Hub-side contract update

Update the Hub once the Manager-side storage layout is stable.

Target outcome:

- Hub cloud operations read local credentials successfully
- OAuth refresh persists refreshed tokens to the local DB only
- notification operations read the local notification config

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

### Repo-integrity validation

- Manager docs and Hub docs agree on where secrets now live
- related issues have been audited so future branches (#58 / #48) do not plan
  against the old storage model

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

## Open For Discussion

- whether `notification_service` truly needs any shared counterpart at all
- whether `team_device_key` public metadata belongs shared long-term or will
  later want its own more explicit representation
- whether the shared/local helper layer should live purely in provisioning or
  whether the Hub deserves its own small helper module for reading the split
  NoteToSelf state
