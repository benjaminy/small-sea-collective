# Follow-up

## Mode-aware ordinary replication

The vocabulary survey exposed a runtime gap behind the proposed terminology.
`small_sea_hub.server._refresh_session_peers(...)` currently adds every teammate to `watched_peers` without consulting the session berth's `berth_role` row.
The watcher therefore observes peer signals broadly even though the intended proposal-only mode would avoid fetching and integrating that teammate's ordinary berth publications.

Do not mechanically add a role filter before the proposal-discovery design is settled.
A proposal-only teammate still needs a lightweight route for announcing a merge request to potential integrators.
Issue #162 tracks that broader design.

When implementation work begins, distinguish at least:

- observation of lightweight proposal announcements
- fetching ordinary berth publication bodies
- integrating fetched ordinary changes
- fetching and integrating an explicitly selected proposal

Micro tests should prove that a proposal-only teammate's ordinary changes are not fetched while their merge-request announcement remains discoverable.

## Signed append-only teammate history

The current team schema is not the target append-only model.
Important mutable paths include updates to `berth_role`, state transitions in admission proposal rows, teammate display-name replacement, and deletion of teammate rows during removal.

Future implementation work should introduce a signed teammate-event source that can represent at least:

- admission and exclusion
- device linking and revocation
- prepared recovery capability and recovery use
- display-name and teammate-unification claims
- per-berth integration-mode changes
- teammate berth storage announcements
- teammate-clone staleness observations
- proposals, revisions, endorsements, rejection, expiry, and finalization

Each event needs canonical signed bytes, author device identity, and enough stable identifiers to replay validation.
Governance-bearing events also need a causal Core anchor or parent references.
Do not use wall-clock timestamps or arrival order to choose governance state; operational announcement streams must document any narrower selector they use.
Competing valid event branches must be preserved and surfaced as a possible Core fork.

Mutable teammate, role, invitation, and UI tables may remain as caches or projections if they can be deterministically rebuilt from an accepted event lineage.
Because this repository is pre-alpha, the implementation should prefer a clean event model over compatibility shims for existing mutable rows.
Reuse the canonical-signing patterns already established by `key_certificate` and `teammate_berth_storage_announcement` rather than creating a parallel notion of authorship.

Micro tests should rebuild projections from signed history, verify historical integrator standing at multiple anchors, preserve revoked devices and excluded teammates in history, and retain both sides of a conflicting Core branch.

Every Core database snapshot must carry the complete signed event chain through that snapshot's state.
Micro tests should prove that a fresh clone can explain current teammate and device standing from the current Core database without retrieving historical checkout blobs.

## Device recovery ceremony

The architecture now fixes the recovery invariants but not the wire format or UX.
Ordinary team-device private keys remain bound to one device and are never copied to a replacement.
A prepared per-team recovery capability may authorize a fresh device key for the existing teammate UUID only through a conspicuous signed recovery event.

Future design must settle:

- backup-key generation, encryption, export, storage, verification, and rotation
- strict per-team isolation and whether recovery data contains any non-secret reconstruction metadata
- how a recovery event anchors to current Core state
- replay, rollback, duplication, and reused-recovery-capability handling
- interaction with revoked or compromised devices and sender-key rotation
- UI language that distinguishes routine sibling linking, prepared recovery, and tier-two readmission

If no sibling or prepared recovery exists, the Manager must create a new teammate UUID and guide fresh admission plus connection rebuilding.
It must not silently reuse an old operational device key or claim continuity that cannot be proven.

Micro tests should prove that the recovered device has a fresh key, that the lost device key cannot sign as the replacement, that replayed recovery events are rejected or visibly idempotent, and that the unprepared path never reuses the old teammate UUID.

## Git history and Core trust-log retention

Cod Sync chain compaction and application-data dehydration need an explicit object-retention implementation.
The complete Git commit DAG, parent relationships, and stable commit IDs remain available for bookkeeping and merge ancestry.
Compaction must not synthesize replacement history or require rebasing from a new snapshot root.

Old bulk blobs and trees may dehydrate beyond a live-data window, but every Core database snapshot retains the complete signed teammate-history chain through its state.
Core should default to a conservative window because its data is small and there is little pressure to prune aggressively.

Micro tests should compact a chain, verify every original commit ID and parent edge remains addressable, reconstruct a recent checkout from retained objects, and verify current Core trust without loading an old Core blob.
They should also document exactly which old trees and blobs may be absent and how a later fetch reports that absence.

## Staleness observations and checkpoints

A candidate signed Core record may state that one participant has not observed another teammate's clone advance for a measured time or number of accepted updates and expects a live-data horizon to pass that state soon.
Its likely payload includes observer, observed teammate, berth, last observed head or signal, local Core anchor, counters, and warning horizon.

The observation is evidence and warning only.
It cannot exclude the observed teammate, advance another participant's horizon, authorize garbage collection, or declare finality.
Different participants may publish conflicting observations without either record being malformed.

Separate protocol work must decide whether Small Sea needs signed checkpoints, who may endorse them, what evidence makes them eligible, and how a late teammate reconverges after old bulk blobs are no longer rehydratable.
The checkpoint design should prefer conservative Core retention and a catch-up warning period over aggressive pruning.

Micro tests should preserve conflicting observations, prove that an observation alone cannot change retention state, and show that the Manager can warn a quiet teammate or integrator before any separately authorized checkpoint takes effect.

## Canonical documentation ownership

`architecture.md` is now the canonical source for teammate identity, governance, integration-mode, recovery, and retention semantics.
Package documentation should explain mechanisms and implementation status, link upward for policy, and avoid independently redefining admission or recovery rules.
A later documentation pass should continue auditing stale brainstorming and archived design notes as those surfaces become active again.

## Remaining vocabulary surfaces

This conservative branch intentionally leaves runtime identifiers, UI labels, and serialized role values unchanged.
A later implementation branch may want to revisit:

- the `read-only` / `read-write` values stored in `berth_role`
- the Manager UI's “Core berth role” and related action labels
- exception messages such as “requires read-write permission on the Core berth”

Those changes should follow a settled runtime model rather than lead it.
