# Branch Plan: Trust-Domain Reframe (Meta-Plan)

**Branch:** `issue-97-trust-domain-reframe`
**Base:** `main`
**Primary issue:** #97 "Evaluate read-access trust domain for linked team devices"
**Kind:** Meta-plan. Output is a decision + an inventory of follow-up branches, not code.
**Related issues (inputs):** #69, #59, #43, #48, #6, #4, #44, #73
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`, `packages/cuttlefish/README.md`, `Documentation/open-architecture-questions.md`
**Related code of interest:** `packages/small-sea-manager/small_sea_manager/provisioning.py` (linked-device bootstrap, rotation, redistribution, invitation flow), `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`, team DB schema in `sql/core_other_team.sql`

## Purpose

Issue #97 surfaced an architectural question that cuts across linked-device bootstrap (#69), sender-key redistribution (#43), and peer/device runtime (#59): the protocol has been describing a read-access confidentiality boundary that the endpoint model cannot actually enforce. This branch exists to decide whether to reframe the protocol to match reality, and if so, to inventory the follow-up work.

This branch does **not** implement any of the follow-up work. It does not touch GitHub issues. The GitHub issue deltas proposed below are executed in the *implementation phase* of this branch, once the decision is accepted. Until then, this branch is decision + inventory only.

## Revisions After Review

### First revision

- Objection is no longer a distinct primitive. It is the same rotation-with-exclusion machinery applied either at admission-time or later.
- A new design concept is introduced: **admin-quorum admission** for new teammates. `quorum = 1` is the default. Stricter teams can set `X > 1`.
- A dedicated invitation-flow rework chunk (B5) is added.
- Write-objection as an independent axis is dropped from the top-level decision. Local-override policies on top of cert checks are deferred.
- An explicit ordering constraint is added: visibility/approval infrastructure lands before branches that widen admission-by-handoff.

### Second revision (this pass)

The first-revision quorum design had three foundation-level holes. The committee flagged them; this revision fixes all three at the top-level decision layer, because they determine the shape of primitives later branches depend on.

- **Approvals bind to a full immutable admission transcript, not a placeholder.** The transcript includes the invitee's concrete keys, device info, cloud endpoint, proposal metadata, and the invitee's own signature over that bundle. Admin approvals sign over that transcript (or its digest). This closes the TOCTOU hole where admins could sign an intent to admit "Carol" and have any cryptographic material later bind to it.
- **Finalization is published by an already-trusted approver, never by the invitee.** The approver whose signature closes the quorum is the publisher of the team-DB admission mutation. Under `quorum = 1` that is the inviter. This avoids the circularity of letting a not-yet-trusted party write the mutation that makes them trusted.
- **Pending proposals are invalidated by governance-state changes and expire after a per-team time window.** They are not durable bearer capabilities. A proposal with outstanding admin approvals stops being valid the moment the frozen admin set is disturbed.

The three-step sketch that motivated this revision — inviter initiates; invitee generates a keypair and signs a transcript; a quorum of admins signs over what the invitee signed — is the shape the plan commits to. Under `quorum = 1` it degenerates cleanly to Alice-initiates, Bob-responds, Alice-approves-and-publishes, which is close in feel to today's informal flow.

## The Decision To Accept Or Reject

Adopt the following model as the honest foundation for Small Sea's read/write access design:

1. **Read access is effectively endpoint-trust-scoped.** Any currently-admitted party (teammate or sibling device) can, in principle, proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this and should stop pretending to.
2. **Linked sibling device admission is a unilateral identity-owner act.** The existing sibling hands off whatever the new device needs (current team state, copies of peer sender keys the sibling holds, the joining device's own publication material). The sibling issues a `device_link` cert signed over the new device's concrete public keys, and the sibling (already trusted) publishes that cert into the team DB. Other teammates observe the new device via the published cert and may object post-hoc by exclusion (see point 5). Linked-device admission therefore satisfies the transcript-binding and trusted-publisher rules automatically via the existing cert-issuance model.
3. **New-teammate admission is a proposal → signed-transcript → approvals → publish flow with per-team admin quorum, with the following non-negotiable crypto properties:**
   - **Transcript binding.** Admissions are cryptographically bound to an immutable admission transcript containing: proposal ID and nonce, team ID, inviter member ID, a digest of the frozen admin set, and the invitee's own signed acceptance blob (carrying the invitee's concrete member_id, device bootstrap-encryption key, signing key, cloud endpoint, and any other material finalization needs). Admin approvals sign over the transcript, not over a proposal shell. An approval cryptographically cannot be satisfied by a different cryptographic subject than the one the approver saw.
   - **Approval ordering.** The inviter initiates the proposal (creation only, not yet an approval). The invitee generates keys and produces the signed acceptance blob. Admins — including the inviter, who normally signs an approval after the transcript completes — sign over the completed transcript. The inviter-as-initiator and the inviter-as-approver are two distinct signing acts, even when the same human performs both in rapid succession.
   - **Trusted-approver finalization.** The admin whose approval signature closes the quorum is responsible for publishing the admission mutation into the team DB. Under `quorum = 1` that is the inviter. The invitee never publishes their own admission; they may, after finalization is observed, use the standard `redistribute_sender_key(...)` primitive to publish their own sender key.
   - **Quorum policy.** `quorum = 1` is the default. The end-to-end user experience at this default is Alice-initiates → Bob-returns-signed-transcript OOB → Alice-signs-approval-and-publishes. `quorum > 1` is available for stricter teams; the invitee's admission is not finalized until `X` distinct valid approvals over the transcript exist.
   - **Non-durable proposals.** Pending proposals are invalidated by governance-state changes affecting the frozen admin set (admin additions, removals, role rotations) and by a per-team expiry window. Invalidation aborts the proposal: no subsequent approval can finalize it, and a new proposal must be re-initiated if the team still wants to admit the invitee. Outstanding proposals are explicitly not durable bearer capabilities.
   - **Frozen admin set at initiation.** The eligible approver set is snapshotted at proposal creation. Approvals are only counted from admins in that frozen set and only while the proposal is valid. This prevents the "promote a friend to admin mid-proposal" race without reintroducing bearer-capability concerns, which are handled separately by the invalidation rule above.
   - **Pre-admission objection is non-approval, not rotation.** Under quorum, an admin who does not want the invitee admitted simply withholds their approval. No rotation is required because admission never finalizes.
4. **Rotation has exactly two purposes:** (a) **exclusion** — removal, or post-admission objection, both handled by the same rotate-with-exclusion primitive; and (b) **hygiene** — routine, semantically neutral. Rotation is never used to *admit* a party.
5. **Write acceptance is cert-chain validated.** Peers accept a device's signed bundles only when that device's `device_link` / membership cert chains back to something they already trust. Additional local-override policies on top of cert checks are out of scope for this decision and deferred to a separate design question.

The proxy-anyway attack remains possible under this model. What changes is that it is no longer passive protocol fiction — it requires the admitter to actively violate an explicit decision (non-approval, rotation, or removal) by peers, and the excluded party cannot respond coherently to post-rotation traffic without continued live collusion.

## Why This Frame

The first-order argument is that the current "Bob must redistribute his sender key to B before B can honestly read Bob's traffic" rule cannot survive contact with endpoint trust. If Alice already has Device A with read access and she links Device B, nothing in the protocol prevents A from copying plaintext or receiver state to B. The ceremony describing B as "not yet a legitimate reader" is therefore performative, not a real confidentiality boundary. The same reasoning applies symmetrically to add-teammate: if Alice invites Carol and Alice is willing to proxy, Bob's refusal to redistribute his sender key does not prevent Carol from reading Bob's current traffic.

The sharper, second-order argument — the one that motivates admin-quorum admission — is that the *real* unresolved problem is not "can the protocol stop Alice from proxying to Carol?" (it cannot) but "how does the framework reduce ugly admit/remove churn when Alice and Bob disagree about Carol?" Without admin-quorum, disagreement surfaces as admit-then-object cycles: Alice admits Carol unilaterally, Bob rotates-excluding Carol immediately, and the team burns through repeated admission/removal/rotation cycles. Moving the disagreement earlier — to a proposal/approval stage before any admission actually happens — is what a team-governance primitive should be buying, and it is what the reframe gains by separating "local handoff" from "team-recognized admission."

The third-order argument, new in this revision, is that governance primitives must be crypto-precise to avoid the kind of subtle holes that become foundation-level regrets later. Transcript binding, trusted-approver finalization, and non-durable proposals are not optional polish on top of quorum — they are the shape that makes quorum an honest primitive rather than a veneer.

## Strongest Counter-Arguments Considered

**"Formal confidentiality analysis is cleaner when the property is 'only explicitly-admitted readers can decrypt.'"** Embracing endpoint reality makes the formal property softer. The branch rejects this: Small Sea is pre-alpha, user-facing, and targeted at real collaboration rather than formal-verification deliverables; the softer property is the true one, and writing the false-but-cleaner one down does not make it enforceable. If a later formal workstream needs a tighter model, it can describe the rotation-on-exclusion and transcript-binding guarantees precisely.

**"Admin-quorum adds complexity for users who do not want it."** The default `quorum = 1` is specifically designed to rebut this. Small, informal teams pay no extra friction; the crypto plumbing for transcript binding and trusted-approver publishing runs under the hood in both modes.

**"Invalidating proposals on any governance-state change is over-conservative and will cause proposal re-initiation churn."** Real, but the alternative is a bearer-capability failure mode that is much harder to retrofit out later. Invalidation is the conservative default; a more permissive policy can be considered as a future B5-or-later refinement if concrete usage patterns justify it. The reverse migration — tightening a too-permissive rule after proposals have been normalized as durable — is cryptographically ugly.

## What This Simplifies

- **#69 (linked-device bootstrap) collapses.** The joining device B is bootstrapped from a sibling device A by receiving (i) the team's current state, (ii) A's copies of the peer sender keys A already holds, and (iii) material for B to publish its own device key / sender key. No round-trip to every other team member is required for B to become a legitimate reader. Join-time-forward becomes the honest and natural historical-access policy.
- **Payload 3 in the current #69 flow changes role.** It stops being "B reporting back so the authorizing sibling can tell peers to admit B." It becomes B's own sender-key publication via the standard `redistribute_sender_key(...)` primitive.
- **`test_linked_device_bootstrap_requires_real_redistribution_for_other_senders` becomes wrong in spirit.** Under the new model, B *should* be able to read future traffic from Bob using the peer sender key A already has. That test is retired and replaced with tests that assert what is actually protocol-enforceable.
- **Invitation flow gets crypto-tightened but user-feel-preserved at default quorum.** Under `quorum = 1`, the end-to-end UX is still Alice-invites / Bob-responds / Alice-finalizes. Under the hood, Bob's response becomes a signed acceptance transcript over his concrete keys, and Alice's finalization becomes a signed approval over that transcript followed by Alice publishing the admission mutation. Invitee-authored team-DB writes (which today happen during `accept_invitation`) are replaced by inviter-authored writes that bind to the signed transcript.
- **Rotation gains a clean two-purpose mental model.** Exclusion (one primitive; applies whether triggered by removal or by post-admission objection) plus hygiene. The admission case disappears from rotation's vocabulary entirely.

## What Survives Unchanged

- **#43 rotation-on-removal stays correct and load-bearing.** It is the one place where the cryptography really does enforce a confidentiality boundary (against an ex-member after the honest-majority rotates excluding them). Post-admission objection reuses the same primitive.
- **The `redistribute_sender_key(...)` primitive stays as-is.** It carries the load for every exclusion timing (removal, post-admission objection) and for the new-member-publishes-own-sender-key case.
- **`device_prekey_bundle` publication, X3DH, and Double Ratchet wiring stay.** Substrate for any pairwise control-plane operation.
- **Cert-chain validation for write authority stays.** `device_link` and membership certs continue to gate acceptance of signed bundles. This is the rule that makes "trusted-approver finalization" in point 3 not just a policy choice but a crypto necessity — any invitee-authored admission write would fail this check.

## Proposed GitHub Issue Deltas

*(Plans for the implementation phase. No GitHub changes happen until the decision is accepted.)*

### Keep (no change)

- **#43** — already aligned once its justification is reread as "rotation-on-exclusion."
- **#44** — sender-key storage revisit; orthogonal.
- **#48** — Manager multi-device NoteToSelf sync and team discovery; orthogonal.
- **#6** — identity model; the reframe refines an open question but does not resolve the whole issue.
- **#4** — Cuttlefish integration; orthogonal.

### Modify

- **#69** — rescope. Drop the "Bob must redistribute to B" framing. Reframe as: bootstrap B by sibling handoff of everything B needs, with join-time-forward historical access. Existing same-member bootstrap code is largely salvageable; the payload-3-as-admission semantics are retired.
- **#59** — rescope. Device-aware sender keys and peer routing stay; the "when a new linked device appears, every member must redistribute to it" trigger goes away. What #59 still owns: device-scoped send keys, peer-table device dimension, per-device Hub routing, and watch behavior that surfaces new device-link events (for objection visibility, not admission orchestration).
- **#73** (periodic sender-key rotation) — clarify that periodic rotation is "hygiene," never "admission." Doc-only.

### Close As Superseded

Any open issue scoped to "distribute sender keys to newly linked devices from every other sender" should close as superseded. Exact candidate identification happens during implementation via a fresh inventory pass.

### Add

- **New issue — "Admin-quorum admission: transcript-bound proposal/approval/finalize for new teammates."** Scope: per-team quorum setting, frozen admin set, new schema for proposals / signed transcripts / admin approval signatures, invalidation rule on governance-state change and on expiry, trusted-approver-publishes-finalization logic. This is the ticket the invitation-flow rework branch (B5) lives under.
- **New issue — "Replace historical-access test for same-member linked-device bootstrap."** Scope: retire `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders`; add tests that (a) B can read Bob's current traffic post-bootstrap using A's copy of Bob's sender key, and (b) Bob rotating-excluding B cuts B off from Bob's subsequent traffic absent active proxying.
- **New issue — "Admission-event visibility and objection affordance."** Scope: the Manager/Hub watch path that surfaces new `device_link` certs, new invitation proposals (so admins in the frozen set know to approve or ignore), and finalized admissions to the user promptly, with an objection affordance that invokes the rotate-with-exclusion primitive.
- **New issue — "Spec and architecture doc sweep for endpoint-truth language."** Scope: rewrite the read-confidentiality language in spec.md, architecture.md, open-architecture-questions.md, and cuttlefish/README.md. Add explicit framing on rotation's two purposes, on admin-quorum with transcript binding, and on trusted-approver finalization.

## Branch-Sized Chunks Of Follow-Up Work

Each is a sketch. None gets a full branch plan in this branch; each gets its own `branch-plan.md` when started.

### B1. Doc sweep: endpoint-truth rewrite

Touch `architecture.md`, `packages/small-sea-manager/spec.md`, `Documentation/open-architecture-questions.md`, and `packages/cuttlefish/README.md`. Replace read-confidentiality language that asserts a protocol-enforceable boundary with the endpoint-truth framing. Add explicit paragraphs on rotation's two purposes (exclusion, hygiene), on admin-quorum admission with transcript binding, on trusted-approver finalization, on non-durable proposals, and on linked-device admission as a unilateral identity-owner act that satisfies the same crypto rules via cert issuance. No code changes.

**Why first:** all subsequent implementation branches will cite the updated docs as their reference.

### B2. Admission-event visibility and objection affordance

When a new `device_link` cert, a new invitation proposal, or a finalized admission appears in the team DB, every existing peer's Manager surfaces the event promptly with enough context for the user to decide whether to act. Admins in the frozen set of an open proposal see it prominently so they can decide to approve or not. Provide a first-class UI affordance for "object" on finalized admissions (invokes the existing exclusion primitive).

**Why this is load-bearing:** observability-and-objection is the real consent mechanism once admission-time read-confidentiality is acknowledged as fiction. For quorum-mode proposals, visibility is what turns "non-approval" into a meaningful act — admins cannot choose not to approve if they do not see the proposal.

### B3. Linked-device bootstrap: sibling peer-sender-key handoff

Extend `create_linked_device_bootstrap(...)` to include A's current snapshot of peer sender keys in the bundle. Update `finalize_linked_device_bootstrap(...)` to store those as local `peer_sender_key` rows on B. Retire the "B cannot read Bob's future traffic until Bob redistributes" boundary and replace the test.

**Gated on:** B2.

### B4. Payload-3 reframe: B's own sender-key publication via the standard redistribute primitive

Replace "B returns a sender distribution payload to the authorizing sibling who tells peers to admit B" with "B uses `redistribute_sender_key(...)` once bootstrap is complete." No special admission ceremony.

### B5. Invitation-flow rework: transcript-bound admin-quorum admission

Largest follow-up chunk. Scope:

- Per-team `admission_quorum` setting (default 1), plus `proposal_expiry` setting.
- New team-DB schema: proposal rows carrying proposal ID, nonce, inviter ID, team ID, frozen admin set snapshot (signed by inviter), state, created_at, expires_at; signed-acceptance-transcript rows or fields keyed by proposal ID; admin-approval-signature rows keyed by (proposal_id, admin_member_id) carrying signatures over the full transcript digest; finalized-admission mutation (only published by a trusted approver).
- Flow implementation:
  1. Inviter creates proposal (no approval yet). Writes row + publishes token OOB.
  2. Invitee generates member_id, device keys, cloud endpoint; signs an acceptance transcript blob; returns it OOB or uploads it somewhere the inviter can fetch (`GET /cloud_proxy` analog).
  3. Inviter verifies the acceptance blob, assembles the full transcript, signs an approval. If that closes the quorum, inviter publishes the finalization mutation. If not, other admins in the frozen set sign approvals until the quorum is met; whichever admin closes the quorum publishes finalization.
  4. Invitee observes finalization, opens cloud setup, and publishes their own sender key via the standard redistribute primitive.
- Invalidation logic: on any admin-set change, every open proposal referencing the disturbed frozen set is marked invalid. Proposals past `expires_at` also invalid. Invalid proposals cannot be finalized; attempted finalization fails with a clear error.
- Degeneration at `quorum = 1`: Alice initiates → Bob returns signed transcript → Alice signs approval, meets quorum, publishes finalization. Functionally very close to today's `create_invitation` / `accept_invitation` / `complete_invitation_acceptance` sequence, with the invitee's team-DB write responsibility moved to the inviter.
- Edge cases documented in the branch plan: inviter removed after initiation but before closing quorum (proposal invalidated); inviter becomes sole remaining eligible approver at close time (still publishes); invitee submits a different transcript later (treated as a new proposal); concurrent admin approvals racing to be the quorum-closer (deterministic tie-break by signature timestamp or lexical order); admin from frozen set signs approval after governance change (ignored because proposal invalid); invitee attempts to publish their own admission write (rejected by cert-chain check).

**Gated on:** B2.

### B6. Issue hygiene: close superseded, retitle rescoped, add new

GitHub actions from "Proposed GitHub Issue Deltas." One-shot administrative task. Runs after B1 so rescoped issues can point at updated docs.

## Ordering Constraints

- **B1 first.** Docs anchor the shared vocabulary.
- **B2 before B3 and B5.** Any branch that widens admission-by-handoff must not ship before the minimum visibility/objection path exists. This is doubly true under transcript-bound quorum: admins cannot approve or withhold approval on proposals they do not see.
- **B4 follows B3.** B4's reframe of payload-3 depends on the sibling handoff in B3.
- **B6 after B1, ideally after the code branches it depends on are scoped.**

B3, B4, and B5 can otherwise be scheduled in any order once B2 has landed.

## Documentation Changes (Scoped Here, Executed In B1)

- `architecture.md` §"Fully Decentralized Team Management": rewrite the paragraph on key rotation to reflect exclusion + hygiene. Add paragraphs on admin-quorum admission, transcript binding, trusted-approver finalization, and non-durable proposals.
- `packages/small-sea-manager/spec.md` §"Linked-device team bootstrap": rewrite "Historical boundary and visibility" and "Scope of the current slice." Remove the "not yet redistributed by every other sender" framing.
- `packages/small-sea-manager/spec.md` §"Invitations" and §"Invitation Protocol (detailed)": describe the transcript-bound proposal/approval/publish model. Be explicit that the inviter (not the invitee) writes the finalization mutation, and that the invitee's own team-DB writes from today's `accept_invitation` go away.
- `Documentation/open-architecture-questions.md` §5 "Identity Model": add a settled-decisions subsection citing the reframe, admin-quorum, transcript binding, trusted-approver finalization, and the non-durable-proposal rule.
- `packages/cuttlefish/README.md` sender-keys and trust sections: reconcile language. Scope-confirm in B1.

## Validation (For This Meta-Plan Branch)

This branch's deliverable is this document. It is "done" when a skeptical reviewer can read it and confirm:

1. The decision is stated precisely enough that later branches can be measured against it. A reviewer should be able to resolve downstream design ambiguities by pointing at a specific point of "The Decision To Accept Or Reject."
2. The three foundation-level crypto properties for teammate admission — transcript binding, trusted-approver finalization, and proposal non-durability — are named explicitly at the top level, not buried as branch details.
3. Each follow-up chunk has a one-paragraph scope clearly smaller than a re-plan of the whole reframe.
4. GitHub issue deltas are executable: each entry names what specifically changes.
5. Ordering constraints are spelled out.
6. No code changed; no GitHub state changed.

## Out Of Scope For This Branch

- Per-branch plans for B1–B6.
- Code touching `provisioning.py`, tests, or schemas.
- GitHub issue edits.
- Final sequencing of B3, B4, B5 beyond constraints above.
- Write-acceptance local-override policy.
- Quorum-style governance for linked sibling-device admission (committee scoped quorum to teammate admission).
- Deterministic-adoption-from-signed-approval-set as an alternative to trusted-approver-publishes finalization. Noted here as a possible future refinement; this plan commits to trusted-approver-publishes as the foundation, because it is simpler to reason about and matches the existing "Manager owns team-DB writes" architecture.
- Formal-model writeup.

## Skeptic-Facing Wrap-Up

A reviewer accepting this meta-plan should be able to answer:

1. **What decision is being asked for?** Accept or reject the five-point model, including all sub-bullets of point 3.
2. **What changes in the codebase if accepted?** Nothing immediately. B1–B6 are the planned follow-ups.
3. **What existing work becomes obsolete?** The admission-flavored parts of #69's design and its "requires real redistribution" test. The invitation flow's invitee-publishes-own-admission pattern, replaced by inviter-publishes under transcript binding.
4. **What existing work stays correct?** #43's rotation-on-removal primitive (now covering post-admission objection too). `device_link` cert chaining for write acceptance. `redistribute_sender_key(...)` as the substrate for all exclusion timings and for new-member own-key publication.
5. **What is new?** Admin-quorum admission for new teammates, with three non-negotiable properties: approvals bind to a full immutable transcript including the invitee's concrete keys; the approver who closes quorum publishes finalization; proposals are invalidated by governance-state change and by expiry, not durable.
6. **Where are foundation-level holes addressed?** TOCTOU → point 3 "Transcript binding" sub-bullet. Bootstrap paradox → point 3 "Trusted-approver finalization" sub-bullet. Bearer-capability durability → point 3 "Non-durable proposals" sub-bullet.
7. **What is not decided here?** Final sequencing of B3/B4/B5 beyond ordering constraints; exact expiry windows and tie-break details (belong to B5); exact superseded-issue list (requires fresh inventory); local-override policy for write acceptance; deterministic-adoption as an alternative to trusted-approver-publishes; formal-model writeup.
