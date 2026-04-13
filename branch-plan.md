# Branch Plan: Separate Peer and Device Team Models

**Branch:** `issue-59-peer-device-model`  
**Base:** `main`  
**Primary issue:** #59 "Make linked devices first-class for sender keys and peer routing"  
**Related issues:** #69, #43, #48, #73  
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`,
`packages/small-sea-hub/spec.md`  
**Related archive plans:** `Archive/branch-plan-issue-59-sender-device-runtime-identity.md`,
`Archive/branch-plan-issue-59-peer-routing-watches.md`,
`Archive/branch-plan-issue-69-linked-device-encrypted-team-bootstrap.md`,
`Archive/branch-plan-issue-43-sender-key-rotation.md`

## Context

The recent runtime branches improved device-scoped crypto and orchestration:

1. sender-key runtime identity is now device-scoped
2. linked devices can bootstrap into encrypted teams
3. sender keys can rotate and redistribute over encrypted pairwise channels
4. runtime reconciliation can now trigger redistribution and some follow-through

But one architectural question is still unresolved and keeps leaking into every
runtime branch:

- the shared team DB still has a member-scoped `peer` table
- runtime logic increasingly needs device-scoped endpoints
- linked devices clearly belong to members, but not all endpoint/routing data
  is member-shaped

That mismatch is now the next real blocker. Without a clean shared model for
member-level and device-level concepts, later work on watches, routing,
notifications, and delivery semantics will keep layering assumptions onto the
wrong table.

## Proposed Goal

After this branch lands:

1. the shared team DB has distinct member-level and device-level models instead
   of forcing `peer` to carry both concepts
2. it is explicit which data belongs to the member-facing `peer` concept and
   which belongs to the device-facing runtime endpoint concept
3. Manager writes both models in the flows that already know device ownership:
   create-team, invitation acceptance, linked-device bootstrap, and member
   removal
4. Hub reads the new shared model cleanly enough that later routing/watch work
   can be implemented without reopening the schema question
5. the branch leaves behind a simpler mental model for future work:
   - members are social/trust/policy actors
   - devices are cryptographic/runtime endpoints

## Why This Slice

This branch is mostly about shared data modeling, not new runtime behavior.

That makes it the right next step because:

- the device/runtime branches now have enough experience to know what data they
  actually need
- the current member-scoped `peer` table is becoming a semantic bottleneck
- later routing/watch branches will be easier if this model is settled first

## Scope Decisions

### S1. Keep both peer and device concepts

This branch starts from the explicit decision that the team DB should maintain
both:

- a member-level `peer` model
- a device-level model linked to members

This is not duplication for its own sake. It reflects that members and devices
are different kinds of things in Small Sea.

### S2. Device ownership must stay explicit

The branch should preserve the existing architectural property that linked
devices are never floating anonymous endpoints. A device row must always make
clear which team member it belongs to.

### S3. Trust still comes from cert history, not from endpoint rows

The new device-level shared model should not become an independent trust source.

Trust remains grounded in:

- `membership` certs
- `device_link` certs
- local adoption of the shared team DB

The new shared device model is for endpoint/routing/runtime metadata, not for
replacing trust resolution.

### S4. Manager remains the only writer of team DBs

The new shared model must continue to respect the repo’s existing rule:

- Manager writes team DBs
- Hub reads them

The Hub should not start writing shared device rows directly.

### S5. Avoid over-solving routing in this branch

This branch should settle the schema/model and the core write/read seams, but
it does not need to finish all watch/routing behavior in the same branch.

The point is to remove ambiguity, not to cram every downstream feature into the
same implementation slice.

## In Scope

### 1. Define the member-level `peer` role precisely

The branch should make `peer` explicitly about member-facing presence and team
relationship data.

Likely examples:

- `member_id`
- human-facing display name
- member-scoped cloud/presence metadata, if any remains truly member-scoped

The branch should also remove any accidental implication that `peer` alone is
the runtime endpoint model.

### 2. Add a device-level shared model

Introduce a device-oriented shared table or tables in the team DB.

The model should make room for data such as:

- owning `member_id`
- `device_key_id` and/or team-device public key
- device-facing endpoint/routing metadata if that belongs in shared state
- status fields that matter for runtime endpoint discovery

The exact table name can be decided during implementation, but it should be
clearly device-scoped and linked back to the member row.

### 3. Write the new model in existing Manager flows

Update the Manager/provisioning flows that already know device ownership:

- `create_team(...)`
- invitation acceptance / completion
- linked-device bootstrap
- member removal / cleanup

These flows should populate and maintain the new shared device model honestly.

### 4. Read the new model in Hub/Manager seams

Update the Hub and any relevant Manager helpers so they can read the new shared
device model without guessing.

This does not require the full next-stage routing logic, but it does require
establishing the read seam future branches will use.

### 5. Specs and micro tests

Update the relevant specs and add skeptical micro tests proving the new split is
real and coherent.

Minimum expected coverage:

- create-team creates both the founding member-facing peer data and the
  founding device-facing row(s)
- invitation acceptance creates a new member plus its initial device row
- linked-device bootstrap adds a second device row for the same member without
  mutating that member into a second peer
- member removal removes the removed member’s associated device rows
- Hub/Manager read paths can distinguish two devices of one member from one
  device each of two members

## Out Of Scope

- redesigning sender-key crypto
- periodic rotation policy
- revocation-cert semantics
- full runtime watch/routing rollout
- NoteToSelf sync and team discovery
- UI/UX work beyond what is needed to keep tests/specs coherent

## Concrete Change Areas

### 1. Team DB schema

- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`
- any team DB migration / compatibility seam already used in Manager

### 2. Manager provisioning logic

- device creation / linkage paths
- invitation acceptance paths
- member removal cleanup paths
- helper functions for shared device enumeration

### 3. Hub read paths

- peer listing / peer lookup seams
- future runtime endpoint enumeration seam

### 4. Specs

- `packages/small-sea-manager/spec.md`
- `packages/small-sea-hub/spec.md`

### 5. Tests

- Manager micro tests for schema/write behavior
- Hub micro tests for read/model behavior

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- the shared team DB now has a clear member/device split instead of one blurry
  `peer` concept doing too much
- linked devices are explicitly attached to members in shared state
- trust is still derived from cert history rather than implicitly from the new
  device rows
- create-team, invitation acceptance, linked-device bootstrap, and member
  removal all keep the two models in sync
- the branch makes future runtime/routing work easier instead of more magical
- tests prove the branch eliminated the conceptual ambiguity that was driving
  repeated planning debates

## Open Questions

### Q1. Which fields truly belong in `peer` versus device?

This branch should settle the rule, not just a one-off column shuffle.

Useful decision test:

- if the field is about the team member as a person/policy actor, it likely
  belongs with `peer`
- if the field is about a concrete cryptographic/runtime endpoint, it likely
  belongs with the device model

### Q2. How much endpoint metadata belongs in shared device rows?

Two plausible shapes:

- the device model carries the endpoint metadata needed for future routing
- the device model only identifies devices, while some endpoint data remains in
  `peer` or another related shared table

This branch should decide the cleanest durable shape rather than leaving it
implicit.

### Q3. How should older team DBs be handled?

The branch should explicitly choose whether the new shared device model is:

- created lazily on first access for existing team DBs, or
- introduced through a stricter shared-schema migration step

The implementation should be honest and testable either way.
