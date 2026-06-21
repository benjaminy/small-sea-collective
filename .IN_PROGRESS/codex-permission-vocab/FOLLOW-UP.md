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
- display-name and teammate-unification claims
- per-berth integration-mode changes
- teammate berth storage announcements
- proposals, revisions, endorsements, rejection, expiry, and finalization

Each event needs canonical signed bytes, author device identity, and enough stable identifiers to replay validation.
Governance-bearing events also need a causal Core anchor or parent references.
Do not use wall-clock timestamps or arrival order to choose governance state; operational announcement streams must document any narrower selector they use.
Competing valid event branches must be preserved and surfaced as a possible Core fork.

Mutable teammate, role, invitation, and UI tables may remain as caches or projections if they can be deterministically rebuilt from an accepted event lineage.
Because this repository is pre-alpha, the implementation should prefer a clean event model over compatibility shims for existing mutable rows.
Reuse the canonical-signing patterns already established by `key_certificate` and `teammate_berth_storage_announcement` rather than creating a parallel notion of authorship.

Micro tests should rebuild projections from signed history, verify historical integrator standing at multiple anchors, preserve revoked devices and excluded teammates in history, and retain both sides of a conflicting Core branch.

## Remaining vocabulary surfaces

This conservative branch intentionally leaves runtime identifiers, UI labels, and serialized role values unchanged.
A later implementation branch may want to revisit:

- the `read-only` / `read-write` values stored in `berth_role`
- the Manager UI's “Core berth role” and related action labels
- exception messages such as “requires read-write permission on the Core berth”

Those changes should follow a settled runtime model rather than lead it.
