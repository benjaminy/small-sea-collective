# Permission Vocabulary Design Record

## Decision

Use only two conceptual per-berth teammate integration modes: **automatic** and **proposal-only**.
Both modes describe recognized teammates who may read, author, and sign data.
The mode controls expected integration behavior rather than permission to produce bytes or records.

Keep the existing `berth_role`, `read-only`, `read-write`, `admin`, `contributor`, and `observer` implementation identifiers for now.
The stored values approximate the new modes, while existing role names are merely presets over them.

The two modes intentionally provide only one outer involvement layer for medium-sized teams.
They allow a smaller group to perform routine integration while other teammates mostly observe and propose, acknowledging the different levels of engagement and accountability a medium-sized team has without trying to design for every arrangement.
They are the current built-in set, not a claim that two modes are permanently sufficient: a later team-configurable role scheme (team-defined roles plus the changes each role is expected to integrate from which others) remains an open future direction.

## Signed teammate history

Significant teammate facts belong in signed append-only Core records.
Admissions, device links and revocations, display-name and teammate-unification claims, integration-mode changes, exclusions, storage announcements, proposals, and endorsements append facts instead of deleting or overwriting history.
Governance state is a projection of an accepted causal Core lineage, not a last-timestamp-wins view.
Operational signed streams may define narrower domain-specific projection rules.

Git remains responsible for snapshots, transport, versioning, and three-way merging.
Application database records carry the signatures and canonical payloads for domain facts whose provenance must survive repository-history manipulation and synthesized merges.
Proposal revisions preserve the proposer's payload signature and automatic integrators' endorsements; a modified payload requires a freshly signed revision.

Every Core database snapshot carries the complete signed teammate-history chain through its state.
The current database must be sufficient to explain a current trust decision without loading historical checkout blobs.
The complete Git commit DAG also remains available as bookkeeping and merge ancestry even if Cod Sync compacts its uploaded bundle chain or dehydrates older bulk blobs.
That dehydration is a shared-substrate content-retention policy, not a promise that old snapshot data no longer exists; any teammate who fetched a snapshot may retain it independently.

## Device recovery

An operational team-device private key remains bound to one device and is never copied so that another device can impersonate it.
Routine sibling enrollment and prepared recovery both create a fresh device key.
A separately prepared per-team recovery capability may authorize that new key for the existing teammate UUID, but only through a conspicuous signed recovery event designed against replay and rollback.
Without an enrolled sibling or prepared recovery, tier-two recovery creates a new teammate UUID and rebuilds connections through admission.

## Core semantics

An accepted Core lineage is the referent against which signer recognition and integrator standing are evaluated.
Validity is replayable relative to an anchor; adoption remains local.
Admission and merge-request machinery replace a central server as validity mechanisms, not as a way to mutate another participant's clone by fiat.
Persistent incompatible Core lineages are explicit team forks.
Forking is a failure mode to diagnose rather than a feature to encourage.

## Retention and quiet teammates

Content rehydration horizons are distinct from both Git history and constitutional history.
Core should keep a conservative live-data window because its data is normally small.
A signed staleness observation may warn that a teammate's last-seen state is approaching the edge of that window and preserve useful evidence for later reconvergence.
It is not a checkpoint, exclusion, finality declaration, or pruning authority; those effects require an explicit protocol rule that remains unsettled.

## Important implementation gaps

The current Hub watcher still discovers signals from every teammate without consulting the session berth's role row.
The current team schema mutates or deletes teammate, role, and invitation state instead of maintaining a complete signed append-only event source.
Merge-request discovery must remain observable when ordinary publications are not fetched, so mode-aware replication should be designed together with issue #162 rather than added as an isolated filter.
Prepared recovery, anti-replay ceremony, Git object-retention mechanics, live-window checkpoints, and staleness-observation schema are also design targets rather than implemented behavior.

## Why this boundary

Replacing every authorization-related word would erase real local security boundaries and produce less precise documentation.
Renaming schema and public values before proposal discovery and append-only history settle would make code churn lead the design.
The conservative split makes the decentralized model louder while preserving stable implementation surfaces for this draft.
