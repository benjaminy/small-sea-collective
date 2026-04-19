# Branch Plan: Admin-Quorum Admission (B5)

**Branch:** `issue-98-admin-quorum-admission`  
**Base:** `main`  
**Primary issue:** #98 "admin-quorum admission"  
**Kind:** Implementation branch. Code + micro tests.  
**Related issues:** #97 (accepted trust-domain reframe), #99 (admission-event visibility, B2), #100 (spec/doc sweep, B1), #102 (member transport configuration, B7)  
**Related prior plan:** `Archive/branch-plan-issue-97-trust-domain-reframe.md`  
**Related code of interest:** `packages/small-sea-manager/small_sea_manager/provisioning.py`, `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`, `packages/small-sea-manager/small_sea_manager/manager.py`, `packages/small-sea-manager/small_sea_manager/web.py`, `packages/small-sea-manager/small_sea_manager/templates/fragments/invitations.html`, `packages/small-sea-hub/`

## Purpose

Replace the current invitee-publishes-own-admission flow with the inviter-orchestrated, transcript-bound, admin-quorum flow already established in `architecture.md`.

The key correction is not cosmetic. Today the invitee can write themselves into the team DB via `accept_invitation`, which violates the trusted-finalizer rule and leaves the most important write authored by the wrong party. This branch moves admission authority back to the inviter, binds the invitee's concrete device keys into a signed transcript, makes admin approvals cover that transcript, and ensures finalization is published by the inviter.

This branch should fully land the production shape for `quorum = 1` and land the core schema, verification, and micro-testable logic for `quorum > 1` without spending effort on polished multi-admin UX.

For this plan, **governance drift** has a precise meaning: any change relative to the proposal's anchor in the admin roster, membership roster, or member-to-device mapping. Those are the only changes that invalidate a proposal in this branch.

## Why This Plan Needs To Be Strict

This branch changes the trust boundary for teammate admission. A loose implementation would be worse than the current one because it could look more rigorous while still leaving hidden authority leaks.

So this plan optimizes for three things:

1. The branch goals are concrete enough to implement without guesswork.
2. The validation is strong enough to convince a skeptical reviewer that the admission authority really moved.
3. The implementation keeps coupling low by putting the durable rules in manager/provisioning logic and using UI/routes as thin adapters.

## Non-Negotiable Invariants

The implementation is only acceptable if all of these remain true:

1. The invitee never writes their own teammate admission into the team DB.
2. Finalization is always published by the inviter.
3. Every counted admin approval is traceable from anchor state: anchor commit -> governance snapshot -> admin member -> linked device key -> signature over transcript digest.
4. Any governance-state drift relative to the anchor invalidates the proposal before finalization.
5. `quorum = 1` is the default and works end-to-end without requiring extra admin UX.
6. `quorum > 1` uses the same core data model and verification rules, not a parallel code path with different semantics.
7. Post-admission transport configuration stays out of the immutable transcript and continues through the B7 flow.
8. This branch must not weaken the existing rule that only `small-sea-manager` reads or writes the core team DB directly.

## Branch Goals

When this branch is done, the repo should provide all of the following:

1. An inviter can create an admission proposal shell anchored to the current governance snapshot and containing a pre-allocated invitee `member_id`.
2. That shell is visible immediately through the existing B2 admission-events path before the invitation token is delivered to the invitee.
3. The invitee can generate device keys and return a signed acceptance blob that binds those keys to the allocated `member_id`, without writing to team DB.
4. The inviter can verify the acceptance blob, assemble the transcript, sign an approval, and publish finalization when quorum is met.
5. At `quorum = 1`, inviter approval and finalization succeed in the same end-to-end flow.
6. At `quorum > 1`, other admins can add valid approvals that count by distinct `admin_member_id`, not by device.
7. Governance drift or expiry invalidates a proposal and blocks finalization.
8. The old invitee-self-admission path is removed or reduced to a hard failure that cannot produce admission.
9. The admission flow handles the invitee role explicitly, with the plan stating whether that role is stored on the proposal or derived elsewhere at finalization.
10. Newly admitted members are handed off to the B7 transport-announcement flow after admission, not during transcript creation.
11. The branch lands enough micro tests to prove the new trust boundary, not just the happy path.
12. The invitation token for this flow is narrowed to the material the invitee actually needs to sign acceptance, instead of carrying the old self-admission payload.

## In Scope

- New synced team-DB tables for admission proposals and admin approvals
- Team-level settings for `admission_quorum` and proposal expiry
- Proposal shell creation anchored to a verifiable team-history commit
- Governance digest over the frozen admin roster, membership roster, and member-to-device mapping
- Inviter allocation of invitee `member_id`
- Explicit handling of the invitee role carried by the admission flow
- Redesign of the invitation token contents for transcript-bound admission
- Invitee-side acceptance signing with no team-DB write
- Transcript digesting and admin approval signatures over that digest
- Approval validation against anchor-era device linkage and admin membership
- Lazy invalidation on governance drift or expiry
- Inviter-published finalization
- Activation of B2 `proposal_shell` and `awaiting_quorum` runtime states
- Any required Hub watcher-contract work so Manager observers wake up on relevant DB-state changes
- Post-finalization handoff into B7 transport announcement
- Spec/doc updates needed to keep repo docs consistent with shipped behavior
- Retirement of the old invitee-writes-own-admission path

## Out Of Scope

- Polished multi-admin approval UX
- Fallback finalizer behavior if the inviter disappears
- New write-override policy design
- Linked-device admission redesign
- Transport metadata in the immutable admission transcript
- Full revocation-certificate infrastructure
- Background invalidation sweep jobs

## Current State

Today:

1. `create_invitation` creates an `invitation` row and returns a token with inviter-side material.
2. `accept_invitation` runs on the invitee side, generates keys, and writes member/device/certificate rows into the team DB.
3. The inviter later syncs and observes that write.

That is the wrong authority split. The invitee currently authors the mutation that effectively makes them a teammate.

The current invitation token is correspondingly too powerful for the replacement flow because it carries material for the old self-admission path. This branch should narrow the token to what the invitee actually needs in order to clone the team repo if necessary, generate keys, and sign acceptance against the proposal.

B2 already reserved `proposal_shell` and `awaiting_quorum` in the event model, but those names are still placeholders rather than real runtime states.

B7 already introduced the post-admission `needs_transport_announcement` flow, which this branch should reuse rather than embedding transport setup into the admission transcript.

## Critical Design Decisions

### 1. `admission_proposal` is the durable source of truth

Admission should revolve around a proposal row, not a loose sequence of invitation side effects. The row needs enough material to verify whether it is still valid and whether it has enough approvals to finalize.

Expected fields:

- Identity: `proposal_id`, `nonce`, `team_id`, `inviter_member_id`, `invitee_member_id`
- Intended admission outcome: invitee role, unless the implementation deliberately keeps role outside the proposal and documents the reason
- Governance anchor: `anchor_commit`, `governance_digest`
- Lifecycle: `state`, `created_at`, `expires_at`
- Invitee transcript material: signing key, bootstrap key, acceptance signature, transcript digest
- Finalization material: finalization signature and/or finalization marker authored by inviter

The state model should stay small and explicit:

- `awaiting_invitee`
- `awaiting_quorum`
- `finalized`
- `invalidated`
- `expired`

### 2. `admin_approval` rows record votes without conflating device and member identity

Approval counting is member-scoped but signature execution is device-scoped. The schema must preserve both:

- `admin_member_id` for quorum counting
- `approver_device_key_id` for anchor-based validity checks

Quorum is counted over distinct `admin_member_id`s with valid approvals for the current transcript digest.

### 2a. The invitation token should be deliberately narrow

The replacement flow should not keep the old invitation token shape by inertia. The token contents are security-relevant because they determine what authority and configuration material reaches the invitee before admission is finalized.

The plan should treat the new token as carrying only the minimum material needed for the invitee side of the flow, such as:

- proposal identity and anti-confusion material, including `proposal_id` and `nonce`
- enough team-repo/bootstrap coordinates for the invitee to obtain the repo state needed to complete acceptance
- any information required to bind the signed acceptance to the correct team and proposal

It should not continue carrying material that only made sense for invitee-authored self-admission, such as the old endpoint/self-write payload, unless a concrete need is identified and justified.

### 3. Governance anchoring must be independently replayable

At proposal creation time, the inviter records the team repo commit hash and a digest over the admin roster, membership roster, and member-to-device mapping at that commit.

The implementation should prefer helpers that make the derivation inspectable and testable. A reviewer should be able to point at one function that computes the digest and one function that validates it against current state.

### 4. Invalidation is lazy but mandatory

This branch does not need a sweeper. It does need every mutation step to refuse progress if:

- the proposal is expired, or
- the current governance state no longer matches the anchored digest because of governance drift in the admin roster, membership roster, or member-to-device mapping

When that happens, the proposal should transition to `invalidated` or `expired` as part of the failed step so the failure is durable and visible.

### 5. `quorum = 1` should be the canonical first-class flow

The cleanest implementation is:

1. Inviter creates proposal shell.
2. Invitee signs acceptance blob out of band.
3. Inviter records the transcript, signs approval, verifies quorum, and finalizes.

This is not a shortcut flow separate from the real design. It is the real design with quorum satisfied immediately.

### 6. `quorum > 1` should extend the same flow, not fork it

For higher quorum:

1. Inviter creates proposal shell.
2. Invitee signs acceptance blob.
3. Inviter records transcript and own approval, leaving the proposal in `awaiting_quorum`.
4. Other admins add approvals after validating anchor and transcript.
5. Inviter finalizes once quorum is met.

The same proposal row, transcript digest, approval validation, and invalidation logic should serve both quorum modes.

### 7. B2 activation is partly a watcher-contract problem, not just a state-model problem

The B2 lesson should be preserved explicitly here: landing the right DB states is not sufficient if Manager observers are only woken for peer-count changes and not for the berth/DB mutations that carry admission-event visibility.

For B5, activating `proposal_shell` and `awaiting_quorum` means both of these happen:

1. The new proposal/admission states are written correctly into the synced DB and event model.
2. The Hub-to-Manager watcher contract wakes berth waiters when those DB changes arrive, so the existing UI path actually refreshes.

If the first part lands without the second, the branch can look complete in review while still regressing real behavior because the UI never wakes up to show the new state transitions.

## Risks And Failure Modes To Design Against

The first draft mostly described the happy design. This branch plan should explicitly guard against these failure modes:

1. A hidden invitee-side write path still exists and can be used to create effective admission.
2. Approval counting accidentally uses device count instead of admin-member count.
3. Approval verification checks current admin state but not anchor-era device linkage, letting newly linked devices retroactively vote.
4. Governance drift is checked in one path but forgotten in another, especially the finalize step.
5. UI or web routes start owning admission rules that belong in provisioning logic.
6. The old `invitation` table remains half-authoritative for teammate admission, causing the repo to carry two overlapping admission models.
7. B7 transport setup leaks back into the transcript, muddying the immutable trust artifact.
8. B5 lands the new proposal states in DB but the Hub/Manager watcher contract does not wake the existing UI path, so visibility silently regresses.
9. Role handling is left implicit and the final admitted role differs from what the inviter intended.
10. The new invitation token accidentally preserves authority or configuration material from the old self-admission flow that the invitee no longer needs.

The implementation should reduce each of these to a clearly testable rule.

## Expected Change Areas

### Schema

- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`
  - Add `admission_proposal`
  - Add `admin_approval`
  - Add or extend storage for `admission_quorum` and proposal expiry settings
  - Make the intended role storage explicit if role is carried on the proposal/finalization path

### Provisioning / core logic

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
  - Create proposal shell
  - Own the single authoritative proposal-validity helper used by every mutation entry point
  - Define and parse the narrowed invitation token shape for transcript-bound admission
  - Sign invitee acceptance transcript without DB writes
  - Record transcript
  - Validate approvals against anchor state
  - Finalize admission
  - Invalidate or expire proposals on attempted use
  - Retire the old `accept_invitation` self-admission behavior
  - Carry the invitee role through proposal creation and finalization, or deliberately document why that role is determined elsewhere

### Manager session / business layer

- `packages/small-sea-manager/small_sea_manager/manager.py`
  - Expose the new provisioning flow through manager methods
  - Keep policy in provisioning/helpers, not in thin manager wrappers

### Web UI / routes

- `packages/small-sea-manager/small_sea_manager/web.py`
  - Minimal routes for proposal creation, transcript recording/finalization, and admin approval

- `packages/small-sea-manager/small_sea_manager/templates/fragments/invitations.html`
  - Render real `proposal_shell` and `awaiting_quorum` states through the existing event model
  - Surface B7 transport-next-step handoff after finalized admission

### Hub watcher contract

- `packages/small-sea-hub/`
  - Ensure berth waiters/observers are pulsed on the DB-state changes that make proposal-shell and awaiting-quorum visibility real in Manager
  - Keep this within the existing watcher contract rather than introducing a parallel notification path

### Micro tests

- `packages/small-sea-manager/tests/`
  - Happy path at `quorum = 1`
  - Multi-admin quorum path
  - Invalidation and expiry
  - Approval validity edge cases
  - Old path retirement

## Implementation Phases

### Phase 1: Establish the data model and verification helpers

Implement the schema and the smallest set of helpers that let the rest of the branch reuse one source of truth:

1. Proposal-row creation
2. Governance-digest computation
3. Anchor verification
4. Proposal validity checks
5. Approval-counting rules
6. One authoritative provisioning helper for "proposal is still valid" reused by every mutation entry point
7. Invitation-token shape narrowed to the material needed for repo access and transcript signing

Exit criteria:

- The proposal and approval schema are in place.
- Governance digest derivation is isolated enough to micro test directly.
- There is one obvious helper path for validity checks rather than several ad hoc checks.

### Phase 2: Land the invitee-signs / inviter-finalizes `quorum = 1` flow

Implement the real end-to-end default flow:

1. Inviter creates proposal shell.
2. Invitee signs acceptance blob without DB writes.
3. Inviter records transcript, signs approval, and finalizes.

Exit criteria:

- The invitee performs no team-DB write.
- The inviter publishes the finalization mutation.
- A full micro test proves the trust boundary moved.

### Phase 3: Extend to `quorum > 1` without changing the trust model

Add:

1. Transcript recording without immediate finalization
2. Other-admin approval path
3. Quorum observation and finalization path

Exit criteria:

- The same transcript digest and approval-validation rules work in both quorum modes.
- Distinct-admin counting is micro tested.
- Invalid approvals are rejected for the right reason.

### Phase 4: Wire runtime states and remove the old admission authority leak

Add:

1. Real B2 event activation for proposal shell and awaiting quorum
2. Any Hub watcher-contract changes required so those states become visible without manual refresh or unrelated wakeups
3. B7 post-finalization transport handoff
4. Removal or hard-disablement of invitee self-admission
5. Doc/spec updates needed to match the shipped behavior

Exit criteria:

- The old path cannot produce teammate admission.
- The event model reflects real runtime transitions.
- The watcher path wakes the UI on the relevant DB changes.
- Post-admission transport setup is still a separate flow.

## Validation

The branch is done only when a skeptical reviewer can verify both correctness and repo integrity from evidence in code, micro tests, and the resulting flow.

### A. Admission authority really moved

1. A micro test proves the invitee can sign acceptance and complete no team-DB write before finalization.
2. A micro test proves the inviter publishes the finalizing mutation.
3. The old self-admission path is removed or fails in a way that cannot produce admission.
4. Code inspection shows no alternate write path outside `small-sea-manager` that can create teammate admission state.

### B. The transcript and approval model is real, not performative

5. A micro test proves the transcript binds the allocated `member_id` and concrete invitee keys.
6. A micro test proves approval signatures are checked against the transcript digest, not merely proposal existence.
7. A micro test proves approvals from two devices of the same admin count as one vote.
8. A micro test proves a device linked after the anchor cannot cast a valid approval for that proposal.
9. A micro test proves a non-admin device cannot cast a valid approval.

### C. Invalidation is enforced everywhere it matters

10. A micro test proves governance drift after proposal creation invalidates the proposal on the next attempted step.
11. A micro test proves expiry blocks progress and marks the proposal expired.
12. A micro test proves invalidated or expired proposals cannot be finalized, even if enough approval rows exist.
13. Code inspection confirms all mutation entry points run the same validity check helper before progress.

### D. Runtime behavior matches the architecture and related branches

14. Creating a proposal produces the B2 `proposal_shell` state in the existing event/UI path.
15. Recording transcript without enough approvals produces the B2 `awaiting_quorum` state when `quorum > 1`; at `quorum = 1`, that state is skipped because inviter approval immediately satisfies quorum.
16. The relevant Hub/Manager watcher path wakes on those DB-state changes, so visibility is live behavior rather than stale-until-refresh behavior.
17. Finalized admission transitions the new member into the B7 `needs_transport_announcement` handoff rather than embedding transport metadata in the transcript.

### E. Repo integrity is maintained or improved

18. Admission rules live in reusable provisioning/helpers rather than being duplicated across manager/web/UI layers.
19. The branch does not leave the old `invitation` table authoritative for teammate admission alongside the new proposal model.
20. Naming, role handling, and state transitions align with `architecture.md` and existing B2/B7 language.
21. Micro tests stay local-only and do not introduce network dependence.
22. Any deleted or rewritten prior tests are updated with rationale that clarifies the trust-boundary change rather than silently dropping coverage.

## Micro Tests To Land

The exact filenames can change, but the branch should land evidence for at least these cases:

1. `quorum = 1` happy path: proposal creation -> invitee acceptance signing with no DB write -> inviter record-and-finalize.
2. `quorum = 2` happy path: proposal creation -> transcript recorded -> two distinct admin approvals -> inviter finalization.
3. `quorum = 2` with only one distinct admin approval: finalization blocked.
4. Two devices for one admin still count as one approval.
5. Approval from a non-admin device is rejected.
6. Approval from a device linked after the anchor is rejected.
7. Governance change after anchor invalidates the proposal.
8. Expired proposal cannot progress.
9. Attempted invitee self-admission no longer works.
10. Governance digest derivation matches an independently recomputed snapshot in test.
11. Visibility-path test or equivalent evidence proves proposal-shell and awaiting-quorum state changes wake the Manager observer path.
12. The admitted role matches the role carried by the flow.
13. Invitation-token test or equivalent evidence proves the new token contains only the material needed for transcript-bound admission and not the old self-admission payload.

## Open Questions To Resolve During Implementation

These do not block the branch plan, but they should be answered explicitly in the implementation notes or final plan update:

1. What is the cleanest storage location for `admission_quorum` and proposal expiry so that the settings model does not become more fragmented?
2. What exact API/form shape is minimal but sufficient for other-admin approval at `quorum > 1`?

## Wrap-Up Notes

When the branch is complete:

1. Update this plan to record what actually landed and any deliberate deviations.
2. Archive it as `Archive/branch-plan-issue-98-admin-quorum-admission.md`.
3. Call out any follow-on issue needed for multi-admin approval UX or cleanup around the old invitation model.
4. Preserve the strongest validation evidence so a reviewer can retrace why this branch is safe.
