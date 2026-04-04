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
4. the Shared File Vault web UI demonstrates the "Fetch then Merge" flow
5. micro tests make the guarantees convincing

## In Scope

- add a durable local ref for fetched peer state: `refs/peers/<member_id>/<branch>`
- update `CodSync` to return the fetched SHA and support pinning to a ref
- store minimal local metadata in `checkouts.db` to track sync state
- preserve current Hub boundaries and git correctness

## Out of Scope

- automatic background fetch
- automatic merge
- change previews
- rich conflict-resolution UI
- broad rollout to every app in this branch

## Core Technical Design

### 1. Ref Namespace
Durable parked state will live in:
- `refs/peers/<member_id_hex>/main` (for Niches)
- `refs/peers/<member_id_hex>/registry` (for the Registry)

### 2. "Already Merged" Detection
Determined by:
`git merge-base --is-ancestor <parked_sha> HEAD`

### 3. Metadata Storage
Shared File Vault's `checkouts.db` will gain a `peer_sync` table:
- `team_name`, `niche_name`, `member_id` (PK)
- `last_fetched_sha`: tip of the parked ref
- `last_merged_sha`: last successfully merged SHA from this peer

## Deliverables

### 1. Enhanced CodSync API
Update `CodSync` in `packages/cod-sync/cod_sync/protocol.py`:
- `fetch_from_remote(branches, pin_to_ref=None) -> str | None`: Returns the fetched SHA.
- `merge_from_ref(ref_name) -> int`: Merges a specific local ref.

### 2. Shared File Vault "Fetch then Merge" UI
Update `niche_detail.html`:
- Replace immediate "Pull" with "Check for Updates" (Fetch).
- If updates are parked and not yet merged, show a "Merge Changes" button with the SHA.

## Implementation Plan

### Phase 1: Cod Sync Primitives
- Update `CodSync.fetch_from_remote` to return the fetched commit SHA.
- Implement `pin_to_ref` logic in `fetch_chain` to update a durable local ref.
- Add `CodSync.merge_from_ref`.

### Phase 2: Metadata & Vault Logic
- Add `peer_sync` table to `vault.py`'s SQLite schema.
- Split `vault.pull_niche` and `vault.pull_registry` into `fetch_*` and `merge_*` variants.
- Implement the "is merged" check in `vault.py`.

### Phase 3: Web UI Wire-up
- Update `sync.py` to expose the new fetch/merge operations to the web layer.
- Update `templates/fragments/niche_detail.html` to show the multi-step flow.
- Ensure the Registry also follows the fetch-then-merge pattern.

### Phase 4: Validation
- **Micro tests:** Prove `refs/peers/` refs are created and persist correctly.
- **Integration tests:** Prove `merge-base` accurately reflects the "ready to merge" state.
- **UI tests:** Confirm the "Merge Changes" button only appears after a successful fetch of new data.

## Risks
- **Ref Collisions:** Ensuring `<member_id>` is unique and correctly derived.
- **Git State:** Handling merges that fail and leave the repo in a "merging" state (Standard SFV conflict handling should apply).
