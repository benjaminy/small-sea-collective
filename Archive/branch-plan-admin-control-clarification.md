# Branch Plan: Admin Control Clarification

**Branch:** `admin-control-clarification`  
**Base:** `main`  
**Related docs:** `README.md`, `architecture.md`,
`packages/small-sea-manager/spec.md`,
`packages/wrasse-trust/README-brain-storming.md`

## Context

The repo currently mixes two different stories about governance:

- a more traditional "admins control team membership" story
- a more explicitly decentralized "every participant has their own clone and
  local view" story

The second story is the intended one.

Small Sea is built on per-participant clones, git history, and voluntary
adoption of one another's updates. That means:

- there is no central authority
- there is no globally authoritative team-membership oracle
- different participants' views may diverge
- "permissions" are protocol expectations and local policy, not centrally
  enforced entitlements
- `admin` is just shorthand for "has write permission to the
  `{Team}/SmallSeaCollectiveCore` berth"

This branch exists to make that explicit across the docs before more trust and
membership code lands on top of fuzzy language.

## Goal

After this branch:

1. the architecture docs clearly say that Small Sea is decentralized all the
   way down and that local views can diverge
2. berth permissions are described in operational/protocol terms:
   `read-only` means peers are not expected to provide future readable updates,
   `read-write` means peers are expected to merge updates from that member in
   that berth
3. `admin` is described consistently as "read-write on
   `{Team}/SmallSeaCollectiveCore`", not as a special centralized authority
4. "remove member" is described honestly as a local clone mutation plus push,
   with social convergence and key rotation layered on top
5. the docs explicitly acknowledge that long-lived splits are awkward because
   the team effectively forks

## Non-Goals

- changing code behavior
- designing quorum governance or threshold signatures
- settling the entire future trust model
- making partitions or conflicting local views disappear

## Planned Changes

### 1. Clarify decentralization in the top-level architecture docs

Spell out that Small Sea participants maintain local views of team history and
that disagreement about membership is a social and synchronization problem, not
something the protocol can fully prevent.

### 2. Reframe permissions as protocol expectations

Document the technical meaning of berth permissions:

- read permission means peers should do the key exchange needed for that member
  to read updates
- write permission means peers should merge that member's updates for that
  berth into their own clone

### 3. Reframe admin and membership operations

Document that:

- `admin` means read-write on the Core berth
- inviting, role-setting, and removing members are edits to a local clone of
  the team DB
- those edits matter to others only insofar as others choose to incorporate
  them

### 4. Name the split/fork consequence directly

Describe the practical consequence of conflicting removals or governance views:
the team can fork into awkwardly incompatible futures, and participants cannot
comfortably live in both branches without bespoke translation.

## Validation

This branch improves repo integrity if:

- `README.md`, `architecture.md`, `packages/small-sea-manager/spec.md`, and
  `packages/wrasse-trust/README-brain-storming.md` all tell the same
  decentralization story
- no doc casually implies a central authoritative admin role
- no doc casually implies that team membership has one forced global answer
- the docs still explain what practical coordination conventions Small Sea
  expects peers to follow

## Outcome

Completed as a docs-only governance clarification branch.

Implemented:

- top-level README wording that says Small Sea's decentralization is literal
- architecture wording that defines berth permissions as protocol expectations
  rather than centrally enforced entitlements
- architecture wording that defines `admin` as write access to the Core berth
- Manager spec wording that reframes invitations, role-setting, and removals as
  edits to a local clone that other participants may or may not adopt
- Wrasse Trust wording that names conflicting governance edits as real team
  forks rather than edge cases to hand-wave away

Validation completed:

- checked that `README.md`, `architecture.md`,
  `packages/small-sea-manager/spec.md`, and
  `packages/wrasse-trust/README-brain-storming.md` now tell the same
  decentralization story
- confirmed that no updated doc treats `admin` as a central cryptographic
  authority
- confirmed that the docs now describe membership and permissions as local
  views plus protocol conventions

Not done on this branch:

- code changes
- micro tests, since this branch only changed docs
- a longer-form discussion of how larger organizations should decompose into
  overlapping small teams
