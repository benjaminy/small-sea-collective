# Bootstrap NoteToSelf Through Hub-Owned Transport

Branch plan for `hub-bootstrap-transport`.
Primary tracker: #64.

## Goal

Make identity bootstrap work through **Hub-owned** cloud transport instead of
`LocalFolderRemote`, while keeping the current architectural rule intact:

- Manager still does not talk to the internet directly
- the joining device still starts without a normal NoteToSelf session
- the result is proven with local MinIO/S3 tests

This branch is about the **Hub transport boundary**, not about solving OAuth
bootstrap.

## Branch Claim

At the end of this branch, a critic should be able to say:

- Hub can fetch NoteToSelf bootstrap data before normal
  participant/session state exists on the joining device
- Manager still obeys “Hub as gateway”
- the flow works in a real cloud-shaped environment, not just a shared local
  filesystem
- OAuth bootstrap is still explicitly deferred

## Non-Goals

- no Dropbox bootstrap
- no GDrive bootstrap
- no new OAuth UX
- no redesign of identity-join trust
- no broad Hub session redesign
- no background NoteToSelf refresh/discovery UX

## Repo Findings

These are the important facts from the current codebase.

### 1. Hub startup is already easy

`SmallSeaBackend.__init__` can start on a blank root. It creates:

- `root_dir`
- `Logging/`
- `small_sea_collective_local.db`

So this branch does **not** need fake participant state just to start Hub.

### 2. Session creation is the real choke point

Normal session creation depends on live participant state:

- `request_session(...)` calls `_find_participant(...)`
- `request_session(...)` validates berth existence through `_resolve_berth(...)`
- `confirm_session(...)` resolves real `participant_id`, `team_id`, `app_id`,
  and `berth_id`

That is the hard part of this branch.

### 3. The S3 bootstrap read path mostly exists already

`proxy_cloud_file(...)` already supports anonymous S3 reads:

- no credentials required
- no participant DB reads required
- no deep adapter refactor required just to prove the concept

This is why S3/MinIO is the right proof harness.

### 4. Authorizing-side push is not the main refactor

The repo already has the right pattern for Hub-backed push:

- `TeamManager.push_team(...)` uses `SmallSeaRemote`
- `/cloud_file` and `/cloud/setup` already work with MinIO

So NoteToSelf push should be a modest extension of an existing pattern, not a
second major design problem.

### 5. Provisioning is still supposed to stay local-only

Per [packages/small-sea-manager/spec.md](/Users/ben8/Repos/small-sea-collective/packages/small-sea-manager/spec.md#L23), the split is still:

- `provisioning.py`: local filesystem/SQLite work only
- session layer (`manager.py`, Hub client): cloud/network orchestration

This branch should preserve that split.

## Decisions

### Decision 1: Manager does not read cloud storage directly

No direct cloud reads in Manager. No “just use the Hub adapters as a library
from provisioning” shortcut.

### Decision 2: This branch needs bootstrap-scoped Hub auth

The joining device needs some kind of Hub-issued auth/transport capability for
bootstrap, but it must be **narrower than a normal session**.

This plan intentionally does **not** lock the exact mechanism yet.

What is locked:

- bootstrap transport cannot depend on normal participant/session/berth lookup
- bootstrap-scoped auth must not be accepted by ordinary session routes
- bootstrap-scoped auth must be limited to bootstrap transport only

### Decision 3: S3/MinIO is the required proof

This branch is S3/MinIO-only.

That is both a scope limit and the validation harness:

- it exercises real cloud-shaped transport
- it keeps tests local and deterministic
- it avoids mixing this branch with OAuth credential design

### Decision 4: OAuth bootstrap is deferred

Dropbox/GDrive bootstrap remains follow-up work. This branch should fail
clearly for unsupported providers rather than half-support them.

### Decision 5: Provisioning stays local-only

The joining-side fetch must move into the Manager/session layer or a new
bootstrap orchestration helper, not deeper into `provisioning.py`.

Provisioning should remain responsible for:

- pending join state
- decrypt/validate welcome bundle
- local directory/db setup
- signature verification after fetch
- final cleanup

### Decision 6: `remote_descriptor` must include `bucket`

The joining device cannot derive the NoteToSelf bucket before it has fetched
NoteToSelf.

So the welcome bundle must explicitly carry:

- `protocol`
- `url`
- `bucket`

For this branch, that is enough.

## What We Should Avoid

These are the biggest traps the current draft exposed.

### 1. Do not smuggle bootstrap into the normal `session` model

A placeholder row in the ordinary `session` table is risky unless the branch
also adds hard enforcement that ordinary routes reject it.

The plan should keep the requirement at the right level:

- bootstrap auth must be structurally narrower than ordinary session auth

### 2. Do not move cloud orchestration into provisioning

That would contradict the current Manager architecture and make later cleanup
harder.

### 3. Do not broaden the bootstrap auth surface too far

Even for S3-only, the branch should not casually create an auth artifact that
can proxy arbitrary cloud locations forever. The bootstrap capability should be
scoped as tightly as practical to the welcome-bundle descriptor and bootstrap
transport only.

## Current Best Direction

Without locking the exact refactor yet, the repo research points to this shape:

### Joining side

- Manager/session layer asks local Hub for bootstrap-scoped transport
- Manager/session layer fetches NoteToSelf through Hub-owned transport
- provisioning handles prepare/finalize around that fetch

### Authorizing side

- NoteToSelf push should use the same general Hub-backed pattern as
  `push_team(...)`
- this is a supporting change, not the main design question

### Hub side

- Hub gets a bootstrap-scoped transport/auth path
- ordinary session-only routes reject that bootstrap auth
- existing anonymous S3 read logic should be reused where possible

## Scope

### In scope

- add a bootstrap-capable Hub transport path
- add bootstrap-scoped Hub auth that is narrower than normal sessions
- include `bucket` in the welcome bundle descriptor
- add authorizing-side NoteToSelf push through Hub for the MinIO proof path
- add MinIO/S3 micro tests for both push and bootstrap fetch
- update docs/specs

### Out of scope

- OAuth bootstrap credential design
- real provider-auth UX for a brand-new device
- automatic NoteToSelf refresh after bootstrap
- background discovery/watch flows
- making every bootstrap transport path share one final abstraction

## Validation

The branch should not be considered complete unless it proves all of these:

- the welcome bundle carries `protocol`, `url`, and `bucket`
- the authorizing device can publish NoteToSelf through Hub-owned cloud
  transport in MinIO/S3 tests
- the joining device can fetch NoteToSelf through Hub-owned bootstrap
  transport without a preexisting normal NoteToSelf session
- the joining device does not need a fake fully initialized NoteToSelf DB
  before fetch
- bootstrap-scoped auth is rejected by ordinary session-only routes such as
  `/session/info` and `/cloud_file`
- unsupported bootstrap providers fail clearly instead of silently falling
  back to local-only assumptions
- existing `LocalFolderRemote` identity-bootstrap tests still pass unless we
  intentionally replace them

## Implementation Order

### Phase 1: Lock the Hub transport boundary

- add bootstrap-scoped Hub auth/transport
- prove it works on a blank Hub root
- prove ordinary session routes reject it

### Phase 2: Authorizing-side NoteToSelf push through Hub

- add a NoteToSelf push path in the Manager/session layer
- prove NoteToSelf can be published to MinIO through Hub

### Phase 3: Joining-side bootstrap fetch through Hub

- move joining-side fetch orchestration into the session layer
- keep provisioning local-only
- fetch NoteToSelf through Hub-owned bootstrap transport

### Phase 4: End-to-end MinIO bootstrap

- full round-trip through Hub-owned transport
- verify the existing signed welcome-bundle checks still pass

### Phase 5: Docs

- update Hub and Manager specs
- document S3/MinIO-only support for bootstrap
- document OAuth deferral explicitly

## Risks

- **Bootstrap auth accidentally becomes “just another session.”**
  Mitigation: make route separation a required validation point.
- **Network code leaks into provisioning.**
  Mitigation: keep the provisioning/session-layer split explicit in the plan.
- **The branch turns into an OAuth design branch.**
  Mitigation: keep MinIO/S3 as the hard scope boundary.
- **Authorizing-side push and joining-side fetch get tangled together.**
  Mitigation: treat authorizing-side push as a supporting task, not the core
  refactor.

## Questions For The Next Refactor Pass

These are the real next-step design questions, and they should be answered in
the next planning round rather than here:

- should bootstrap-scoped auth live in a dedicated table, a typed session row,
  or a separate endpoint family?
- should bootstrap transport reuse `/cloud_proxy` or get its own narrower
  route?
- what is the cleanest session-layer home for joining-side bootstrap fetch?
- what is the cleanest NoteToSelf push helper on the authorizing side?

This plan intentionally stops short of solving those implementation details.
