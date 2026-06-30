# Follow-up

## Vocabulary propagation (prose only, no functional change)

This branch establishes `architecture.md` as canonical but does not yet reach every prose surface.
A first, low-risk follow-up branch should propagate the new vocabulary into remaining documentation, comments, and docstrings without touching any code identifier, schema value, or serialized contract.

In scope:

- residual SaaS-permission framing in untouched docs, including `packages/ssc-files/NOTES.md` and `Experiments/RealTimeTransport/README.md`
- code comments and docstrings that still describe teammate policy as central "permission" enforcement
- any package README that still implies a team authority rather than local integration

Explicitly out of scope for this pass (deferred to the runtime model; see "Remaining vocabulary surfaces"):

- renaming `berth_role`, its `read-only` / `read-write` values, or the admission identifiers
- changing API fields, exception messages, or serialized values

The point of separating this pass is to keep the cheap, safe text alignment from front-running the runtime design that justifies any identifier rename.

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

## Personal data off the chain

`architecture.md` now states canonically that the permanent Core chain carries only the governance skeleton and that personally identifying content (display names, identity material, free-text reasons) is intentionally not durable chain data.
See the `Personal Data Is Not in the Long-Term Chain` section and the sharpened excision item in `Documentation/open-architecture-questions.md`.

Prose propagation still to do (no functional change):

- The signed-event list above (display-name and teammate-unification claims) and the parallel lists in `design-record-codex-permission-vocab.md`, the Manager spec, and `wrasse-trust` should consistently distinguish the durable governance fact and commitment from the separable personal payload. `architecture.md` and the Manager-spec projection paragraph are already reconciled.
- The branch design record should capture the PII-off-chain decision and its rationale (excision would break replay; identity is observer-relative and accretes over time; pseudonymous default avoids an opt-out spotlight) at wrap-up.

Mechanism work (tracked in `open-architecture-questions.md`, needs cryptographic analysis before adoption):

- Hiding commitment scheme (a bare `hash(name)` is brute-forceable for low-entropy payloads); sign over the commitment, never the raw payload; keep governance replay strictly inert to payload content.
- Optional encryption-window key schedule, kept as a lineage separate from content/sender-key rotation; understood as roster-hygiene convenience, not erasure.
- The interaction-based identity-confidence (accretion) mechanism, which should be the primary identity story rather than a seed-only admission payload.

Micro tests, when implemented, should prove that a record's governance effect is unchanged when its personal payload is absent, encrypted-to-a-subset, or excised, and that an excised payload is unrecoverable from the retained commitment.

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

The assumption that constitutional events remain small and infrequent needs empirical protection rather than an invented design-time bound.
The current working assumption is roughly a few hundred bytes of constitutional history per day for a small-to-medium team — kilobytes per year, a few megabytes over a team's lifetime.
Implementation work should measure event counts, serialized event sizes, total signed-log size, and projection-rebuild cost against that assumption, and should expose useful warning thresholds before growth becomes operationally surprising.
If those measurements challenge the human-scale assumption, revisit the representation without silently deleting or severing signed history.

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
The spec prose now describes admission in `endorsement` / `Core integrator` terms while the code still uses approval/admin identifiers, so a later implementation branch should reconcile at least:

- the `read-only` / `read-write` values stored in `berth_role`
- the admission identifiers in `small-sea-manager`: the `admin_approval` table and its `approval_id` / `admin_teammate_id` columns, `sign_admin_approval`, `_approval_count`, `_insert_admin_approval`, the `admins` key in the frozen-governance snapshot, and `admission_quorum` / `quorum` naming
- the Manager UI's “Core berth role” and related action labels
- exception messages such as “requires read-write permission on the Core berth”

Those changes should follow a settled runtime model rather than lead it, and should land alongside the runtime that justifies each rename rather than as a standalone churn pass.

## Sequencing toward doc–code harmony

Merging this branch deliberately opens a documentation-ahead-of-code gap.
The plan to close it should exist before merge, even though most of the work happens after.

1. **This branch** — land the canonical model and the target-vs-current labeling in docs.
2. **Vocabulary propagation** (see first section) — align remaining prose/comments/docstrings, no identifier changes.
3. **Issue survey** — reconcile the existing backlog with the canonical model before building. Known candidates to amend or close:
   - #20 "Cloud chain compaction (rebase to new initial snapshot)" — title contradicts the no-rebase / preserve-DAG invariant; retitle.
   - #16 "Add permission checks to Hub cloud location methods" — reframe as mode-aware replication plus local Hub authorization.
   - #6 "Settle identity model … multi-device" — device-identity and recovery invariants are now fixed; trim to the open mechanism (prepared-recovery format/ceremony).
   - #162 — confirmed anchor for two-mode replication and merge-request discovery.
   - #57 admission pipeline, #150 storage-announcement delivery — fold under signed append-only history.
   - #11 / #12 pruning, #135 rebase-vs-merge, #73 / #43 rotation — reframe under retention horizons and staleness.
4. **Implementation branches**, in dependency order, each carrying the identifier renames the runtime justifies:
   - signed append-only teammate history (foundation for the rest)
   - mode-aware replication + merge-request discovery (#162)
   - prepared-recovery format and ceremony
   - retention horizons and object-retention mechanics
   - staleness observations and any checkpoint rule

Themes 2–5 above are epics, not single issues; the survey should split them at a granularity the team can schedule.
