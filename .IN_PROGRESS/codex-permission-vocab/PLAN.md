# Permission Vocabulary Plan

## Goal

Make Small Sea's decentralized collaboration model harder to mistake for a centrally enforced SaaS permission system.
Reduce teammate integration policy to two per-berth modes: `automatic` and `proposal-only`.
Record significant teammate facts as signed append-only domain history, with mutable tables treated as rebuildable projections.
Make device non-impersonation, prepared recovery, complete Core trust-log retention, complete Git DAG retention, and cautious treatment of stale teammates explicit architectural invariants.
Retain authorization and access-control language where a local component actually enforces a boundary, especially Hub client sessions, credential access, and cryptographic readability.

This branch is intentionally conservative.
It will improve the conceptual framing and the most misleading prose without renaming database columns, API fields, exceptions, or every established role in one sweep.

## Working distinctions

- **Recognition** describes whether a signed record can be traced through accepted teammate and device history at its referenced state.
- **Readability** describes who receives the cryptographic material needed to interpret future berth updates.
- **Integration mode** is the only per-berth teammate category: ordinary publications are either automatic or proposal-only.
- **Replication and discovery** are mechanisms that mostly follow integration intent but must preserve a lightweight proposal path.
- **Authorization** remains appropriate for locally enforced client-to-Hub and component boundaries.
- **Core validity** is replayable relative to an accepted anchor, while adoption remains local and persistent disagreement creates a team fork.
- **Involvement gradient** stops at a routine-integrator inner layer and a mostly-observing proposal layer rather than becoming a general governance taxonomy.
- **Device recovery** authorizes a fresh device key through a loud signed event and never copies or impersonates an ordinary device key.
- **Retention** keeps the complete Git commit DAG and a complete signed trust log in every Core snapshot, while allowing old bulk blobs outside a live-data window to dehydrate.
- **Staleness observations** are signed evidence and warning, not silent checkpoint or pruning authority.

## Survey

1. Inventory permission, authorization, access-control, role, pull, watch, and merge language across top-level and package documentation.
2. Classify each important use as locally enforced, cryptographic, provider-enforced, or decentralized teammate policy.
3. Identify the smallest set of high-leverage documents whose framing influences the rest of the repository.
4. Record follow-up work rather than mechanically renaming schema and code identifiers on this branch.

## Implementation

1. Add an early, explicit explanation that a team has no central authorization service and each clone independently decides what to integrate.
2. Define signed append-only teammate history and the division between Git provenance and domain-record signatures.
3. Define automatic and proposal-only as the only conceptual per-berth integration modes.
4. Explain Core as constitutional history whose validity is anchor-relative and whose adoption remains local.
5. Update the Manager specification's current mutable schema and operations honestly as implementation gaps.
6. Update other core specifications only where the old analogy is prominent or actively misleading.
7. Define the strict separation between device linking, prepared recovery of an existing teammate UUID, and tier-two readmission under a new UUID.
8. Separate Cod Sync transport compaction from Git DAG retention and Core trust-log retention.
9. Record staleness observations as a possible aid to conservative live-window advancement without treating them as finality.
10. Preserve semantic line breaks in all edited prose.

## Validation

The branch should convince a skeptical reviewer through multiple independent checks.

### Goal evidence

- The README introduces the decentralized integration model before relying on permission shorthand.
- Architecture terminology explicitly distinguishes recognition, readability, integration mode, discovery, and real local Hub authorization.
- The architecture defines significant teammate information as signed append-only domain records carried through Git snapshots.
- The Manager specification has only two conceptual per-berth modes and labels current mutable tables as projections awaiting implementation work.
- Core validity is defined relative to an accepted anchor, while persistent incompatible Core lineages are named as team forks.
- The two-mode model is explained as a deliberately shallow involvement compromise for medium-sized teams rather than merely technical scalability.
- Recovery prose proves that ordinary device keys never move, prepared recovery authorizes a fresh device key through a visible event, and unprepared tier-two recovery creates a new teammate UUID.
- Cod Sync prose preserves the complete commit DAG while allowing old bulk blobs to dehydrate, and Core snapshots retain the complete signed teammate log.
- Staleness observations are consistently described as evidence and warning rather than unilateral finality or pruning permission.
- Package-level trust and sync notes point to `architecture.md` as the canonical policy source.
- A repository-wide search shows that remaining permission and authorization wording either describes an enforced local boundary or sits outside the intentionally conservative scope and is recorded for follow-up.

### Integrity evidence

- No runtime code, schema identifier, API contract, or serialized value changes on this documentation-first branch.
- Links and Markdown structure remain valid.
- Edited prose follows the repository's semantic-line-break rule.
- A focused diff review checks for accidental claims that encryption can prevent an admitted teammate from sharing plaintext or receiver state.
- A focused diff review checks that the Hub remains the sole Small Sea internet gateway and that Manager database exclusivity is not weakened.
- A focused contradiction audit checks old Wrasse notes for claims that device keys are copied, recovery is impossible even when prepared, or arbitrary teammate signatures unilaterally constitute admission.
- A focused retention audit checks that chain compaction never implies rebasing or discarding the commit DAG and that current Core trust does not depend on historical checkout blobs.

### Validation commands

- `git diff --check`
- Repository-wide `rg` audits for permission and authorization terminology.
- Any available local Markdown/link checker that does not require internet access.
- Manual inspection of every changed paragraph in context.

## Non-goals

- Renaming `berth_role`, its `read-only` / `read-write` serialized values, or public API fields.
- Implementing merge requests from issue #162.
- Implementing the signed append-only teammate-event schema.
- Rewriting teammate admission onto a generalized proposal engine.
- Removing the words permission, authorization, or access when they accurately describe a locally enforced boundary.

## Outcome

The third draft distinguishes recognition, readability, integration mode, replication/discovery, and local Hub authorization without turning each distinction into a teammate category.
It defines automatic and proposal-only as the only conceptual per-berth modes.
It explains those modes as a deliberately shallow inner/outer involvement compromise for medium-sized teams rather than the start of a general governance framework.

It defines significant teammate facts as a complete signed append-only history carried inside every Core database snapshot.
Git retains the complete commit DAG for bookkeeping and merge ancestry; Cod Sync transport compaction and bulk-blob dehydration do not rebase that history or make trust depend on old checkouts.

It also preserves device non-impersonation through both ordinary linking and prepared recovery.
A recovery capability authorizes a fresh device key through a conspicuous signed event, while an unprepared tier-two recovery creates a new teammate UUID and rebuilds connections.
Signed staleness observations are introduced as possible warning and reconvergence evidence, never as unilateral checkpoint or pruning authority.

The Manager, Hub, SSC Files, Hedgerow, Small Sea Live, and older conceptual docs were aligned where their wording implied a central team authority.
Current mutable teammate projections, the all-teammate Hub watcher, recovery mechanics, live-window checkpoints, and staleness-observation schema are recorded as implementation gaps in `FOLLOW-UP.md` rather than described as completed behavior.

Validation found no runtime, schema, API, dependency, or serialized-value changes.
`git diff --check` passes, the changed-file audit contains only Markdown, and the focused terminology audit leaves old permission wording only where it is quoted as a current implementation surface or describes a real local/external enforcement boundary.
Focused contradiction searches found no remaining active-doc claim that recovery is deferred, that another device copies an operational key, that an arbitrary teammate signature unilaterally constitutes admission, or that Git alone is the trust log.
No repository-local Markdown or link checker is configured, so local links and anchors were inspected directly.
