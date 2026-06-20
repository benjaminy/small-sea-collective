# Permission Vocabulary Plan

## Goal

Make Small Sea's decentralized collaboration model harder to mistake for a centrally enforced SaaS permission system.
Use replication and integration language where teammate berth roles describe local sync policy and protocol expectations.
Retain authorization and access-control language where a local component actually enforces a boundary, especially Hub client sessions, credential access, and cryptographic readability.

This branch is intentionally conservative.
It will improve the conceptual framing and the most misleading prose without renaming database columns, API fields, exceptions, or every established role in one sweep.

## Working distinctions

- **Readability** describes who receives the cryptographic material needed to interpret future berth updates.
- **Replication** describes which published histories a participant watches or fetches.
- **Integration** describes which fetched changes a participant merges into their local clone.
- **Authorization** remains appropriate for locally enforced client-to-Hub and component boundaries.
- **Roles** remain convenient shorthands, but they summarize local integration policy and protocol expectations rather than grants from a central authority.

## Survey

1. Inventory permission, authorization, access-control, role, pull, watch, and merge language across top-level and package documentation.
2. Classify each important use as locally enforced, cryptographic, provider-enforced, or decentralized teammate policy.
3. Identify the smallest set of high-leverage documents whose framing influences the rest of the repository.
4. Record follow-up work rather than mechanically renaming schema and code identifiers on this branch.

## Implementation

1. Add an early, explicit explanation that a team has no central authorization service and each clone independently decides what to replicate and integrate.
2. Replace the top-level teammate-permission explanation with readability, replication, and integration terminology.
3. Update the Manager specification's role framing and nearby operations where permission language implies central enforcement.
4. Update other core specifications only where the old analogy is prominent or actively misleading.
5. Preserve semantic line breaks in all edited prose.

## Validation

The branch should convince a skeptical reviewer through multiple independent checks.

### Goal evidence

- The README introduces the decentralized integration model before relying on permission shorthand.
- Architecture terminology explicitly distinguishes team integration policy from real local Hub authorization.
- The Manager role table and explanatory prose describe observable sync behavior: key distribution, watching or fetching, and merging.
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
- Redesigning teammate admission.
- Removing the words permission, authorization, or access when they accurately describe a locally enforced boundary.

## Outcome

The documentation now distinguishes local Hub authorization from decentralized teammate policy near the beginning of both the README and architecture document.
The berth-role explanation uses readability, replication, and integration as separate concepts while retaining current schema values.
The Manager, Hub, SSC Files, Hedgerow, Small Sea Live, and older conceptual docs were aligned where their wording implied a central team authority.

The survey also found that the current Hub watcher observes signals from every teammate without filtering by berth role.
The docs identify role-aware ordinary replication as an intended model rather than a completed runtime guarantee, and `FOLLOW-UP.md` records the implementation seam with issue #162.

Validation found no runtime, schema, API, dependency, or serialized-value changes.
`git diff --check` passes, and the focused terminology audit leaves authorization language only where it describes a local or external enforcement boundary within the reviewed scope.
