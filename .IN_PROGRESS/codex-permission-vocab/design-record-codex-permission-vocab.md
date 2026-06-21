# Permission Vocabulary Design Record

## Decision

Use only two conceptual per-berth teammate integration modes: **automatic** and **proposal-only**.
Both modes describe recognized teammates who may read, author, and sign data.
The mode controls expected integration behavior rather than permission to produce bytes or records.

Keep the existing `berth_role`, `read-only`, `read-write`, `admin`, `contributor`, and `observer` implementation identifiers for now.
The stored values approximate the new modes, while existing role names are merely presets over them.

## Signed teammate history

Significant teammate facts belong in signed append-only Core records.
Admissions, device links and revocations, display-name and teammate-unification claims, integration-mode changes, exclusions, storage announcements, proposals, and endorsements append facts instead of deleting or overwriting history.
Governance state is a projection of an accepted causal Core lineage, not a last-timestamp-wins view.
Operational signed streams may define narrower domain-specific projection rules.

Git remains responsible for snapshots, transport, versioning, and three-way merging.
Application database records carry the signatures and canonical payloads for domain facts whose provenance must survive repository-history manipulation and synthesized merges.
Proposal revisions preserve the proposer's payload signature and automatic integrators' endorsements; a modified payload requires a freshly signed revision.

## Core semantics

An accepted Core lineage is the referent against which signer recognition and integrator standing are evaluated.
Validity is replayable relative to an anchor; adoption remains local.
Admission and merge-request machinery replace a central server as validity mechanisms, not as a way to mutate another participant's clone by fiat.
Persistent incompatible Core lineages are explicit team forks.

## Important implementation gaps

The current Hub watcher still discovers signals from every teammate without consulting the session berth's role row.
The current team schema mutates or deletes teammate, role, and invitation state instead of maintaining a complete signed append-only event source.
Merge-request discovery must remain observable when ordinary publications are not fetched, so mode-aware replication should be designed together with issue #162 rather than added as an isolated filter.

## Why this boundary

Replacing every authorization-related word would erase real local security boundaries and produce less precise documentation.
Renaming schema and public values before proposal discovery and append-only history settle would make code churn lead the design.
The conservative split makes the decentralized model louder while preserving stable implementation surfaces for this draft.
