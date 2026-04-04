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
4. the Shared File Vault web UI demonstrates the fetch-then-merge flow
5. micro tests make the guarantees convincing

## In Scope

- add a durable local ref for fetched peer state
- keep fetch and merge as separate app-facing operations
- choose and document a stable ref naming scheme
- update `CodSync` so fetch can report the fetched SHA and pin it to a ref
- store the minimum app-local metadata needed to know:
  - what peer tip is currently parked
  - whether it has already been merged
- prove the pattern in the Shared File Vault web UI
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

## Core Technical Design

### 1. Ref Namespace & Lifecycle

Use a durable peer namespace inside each local git repo:

- `refs/peers/<member_id_hex>/<branch>`

For the first version, `<branch>` will normally be `main`.

**Lifecycle Rules:**
- **Fetch:** Creating or updating a parked ref is a "latest wins" operation.
- **Merge Success:** Keep the parked ref after a successful merge. The ancestry check (`merge-base --is-ancestor`) will correctly identify it as "already merged."
- **Merge Failure:** Keep the parked ref. The user may need it to re-attempt the merge after aborting or resolving conflicts.
- **Pruning:** Define a strategy for pruning refs when a peer is removed from the team or a niche is deleted.

Important clarification:

- niche repos and the registry repo are separate repos
- the registry should therefore use the same peer-ref shape inside its own repo,
  not a special pseudo-branch like `registry`

### 2. Primitive Ownership

Keep the low-level git primitive in `CodSync`, but keep app-specific teammate
state in Shared File Vault.

That likely means:

- `CodSync.fetch_from_remote(..., pin_to_ref=...)` or an equivalent helper that
  returns the fetched SHA
- `CodSync.merge_from_ref(ref_name)` or an equivalent merge helper
- Vault-level code owns teammate-facing status and metadata

### 3. "Already Merged" Detection

Use git ancestry rather than only comparing stored markers:

- `git merge-base --is-ancestor <parked_sha> HEAD`

That gives a solid definition of "already merged" even if app-local metadata
gets stale. This also naturally handles "obsolete" parked refs if `HEAD` has 
moved past them due to other merges or local commits.

### 4. App-Local Metadata (UI Cache)

Keep this minimal and app-local.

For Vault, the most likely home is a new table in `checkouts.db` or another
small adjacent app-local SQLite table, with enough scope to distinguish:

- team
- repo kind (`niche` vs `registry`)
- niche name when applicable
- peer member ID
- last fetched SHA
- last merged SHA

**Source of Truth:** The git ref is the source of truth for the merge operation. 
The database is a UI cache used to drive status indicators (e.g., "Update 
Available").

## Deliverables

### 1. Enhanced CodSync Primitive

A caller should be able to:

- fetch peer Alice through the Hub
- learn which commit is now parked for Alice
- merge that parked commit later

The likely API shape is:

- `fetch_from_remote(branches, pin_to_ref=None) -> fetched_sha | None`
- `merge_from_ref(ref_name) -> exit_code`

### 2. Split App-Facing Operations

Expose separate operations for:

- fetch peer update without merge
- inspect parked peer-update state
- merge parked peer update

**Multi-Repo Coordination:** In Shared File Vault, the Registry should usually 
be merged before Niches to ensure any member/permission changes are applied 
first.

### 3. Shared File Vault Web Flow

Use Shared File Vault as the proving ground.

That slice should show:

- peer update awareness
- fetch without merge
- merge later from the parked ref
- existing conflict surfacing still works (and preserves the parked ref)

In the web UI, the first-version flow should be closer to:

- "Check for Updates" or equivalent fetch action
- parked update status once a peer tip is fetched
- explicit "Merge Changes" action later

## Implementation Plan

### Phase 0: Lock the semantics

Before deeper code changes, write down:

- the chosen parked-ref namespace
- what commit SHA the caller gets back after fetch
- what counts as "already merged"
- what happens to parked state after successful merge
- what happens to parked state after failed merge (conflicts/aborts)
- how Vault scopes parked state for niche repos vs the registry repo

### Phase 1: Add durable peer refs

Implement the parked-fetch primitive.

Required behavior:

- fetching peer A creates or updates only peer A's parked ref
- fetching peer B does not disturb peer A's parked ref
- the parked ref resolves to a commit in the local repo
- merge can target the parked ref directly
- fetch reports the fetched SHA back to the caller
- "latest wins" policy for repeated fetches

### Phase 2: Add minimal app-local state

Add only enough metadata to support honest UI:

- fetched commit
- merged commit
- scope fields sufficient to distinguish niche vs registry state

Avoid building a larger "notification center" in this branch. Ensure the 
metadata is treated as a cache of the git state.

### Phase 3: Wire one app flow

In Shared File Vault, change the web flow from:

- fetch and immediately merge

to:

- fetch and park
- show parked update state
- merge on explicit user action
- handle merge conflicts by preserving the parked ref for retry/investigation

This should stay intentionally manual in the first version.

### Phase 4: Validate and document

Add micro tests and tighten the write-up so a skeptical reader can see exactly
what is now guaranteed and what is still future work.

## Validation

Add micro tests that prove:

- parked fetch creates a durable ref
- parked refs for different peers do not collide
- re-fetching one peer updates only that peer's ref
- the fetched SHA returned to the caller matches the parked ref tip
- merge from the parked ref works in the happy path
- merge conflicts are still surfaced when merging from a parked ref
- the parked ref remains usable after a failed merge unless we deliberately
  clear it

Add app-level tests for the chosen Vault slice that prove:

- the app can show "update available to merge"
- the app can merge later without re-fetching
- the flow still goes through the Hub rather than direct cloud access
- "already merged" detection is driven by actual git ancestry, not only cached
  metadata

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
