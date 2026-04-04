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
- store the minimum app-local metadata needed to show parked-update state
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

## Product Shape

First-version user flow:

1. the app can tell that a peer may have updates
2. the app can fetch and park that peer's latest state without merging it
3. the UI can show "fetched and ready to merge"
4. the user chooses when to merge

Important honesty rule:

- a Hub signal is only an update hint
- a parked ref is a concrete fetched git state

This branch should not claim to know more than that.

## Core Technical Decisions

### 1. Ref Namespace

Use a durable peer namespace inside each local git repo:

- `refs/peers/<member_id_hex>/<branch>`

For the first version, `<branch>` will normally be `main`.

Important clarification:

- niche repos and the registry repo are separate repos
- the registry should use the same peer-ref shape inside its own repo, not a
  special pseudo-branch like `registry`

### 2. Ref Lifecycle

Keep the lifecycle simple:

- fetch updates the parked ref using a latest-wins policy
- successful merge does not delete the parked ref
- failed merge does not delete the parked ref

That keeps git state inspectable and lets ancestry answer whether the parked
tip has already been integrated.

### 3. Primitive Ownership

Keep the low-level git primitive in `CodSync`, but keep teammate-facing state in
Shared File Vault.

That likely means:

- `CodSync.fetch_from_remote(..., pin_to_ref=...)` or an equivalent helper that
  returns the fetched SHA
- `CodSync.merge_from_ref(ref_name)` or an equivalent merge helper
- Vault-level code owns parked-update status and UI metadata

### 4. "Already Merged" Detection

Use git ancestry rather than only comparing stored markers:

- `git merge-base --is-ancestor <parked_sha> HEAD`

That gives a solid definition of "already merged" even if app-local metadata
gets stale.

### 5. App-Local Metadata

Keep metadata minimal and treat it as a UI cache, not as the source of truth.

For Vault, the most likely home is a new table in `checkouts.db` or a small
adjacent app-local SQLite table with enough scope to distinguish:

- team
- repo kind (`niche` vs `registry`)
- niche name when applicable
- peer member ID
- last fetched SHA
- last merged SHA

The git ref is the source of truth for merge behavior. The database only helps
drive teammate-facing status in the UI.

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

Existing callers that want the old immediate-merge behavior should still be
able to keep using a simple path.

### 3. Shared File Vault Web Flow

Use Shared File Vault as the proving ground.

That slice should show:

- peer update awareness
- fetch without merge
- merge later from the parked ref
- existing conflict surfacing still works

In the web UI, the first-version flow should be closer to:

- "Check for Updates" or equivalent fetch action
- parked update status once a peer tip is fetched
- explicit "Merge Changes" action later

## Implementation Order

### Phase 0: Lock the semantics

Write down the answers to these before editing much code:

- exact parked-ref naming
- what SHA fetch returns
- what counts as "already merged"
- what happens to parked state after successful merge
- what happens to parked state after failed merge
- how Vault scopes parked state for niche repos vs the registry repo

### Phase 1: CodSync primitive

Start in [`packages/cod-sync/cod_sync/protocol.py`](/Users/ben8/Repos/small-sea-collective/packages/cod-sync/cod_sync/protocol.py).

Implement:

- fetch that pins to a durable ref
- fetch that returns the fetched SHA
- merge from a named local ref

Required behavior:

- fetching peer A creates or updates only peer A's parked ref
- fetching peer B does not disturb peer A's parked ref
- the parked ref resolves to a commit in the local repo
- merge can target the parked ref directly

### Phase 2: Vault storage and core flow

Then update [`packages/shared-file-vault/shared_file_vault/vault.py`](/Users/ben8/Repos/small-sea-collective/packages/shared-file-vault/shared_file_vault/vault.py).

Implement:

- minimal parked-update metadata storage
- fetch and merge as separate Vault-level operations
- ancestry-based "already merged" checks

Keep the metadata clearly subordinate to git state.

### Phase 3: Vault sync layer

Then update [`packages/shared-file-vault/shared_file_vault/sync.py`](/Users/ben8/Repos/small-sea-collective/packages/shared-file-vault/shared_file_vault/sync.py).

Expose:

- fetch-via-Hub without merge
- merge of an already parked peer ref
- a simple status model the web layer can consume

### Phase 4: Web UI

Then update:

- [`packages/shared-file-vault/shared_file_vault/web.py`](/Users/ben8/Repos/small-sea-collective/packages/shared-file-vault/shared_file_vault/web.py)
- [`packages/shared-file-vault/shared_file_vault/templates/fragments/niche_detail.html`](/Users/ben8/Repos/small-sea-collective/packages/shared-file-vault/shared_file_vault/templates/fragments/niche_detail.html)

Change the web flow from:

- fetch and immediately merge

to:

- fetch and park
- show parked update state
- merge on explicit user action

This should stay intentionally manual in the first version.

### Phase 5: Validation and write-up

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

## What Landed

The branch now implements the core fetch-then-merge primitive:

- `CodSync.fetch_from_remote(..., pin_to_ref=...)` can return the fetched SHA
  and pin it to a durable local ref
- `CodSync.merge_from_ref(ref_name)` can merge an already parked ref
- Shared File Vault splits peer sync into fetch and merge steps
- parked refs live under `refs/peers/<member_id_hex>/main`
- Vault stores minimal peer sync metadata in app-local SQLite as a UI cache
- "already merged" is determined from git ancestry rather than only cached
  metadata
- the Shared File Vault web UI now exposes a fetch-first, merge-later flow

Compatibility paths were kept where practical:

- the older immediate pull path still exists as a wrapper that does fetch then
  merge
- manager invitation and pull flows were updated to work with the new
  `fetch_from_remote()` return value

## Validation Completed

The following targeted micro tests passed after implementation:

- `uv run pytest packages/cod-sync/tests/test_roundtrip.py`
- `uv run pytest packages/shared-file-vault/tests/test_hub_sync.py`
- `uv run pytest packages/shared-file-vault/tests/test_web_sync.py`
- `uv run pytest packages/small-sea-manager/tests/test_merge_conflict.py packages/small-sea-manager/tests/test_hub_invitation_flow.py`

## Risks

- choosing a ref namespace that becomes awkward later
- letting app-specific UX leak down into Cod Sync
- confusing "signal noticed" with "fetched and ready"
- overbuilding metadata before the core primitive is proven

## Discussion

Nothing here looks like a blocker for this branch.

The remaining discussion topics are follow-on policy or cleanup choices:

- whether and when to prune parked refs after peer removal or niche deletion
- whether the registry should always be fetched or merged ahead of niches in
  every caller and UI path
- whether the current UI wording is the exact language we want for "Check For
  Updates" versus "Fetch"
- whether to archive this plan into
  `Archive/branch-plan-better-fetch-merge-separation.md` immediately before
  merge, per repo habit

## Recommendation

Keep this branch about one thing:

- durable parked peer fetches plus explicit later merge

Once that is solid, the notification-driven automation and richer decision
support become much easier to reason about as standalone GitHub issues.
