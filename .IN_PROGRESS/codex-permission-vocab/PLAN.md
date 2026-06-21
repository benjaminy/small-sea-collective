# Permission Vocabulary Plan

## Goal

Make Small Sea's decentralized collaboration model harder to mistake for a centrally enforced SaaS permission system.
Reduce teammate integration policy to two per-berth modes: `automatic` and `proposal-only`.
Record significant teammate facts as signed append-only domain history, with mutable tables treated as rebuildable projections.
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
7. Preserve semantic line breaks in all edited prose.

## Validation

The branch should convince a skeptical reviewer through multiple independent checks.

### Goal evidence

- The README introduces the decentralized integration model before relying on permission shorthand.
- Architecture terminology explicitly distinguishes recognition, readability, integration mode, discovery, and real local Hub authorization.
- The architecture defines significant teammate information as signed append-only domain records carried through Git snapshots.
- The Manager specification has only two conceptual per-berth modes and labels current mutable tables as projections awaiting implementation work.
- Core validity is defined relative to an accepted anchor, while persistent incompatible Core lineages are named as team forks.
- A repository-wide search shows that remaining permission and authorization wording either describes an enforced local boundary or sits outside the intentionally conservative scope and is recorded for follow-up.

### Integrity evidence

- No runtime code, schema identifier, API contract, or serialized value changes on this documentation-first branch.
- Links and Markdown structure remain valid.
- Edited prose follows the repository's semantic-line-break rule.
- A focused diff review checks for accidental claims that encryption can prevent an admitted teammate from sharing plaintext or receiver state.
- A focused diff review checks that the Hub remains the sole Small Sea internet gateway and that Manager database exclusivity is not weakened.

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

The second draft distinguishes recognition, readability, integration mode, replication/discovery, and local Hub authorization without turning each distinction into a teammate category.
It defines automatic and proposal-only as the only conceptual per-berth modes.
It also defines significant teammate facts as signed append-only Core history and explains that Git carries and merges those records without replacing their domain signatures.

The Manager, Hub, SSC Files, Hedgerow, Small Sea Live, and older conceptual docs were aligned where their wording implied a central team authority.
Current mutable teammate projections and the all-teammate Hub watcher are recorded as implementation gaps in `FOLLOW-UP.md` rather than described as completed behavior.

Validation found no runtime, schema, API, dependency, or serialized-value changes.
`git diff --check` passes, the changed-file audit contains only Markdown, and the focused terminology audit leaves old permission wording only where it is quoted as a current implementation surface or describes a real local/external enforcement boundary.
