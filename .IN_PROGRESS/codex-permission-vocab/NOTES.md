# Survey Notes

## High-leverage wording

The main misleading teammate-permission language was concentrated in:

- `README.md`
- `architecture.md`
- `packages/small-sea-manager/spec.md`
- `Documentation/apps-and-teams.md`
- `Documentation/open-architecture-questions.md`
- one stale open question in `packages/small-sea-hub/spec.md`
- a misleading membership-enforcement claim in `packages/ssc-files/spec.md`
- team-as-permission-context wording in `packages/the-hedgerow/README.md`
- team-wide authorization wording in the early Small Sea Live design documents

The repository already contained the correct decentralized substance, especially the statements that there is no membership oracle and that each participant has a local team view.
The problem was that nearby permission and authorization shorthand kept inviting a SaaS interpretation.

## Language intentionally retained

Authorization remains accurate for locally enforced boundaries, including:

- Bearer-token and PIN-mediated Hub sessions
- app-to-berth session scope
- provider credentials and provider-side capabilities
- OS and platform permissions
- cryptographic verification of devices and signed artifacts

The branch does not mechanically replace those terms.

## Compatibility surface intentionally retained

The following identifiers remain unchanged:

- `berth_role`
- `read-only`
- `read-write`
- `admin`, `contributor`, and `observer`
- API fields, database columns, exception names, and serialized values

The prose now explains those existing names as shorthand for per-berth integration behavior.

The second draft narrows that interpretation further.
`read-write` approximates automatic integration, while `read-only` approximates proposal-only integration.
Recognition, readability, and proposal discovery are separate concerns rather than additional teammate categories.

The third draft adds the reason for stopping at two modes.
It is a pragmatic way for a medium-sized team to develop an inner routine-integration group and a larger outer mostly-observing group without asking Small Sea to model general group governance.

## Signed history direction

The current team schema is a mutable snapshot and does not preserve all historical teammate state.
The target model moves significant teammate facts into signed append-only Core records and treats mutable teammate, role, invitation, and UI rows as rebuildable projections.
Git continues to carry snapshots and merge histories; signatures inside the application database preserve domain authorship and meaning.

The Core lineage provides the state-relative referent for signer recognition and automatic-integrator standing.
A valid Core record does not force local adoption, and persistent incompatible accepted Core lineages constitute a team fork.

Every Core database snapshot must contain the complete signed teammate-history chain through that state.
This makes the current database sufficient to explain current trust without depending on old Git checkout blobs.
The complete Git commit DAG is also retained for bookkeeping and merge ancestry even when Cod Sync compacts transport bundles or dehydrates older bulk blobs.
Dehydration bounds what the shared sync substrate keeps readily rehydratable; it is not an erasure guarantee against teammates who already fetched and kept a snapshot.

## Recovery direction

Operational team-device keys remain bound to their devices and are never copied to another device.
A user may prepare a separate per-team recovery capability outside routine sync.
Using it is a loud signed event that authorizes a fresh device key for the existing teammate UUID, with replay and rollback defenses still to be designed.
Without an enrolled sibling or prepared recovery, tier-two recovery creates a new teammate UUID and rebuilds connections through admission.

## Quiet teammates and retention

Forking is not a desired workflow, but a team may need to advance while a teammate's clone remains stale for a long time.
A signed Core staleness observation could record who observed which teammate and berth, the last seen head or signal, and elapsed local updates or time before a live-data horizon moves.
Such an observation is warning and diagnostic evidence only.
It does not establish finality, advance another participant's horizon, or authorize pruning.
Core should keep a conservative live-data window because its data is usually small.

## Prior work

`Archive/branch-plan-admin-control-clarification.md` records an earlier pass that established local-view and social-sync semantics.
This branch builds on that work by separating replication and integration from local Hub authorization more explicitly and by moving the distinction earlier in the top-level documentation.
