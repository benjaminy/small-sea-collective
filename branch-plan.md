# Better Fetch / Merge Separation

Branch plan for `better-fetch-merge-separation`.

## Branch Goal

Prove and implement one core Small Sea sync primitive:

- apps can fetch a peer's Cod Sync state through the Hub
- the fetched tip is pinned to a durable local ref
- the user can merge that parked ref later

This branch is about making "fetch now, merge later" real and trustworthy.

## Why This Branch Exists

The repo is already close to this model:

- the Hub already notices peer updates and exposes notification/watch APIs
- Cod Sync already has separate fetch and merge steps
- peer reads already go through `PeerSmallSeaRemote`

But current app flows still fetch and immediately merge, and the fetched peer
tip currently lives in Cod Sync's temp bundle remote rather than a stable local
ref. That is good enough for immediate merge, but not good enough for a user-
controlled merge workflow.

## Branch Success

This branch succeeds if, at the end:

1. a peer fetch can be parked in a durable local ref
2. later fetches do not overwrite parked refs for other peers
3. a caller can merge the parked ref later and get the same merge behavior as
   the current immediate-merge path
4. one real app surface demonstrates the flow end-to-end through the Hub
5. micro tests make the guarantees convincing

## In Scope

- add a durable local ref for fetched peer state
- keep fetch and merge as separate app-facing operations
- choose and document a stable ref naming scheme
- store the minimum local metadata needed to know:
  - what peer tip is currently parked
  - whether it has already been merged
- prove the pattern in one app-facing slice
- preserve current Hub boundaries and git correctness

## Out of Scope

- automatic background fetch
- automatic merge
- change previews
- "benign change" heuristics
- rich conflict-resolution UI
- redesigning Hub notifications
- broad rollout to every app in this branch

Follow-on work is tracked in GitHub issues `#35` and `#36`:

- notification-driven parked-update UX
- preview and auto-merge policy for parked peer updates

## Narrowed Product Shape

The branch target is deliberately smaller than the broader long-term UX vision.

First-version user flow:

1. the app can tell that a peer may have updates
2. the app can fetch and park that peer's latest state without merging it
3. the UI can show "fetched and ready to merge"
4. the user chooses when to merge

The important honesty rule is:

- a Hub signal is only an update hint
- a parked ref is a concrete fetched git state

This branch should not claim to know more than that.

## Core Decisions This Branch Must Make

### 1. Where parked peer state lives

Pick one durable representation and document it. Likely options:

- `refs/peers/<member>/<branch>`
- app-owned `refs/remotes/...`
- lightweight branches or tags

The choice must clearly encode peer identity and branch name, and should not
quietly block future multi-branch use.

### 2. Which layer owns the primitive

Pick the lowest layer that stays reusable without making Cod Sync app-specific.

Likely options:

- Cod Sync owns "fetch and pin"
- app sync helpers own "fetch, then pin"

### 3. What local state the app remembers

Keep this minimal. The app likely needs to track:

- latest fetched commit per peer
- latest merged commit per peer

This should stay app-local, not shared/team DB state, unless a stronger reason
appears.

## Deliverables

### 1. Durable parked-fetch primitive

A caller should be able to:

- fetch peer Alice through the Hub
- learn which commit is now parked for Alice
- merge that parked commit later

### 2. Split app-facing operations

Expose separate operations for:

- fetch peer update without merge
- inspect parked peer-update state
- merge parked peer update

Existing callers that want the old immediate-merge behavior should still be
able to keep using a simple path.

### 3. One real app demonstration

Use Shared File Vault as the proving ground.

That slice should show:

- peer update awareness
- fetch without merge
- merge later from the parked ref
- existing conflict surfacing still works

## Implementation Plan

### Phase 0: Lock the semantics

Before deeper code changes, write down:

- the chosen parked-ref namespace
- what commit SHA the caller gets back after fetch
- what counts as "already merged"
- what happens to parked state after successful merge
- what happens to parked state after failed merge

### Phase 1: Add durable peer refs

Implement the parked-fetch primitive.

Required behavior:

- fetching peer A creates or updates only peer A's parked ref
- fetching peer B does not disturb peer A's parked ref
- the parked ref resolves to a commit in the local repo
- merge can target the parked ref directly

### Phase 2: Add minimal app-local state

Add only enough metadata to support honest UI:

- fetched commit
- merged commit

Avoid building a larger "notification center" in this branch.

### Phase 3: Wire one app flow

In Shared File Vault, change one sync path from:

- fetch and immediately merge

to:

- fetch and park
- show parked update state
- merge on explicit user action

This should stay intentionally manual in the first version.

### Phase 4: Validate and document

Add micro tests and tighten the write-up so a skeptical reader can see exactly
what is now guaranteed and what is still future work.

## Validation

Add micro tests that prove:

- parked fetch creates a durable ref
- parked refs for different peers do not collide
- re-fetching one peer updates only that peer's ref
- merge from the parked ref works in the happy path
- merge conflicts are still surfaced when merging from a parked ref
- the parked ref remains usable after a failed merge unless we deliberately
  clear it

Add app-level tests for the chosen Vault slice that prove:

- the app can show "update available to merge"
- the app can merge later without re-fetching
- the flow still goes through the Hub rather than direct cloud access

## Risks

- choosing a ref namespace that becomes awkward later
- letting app-specific UX leak down into Cod Sync
- confusing "signal noticed" with "fetched and ready"
- overbuilding metadata before the core primitive is proven

## Recommendation

Keep this branch about one thing:

- durable parked peer fetches plus explicit later merge

Once that is solid, the notification-driven automation and richer decision
support become much easier to reason about as standalone GitHub issues.
