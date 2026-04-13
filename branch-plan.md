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
- a device-level `team_device` model linked to members

This is not duplication for its own sake. It reflects that members and devices
are different kinds of things in Small Sea.

Concrete split for the current shared schema:

- `peer.member_id` and `peer.display_name` stay member-level
- `peer.protocol`, `peer.url`, and `peer.bucket` move to `team_device`
- `member.device_public_key` moves to `team_device` and is removed from
  `member`

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

### S5. Manager owns migration/adoption of the new shared schema

This branch chooses explicit Manager-run migration/adoption behavior.

That means:

- Manager migrates or backfills older team DBs when adopting them
- Hub read paths do not lazily create shared schema
- the migration path is explicit and micro-tested

### S6. Avoid over-solving routing in this branch

This branch should settle the schema/model and the core write/read seams, but
it does not need to finish all watch/routing behavior in the same branch.

The point is to remove ambiguity, not to cram every downstream feature into the
same implementation slice.

## In Scope

### 1. Define the member-level `peer` role precisely

The branch should make `peer` explicitly about member-facing presence and team
relationship data.

- `member_id`
- `display_name`

The branch should also remove any accidental implication that `peer` alone is
the runtime endpoint model.

### 2. Add a device-level shared model

Introduce a device-oriented shared table in the team DB: `team_device`.

The model should make room for data such as:

- owning `member_id`
- `device_key_id`
- `public_key`
- device-facing endpoint/routing metadata: `protocol`, `url`, `bucket`
- status fields that matter for runtime endpoint discovery

`device_prekey_bundle.device_key_id` should conceptually attach to
`team_device.device_key_id`. The branch may or may not add a literal FK, but it
should treat `team_device` as the shared ownership home for team-device
identity.

### 3. Write the new model in existing Manager flows

Update the Manager/provisioning flows that already know device ownership:

- `create_team(...)`
- invitation acceptance / completion
- linked-device bootstrap
- member removal / cleanup

These flows should populate and maintain the new shared device model honestly.
Member removal should delete the removed member's `team_device` rows as shared
endpoint metadata while the existing membership / device-link removal flow
independently removes the cert-backed trust path.

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

Migration/shape micro tests should also prove:

- `peer` no longer contains `protocol`, `url`, or `bucket`
- `member` no longer contains `device_public_key`
- `team_device` contains exactly one row per `(member_id, device_key_id)` pair
  after create-team, invitation acceptance, and linked-device bootstrap
- one member with two devices produces one `peer` row and two `team_device`
  rows

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

Specific schema target:

- `peer(member_id, display_name, ...)` becomes member-facing only
- new `team_device` table holds:
  - `member_id`
  - `device_key_id`
  - `public_key`
  - `protocol`
  - `url`
  - `bucket`
- `member.device_public_key` is removed
- Manager-owned migration/adoption updates older team DBs to this shape

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

- `peer` no longer contains `protocol`, `url`, or `bucket`
- `member` no longer contains `device_public_key`
- `team_device` exists and has exactly one row per `(member_id, device_key_id)`
  pair after create-team, invitation acceptance, and linked-device bootstrap
- one member with two devices produces one `peer` row and two `team_device`
  rows
- create-team creates exactly one founding `peer` row and one founding
  `team_device` row for the founding device
- invitation acceptance creates exactly one new `peer` row and one new
  `team_device` row for that admitted member's first device
- linked-device bootstrap adds a second `team_device` row for the same member
  without creating a second `peer` row
- linked devices are explicitly attached to members in shared state
- trust is still derived from cert history rather than implicitly from
  `team_device` rows
- member removal deletes associated `team_device` rows while the existing
  membership / device-link trust-removal flow independently removes the trust
  path; endpoint-row deletion is not itself the trust mechanism
- Hub read paths that need endpoint data no longer read it from `peer`
- the existing member-keyed Hub peer-cloud-file lookup resolves endpoint data
  through `team_device`, not `peer`
- older adopted team DBs are migrated/backfilled by Manager before Hub read
  paths rely on `team_device`
- the branch makes future runtime/routing work easier instead of more magical

## Open Questions

### Q1. Interim deterministic rule for member-keyed Hub peer endpoints

Current Hub peer-proxy paths are still member-keyed. After endpoint metadata
moves to `team_device`, the branch should keep those member-keyed APIs working
by documenting a temporary rule:

- when a member has multiple device rows, member-keyed Hub peer reads choose one
  readable endpoint by a deterministic rule

This branch does not need to finish device-keyed caller APIs, but it should not
leave the one-to-many lookup behavior implicit.
