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
- discovering teams and related berth/app metadata created elsewhere by the
  same identity
- doing all of that without syncing device-local secrets or sneaking in
  automatic team admission

That missing slice keeps showing up as deferred follow-up work in the recent
identity/runtime/crypto branches. It is now the clearest next dependency.

## Proposed Goal

After this branch lands:

1. a second device can refresh shared NoteToSelf through Hub-mediated sync and
   discover teams created on another device of the same identity
2. the Hub can surface a narrow "shared NoteToSelf changed" update-awareness
   signal to the Manager instead of leaving refresh entirely blind
3. the Manager can adopt refreshed shared NoteToSelf state and rescan newly
   visible teams/apps/berths without inventing a shadow registry
4. discovery remains distinct from team access: seeing a team in NoteToSelf is
   not the same thing as having a local clone or local team-device participation
5. device-local NoteToSelf secrets and runtime state remain local and unsynced

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
- `app`
- `team_app_berth`
- any related shared locator metadata already meant to sync across devices

### S2. Hub owns transport and update detection; Manager owns adoption and writes

This branch should keep the existing architectural seam honest:

- Hub detects that shared NoteToSelf may have changed remotely
- Hub handles the actual cloud transport for pull/push
- Manager remains the only writer of NoteToSelf DBs
- Manager decides how to adopt the refreshed state and what newly visible teams
  or berths mean locally

### S3. User-initiated refresh remains the default

The Hub should help with update-awareness, but this branch should not turn
NoteToSelf sync into a magical always-on auto-adopt system.

The intended model is:

- Hub can notice incoming change and surface it through a narrow mailbox/reminder
  seam
- Manager can present or act on that signal in a controlled way
- tests may auto-trigger refresh for local proof, but production semantics stay
  user-initiated by default

### S4. Discovery is not team join

This branch should be explicit about the boundary:

- discovering a team in shared NoteToSelf means "this identity knows about this
  team"
- it does not automatically create a local team clone
- it does not automatically mint local team-device keys
- it does not automatically complete encrypted team bootstrap/admission

That follow-through remains separate work already owned by the team-join and
runtime branches.

### S5. Device-local NoteToSelf state must remain out of sync

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

### S6. Keep the mailbox seam narrow

The Manager spec already says the Hub should place an incoming notification in
its mailbox when remote data arrives. This branch should implement the smallest
honest version of that story for shared NoteToSelf, not a grand new messaging
framework.

### S7. Pre-alpha rules apply

Prefer a clean, reviewable implementation over backward-compatibility shims for
old sandboxes.

## In Scope

### 1. Shared NoteToSelf refresh path

Add or tighten the explicit Manager/Hub seam for refreshing shared NoteToSelf
through Hub-mediated transport.

Minimum behavior:

- a device can pull shared NoteToSelf updates through the Hub
- the refreshed shared DB/repo becomes the source of truth immediately after
  adoption
- refresh works in local micro-test setups without internet dependence

### 2. Update-awareness for shared NoteToSelf

Add the smallest honest update-awareness path so the Manager is not blind to
remote NoteToSelf changes.

Minimum behavior:

- Hub detects that the NoteToSelf remote changed or may have changed
- Hub surfaces a narrow update signal/mailbox item for the local participant
- Manager has a clean seam for reacting to that signal

### 3. Team and berth discovery after refresh

After a successful shared NoteToSelf refresh, the Manager should rescan and
surface newly visible shared metadata, including:

- teams created on another device
- related app / berth metadata needed to keep local discovery coherent

The implementation should avoid shadow copies of discovery state when the
shared NoteToSelf tables already contain the truth.

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

- two-device same-identity setup where device A creates or records a team in
  shared NoteToSelf and device B does not see it until refresh
- after shared NoteToSelf refresh, device B discovers the team cleanly
- device B still does not auto-create local team participation for that team
- shared NoteToSelf refresh does not sync device-local secrets/runtime state
- Hub update-awareness path produces a narrow signal/mailbox effect instead of
  silent magic

## Out Of Scope

- automatic team join / local team clone after discovery
- sender-key crypto redesign
- revocation or device-removal semantics
- periodic sender-key rotation policy
- broad UI/UX redesign
- generalized multi-remote arbitration beyond the current practical assumption
- redesigning the whole Hub mailbox system beyond what this branch actually
  needs

## Concrete Change Areas

### 1. Manager refresh/adoption seams

- `packages/small-sea-manager/small_sea_manager/manager.py`
- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- any narrow helper layer already responsible for NoteToSelf sync/discovery

Likely work:

- explicit shared NoteToSelf refresh entry point
- post-refresh rescan of teams/apps/berths
- clear distinction between "discovered in NoteToSelf" and "locally joined"

### 2. Hub update-awareness / transport seams

- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/small-sea-hub/small_sea_hub/server.py`
- any mailbox or watch helper already used for incoming reminders

Likely work:

- detect incoming shared NoteToSelf changes
- emit the narrow update signal/mailbox effect
- support the steady-state Hub-mediated NoteToSelf refresh path cleanly

### 3. Specs

- `packages/small-sea-manager/spec.md`
- `packages/small-sea-hub/spec.md`

### 4. Tests

- focused Manager micro tests for refresh/discovery semantics
- focused Hub micro tests for the update-awareness seam
- one end-to-end local two-installation micro test proving same-identity team
  discovery after refresh

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- there is one explicit code path for shared NoteToSelf refresh instead of
  ad-hoc local DB poking
- the Hub, not the Manager, still owns the actual cloud transport
- the Manager, not the Hub, still owns NoteToSelf DB writes/adoption
- device B in a two-installation same-identity test does not see a newly added
  team before refresh
- after refresh, device B does see the team in NoteToSelf-backed discovery
- after refresh, device B still has no local team clone or local team-device
  participation unless separate join/bootstrap work runs
- shared/device-local NoteToSelf boundaries remain intact after refresh
- the new update-awareness seam is narrow enough that future mailbox work is
  easier, not more magical
- the implementation makes later runtime/team-join branches simpler instead of
  introducing another shadow source of truth

## Open Questions

### Q1. How coarse should the mailbox/update signal be?

Recommended first-pass answer:

- keep it coarse and berth-scoped
- something like "shared NoteToSelf may have changed" is enough
- do not make this branch compute rich semantic diffs inside the Hub

### Q2. What is the smallest honest local representation of a discovered team?

This branch should likely avoid a new discovery table and instead derive the
state from:

- shared NoteToSelf `team` rows
- existence or absence of local team clone/adoption state

If implementation pressure suggests a tiny explicit local marker is necessary,
the branch should justify it rather than slipping it in silently.
