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

- Objection collapsed into exclusion; rotation-with-exclusion is one primitive applied either at admission-time or later.
- Admin-quorum admission for new teammates. `quorum = 1` default; stricter teams can set `X > 1`.
- Invitation-flow rework added as a dedicated chunk (B5).
- Write-objection as an independent axis dropped; local-override policies deferred.
- Ordering constraint added: visibility lands before admission-by-handoff widens.

### Second revision

Three foundation-level holes closed:

- **Transcript binding.** Approvals sign over an immutable transcript including the invitee's concrete keys and device info, not a placeholder.
- **Trusted-approver finalization.** The invitee never publishes their own admission.
- **Non-durable proposals.** Pending proposals invalidate on governance-state change and on expiry.

### Third revision (this pass)

Three further foundation-level fixes addressing distributed-validity and ownership-boundary concerns:

- **Governance snapshot is a concrete, verifiable anchor** (a team-history commit hash / Cod Sync link ID), not a prose set of admin IDs. The frozen admin set is whatever the team DB says at that anchor. Every approver, the finalizer, and every peer can independently verify validity against the anchor. This closes the distributed-validity hole where two honest admins could disagree about whether the same finalization was legitimate.
- **The inviter is the canonical finalizer**, named in the transcript at proposal creation. The inviter initiates, relays the invitee's signed transcript, collects other admins' approvals, observes quorum met, and publishes the finalization. Quorum-closing is an observation the inviter makes, not an identity-assignment event. Concurrent approval races cannot produce different admitting authorities because the publisher is fixed from the start.
- **The inviter allocates the team-local `member_id`** at proposal creation. The invitee's acceptance transcript binds to the allocated ID but does not choose it. The invitee still generates their own device keys, cloud endpoint, and other material that is genuinely theirs; the team-local namespace stays governance-owned.

The inviter's special, orchestrating role throughout the flow — initiation, gathering the invitee's signed transcript, gathering other admins' approvals, publishing the finalization — is the cleanest available model. It matches the user-level story ("Alice is running this invite"), it eliminates race-sensitivity in admitting authority, and it degenerates to today's informal flow at `quorum = 1`.

### Fourth revision (this pass)

Three further fixes sharpening the member/device boundary, the transcript's mutable-vs-immutable cut, and when disagreement can surface:

- **Member/device bridge for approvals.** The governance snapshot anchor freezes not only admin and membership rosters but also the member→device mapping. An approval is cryptographically valid iff signed by a device key that the anchor's mapping shows as linked to a current admin. Two devices of the same admin dedupe to one vote per `admin_member_id`. Devices `device_link`-issued after the anchor cannot approve that proposal. The non-durable rule extends accordingly: any device-mapping change (device additions, revocations, role-affecting re-issuance) is a governance-state change that invalidates in-flight proposals.
- **Transport state stays out of the immutable transcript.** The invitee's cloud endpoint is removed from the admission transcript. Admission binds device keys and the allocated `member_id`; it does not bind transport metadata that may still change or not yet exist at signing time. Post-admission, the invitee configures their own incoming cloud endpoint via the normal member-transport-configuration flow (B7), which Small Sea needs independently for existing members who change cloud providers.
- **Proposal shell is published at initiation, not after the invitee responds.** The inviter's first act is to publish a proposal shell (anchor, allocated `member_id`, inviter's free-form framing) to team DB. Other admins see the proposal immediately and can decline to approve or rotate-for-exclusion before the invitee has invested effort or disclosed device material. The invitee's signed acceptance transcript is published later as an update to the same proposal row, at which point the transcript is complete and admins can sign approvals over it.

## The Decision To Accept Or Reject

Adopt the following model as the honest foundation for Small Sea's read/write access design:

1. **Read access is effectively endpoint-trust-scoped.** Any currently-admitted party (teammate or sibling device) can, in principle, proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this and should stop pretending to.
2. **Linked sibling device admission is a unilateral identity-owner act.** The existing sibling hands off whatever the new device needs. The sibling issues a `device_link` cert signed over the new device's concrete public keys and publishes that cert into the team DB. Other teammates observe the new device via the published cert and may object post-hoc by exclusion (see point 5). Linked-device admission therefore satisfies the transcript-binding, trusted-publisher, and governance-anchor rules automatically via the existing cert-issuance model.
3. **New-teammate admission is an inviter-orchestrated, transcript-bound, admin-quorum flow**, with the following non-negotiable crypto properties:
   - **Inviter's orchestrating role.** One admin is the inviter for a given proposal. The inviter initiates the proposal, delivers the token to the invitee OOB, receives the invitee's signed acceptance transcript (OOB or via cloud-proxy), publishes the proposal+transcript to team DB, collects other admins' approvals (for `quorum > 1`), observes when the quorum is met, and publishes the finalization mutation. The inviter is the canonical finalizer, named in the transcript from the start (`finalizer_member_id = inviter_member_id`).
   - **Governance snapshot anchor.** Every proposal is anchored to a concrete, verifiable team-history reference at creation time: the team's `Sync/core.db` commit hash (or the equivalent Cod Sync link ID). The frozen governance state at the anchor includes the admin roster, the membership roster, AND the member→device mapping. Every approver, the finalizer, and every future peer can replay the team history to the anchor and independently verify the frozen state. Proposals that do not match the anchor are invalid.
   - **Member/device bridge for approvals.** Admin approvals are policy-scoped to member but crypto-scoped to device. An approval is valid iff signed by a device key that the anchor's member→device mapping shows as linked to a current admin. Duplicate approvals from multiple devices of the same admin dedupe to one vote per `admin_member_id`. Devices linked after the anchor (via later `device_link` issuance) cannot approve that proposal; under the non-durable rule below, any such device-mapping change invalidates the proposal anyway, so this is belt-and-suspenders.
   - **Inviter allocates the team-local `member_id`.** At proposal creation the inviter mints a fresh UUIDv7 `member_id` for the invitee, records it in the proposal, and commits to it. The invitee's acceptance transcript binds to the allocated ID; the invitee does not choose it.
   - **Transcript binding.** Admissions are cryptographically bound to an immutable admission transcript containing: proposal ID and nonce, team ID, inviter / finalizer member ID, the team-history anchor reference, a digest of the frozen governance state (derivable from the anchor, covering admin roster, membership roster, and member→device mapping), the pre-allocated invitee `member_id`, and the invitee's signed acceptance blob (carrying the invitee's concrete device bootstrap-encryption key, device signing key, and confirmation of the allocated `member_id`). The transcript explicitly does NOT bind transport state: no cloud endpoint, no cloud subscription details. Admin approvals sign over the transcript — approvals cryptographically cannot be satisfied by a different cryptographic subject than the approver saw.
   - **Approval ordering and proposal visibility.** Inviter creates the proposal shell (anchor, allocated `member_id`, inviter's free-form framing) and publishes it to team DB immediately — other admins see the proposal at this point, before the invitee has responded, so non-approval or disagreement can surface early. Inviter delivers the proposal token to the invitee OOB. Invitee generates keys and signs the acceptance blob binding to the allocated `member_id`, returns it OOB (or via cloud-proxy). Inviter assembles the full transcript, signs an approval (counts as 1), publishes transcript + approval as an update to the existing proposal row. Other admins (for `quorum > 1`) sync, verify the now-complete transcript against the anchor and the frozen governance state, and sign approvals that accrue as team-DB rows. Inviter observes quorum met and publishes finalization.
   - **Trusted-finalizer publication.** Finalization is published by the inviter. Always. Under `quorum = 1` that is a single round of "inviter's approval meets quorum, inviter publishes." Under `quorum > 1` the inviter waits for others' approvals to reach threshold, then publishes. The invitee never publishes their own admission; the cert-chain write-acceptance rule (point 5) would reject it anyway.
   - **Quorum policy.** `quorum = 1` is the default. At that setting, the inviter's own approval alone meets quorum and the end-to-end flow degenerates to Alice-initiates → Bob-returns-signed-transcript → Alice-approves-and-publishes, close in feel to today's informal invite. `quorum > 1` is available for stricter teams.
   - **Non-durable proposals.** Proposals are invalidated by any governance-state change relative to the anchor: admin roster changes, membership roster changes, or member→device mapping changes (`device_link` issuance or revocation for any admin or member). Proposals also expire after a per-team time window. Invalidation aborts the proposal; no subsequent approval can finalize it. If the inviter is removed or unreachable past expiry, the proposal dies with it; no fallback finalizer is defined. Outstanding proposals are explicitly not durable bearer capabilities.
   - **Pre-admission objection is non-approval, not rotation.** An admin who does not want the invitee admitted withholds their approval. No rotation is required because admission never finalizes.
4. **Rotation has exactly two purposes:** (a) **exclusion** — removal, or post-admission objection, both handled by the same rotate-with-exclusion primitive; and (b) **hygiene** — routine, semantically neutral. Rotation is never used to *admit* a party.
5. **Write acceptance is cert-chain validated.** Peers accept a device's signed bundles only when that device's `device_link` / membership cert chains back to something they already trust. Additional local-override policies on top of cert checks are out of scope for this decision and deferred.

The proxy-anyway attack remains possible under this model. What changes is that it is no longer passive protocol fiction — it requires the admitter to actively violate an explicit decision by peers, and the excluded party cannot respond coherently to post-rotation traffic without continued live collusion.

## Why This Frame

The first-order argument is that the current "Bob must redistribute his sender key to B before B can honestly read Bob's traffic" rule cannot survive contact with endpoint trust. If Alice already has Device A with read access and she links Device B, nothing in the protocol prevents A from copying plaintext or receiver state to B. The ceremony describing B as "not yet a legitimate reader" is therefore performative, not a real confidentiality boundary. The same reasoning applies symmetrically to add-teammate.

The sharper, second-order argument — motivating admin-quorum admission — is that the *real* unresolved problem is not "can the protocol stop proxying?" (it cannot) but "how does the framework reduce ugly admit/remove churn when teammates disagree?" Moving disagreement earlier, into a proposal/approval stage before admission finalizes, is what governance primitives should be buying.

The third-order argument, shaping these revisions: governance primitives must be crypto-precise to avoid foundation-level regrets. Transcript binding, trusted-approver finalization, non-durable proposals, governance-snapshot anchoring, fixed-inviter-as-finalizer, governance-owned `member_id` allocation, an explicit member/device bridge for approvals, a transport-state cut that keeps the immutable transcript immutable, and early proposal-shell visibility are not polish on top of quorum — they are the shape that makes quorum an honest primitive. Each closes a distinct class of failure that would otherwise become painful to retrofit out.

## Strongest Counter-Arguments Considered

**"Formal confidentiality analysis is cleaner when the property is 'only explicitly-admitted readers can decrypt.'"** The softer property is the true one; writing the false-but-cleaner one down does not make it enforceable. If a later formal workstream needs a tighter model, it can describe the rotation-on-exclusion and transcript-binding guarantees precisely.

**"Admin-quorum adds complexity for users who do not want it."** The default `quorum = 1` rebuts this. Small teams pay no extra friction.

**"Invalidating proposals on governance change is over-conservative."** Real, but the reverse migration — tightening a too-permissive rule after proposals have been normalized as durable bearer capabilities — is cryptographically ugly. Conservative default now, permissiveness added later if concrete usage justifies.

**"Fixing the inviter as sole finalizer creates a single point of failure."** Accepted. If the inviter disappears mid-flow, the proposal dies via the invalidation/expiry rule and must be re-initiated by another admin. This is deliberate: a fallback finalizer would either reintroduce the race-sensitivity concern (whichever-admin-decides-to-take-over becomes admitting authority) or require its own deterministic-selection rule with its own design surface. For a pre-alpha foundation, simpler is better.

**"Inviter-allocates `member_id` is awkward because the inviter doesn't yet know the invitee's device keys."** Not actually awkward — `member_id` is a pure namespace allocation (UUIDv7), unrelated to device keys. It's the team-local handle for this prospective member, allocated by the trusted side at proposal time and bound into the transcript. The invitee's device keys come later, in the invitee-signed acceptance blob, which binds to the pre-allocated `member_id`.

**"Publishing a proposal shell before the invitee responds leaks the inviter's intent to the team."** Intended, not leaked. Early visibility is the point: if some admin is going to object, better they do it before the invitee has spent any effort or disclosed device material. The alternative — team DB learns of the proposal only after the invitee has engaged — recreates the social problem quorum was meant to reduce. If inviter secrecy is ever desired for a specific case, that is a UX/policy layer on top, not a default.

**"Keeping transport out of the immutable transcript means the admitted member is inert until they configure cloud."** Correct, and survivable. Post-admission transport setup is a capability Small Sea needs anyway (existing members change cloud providers). B7 scopes that flow. Keeping mutable operational metadata out of the signed admission artifact is worth the interim inertness — freezing endpoint data into the transcript means either provisioning cloud before signing (operational ordering pain) or binding to metadata that can age out (integrity pain).

## What This Simplifies

- **#69 collapses.** B is bootstrapped from a sibling A by receiving (i) current team state, (ii) A's copies of peer sender keys A holds, (iii) material for B's own publication. No round-trip to every other team member. Join-time-forward becomes the honest historical-access policy.
- **Payload 3 changes role.** No longer "B reporting back for admission." Becomes B's own sender-key publication via standard `redistribute_sender_key(...)`.
- **`test_linked_device_bootstrap_requires_real_redistribution_for_other_senders` becomes wrong in spirit**; retired and replaced.
- **Invitation flow gets crypto-tightened, user-feel-preserved at default quorum.** Under `quorum = 1`, UX is still Alice-invites / Bob-responds / Alice-finalizes. Under the hood: inviter-allocated `member_id`, signed acceptance transcript over inviter-assigned identity, signed approval over the full transcript, inviter-published finalization bound to a team-history anchor. Today's `accept_invitation` path of invitee-authored team-DB writes goes away.
- **Rotation's mental model** collapses to exclusion + hygiene.

## What Survives Unchanged

- **#43 rotation-on-removal** stays correct and load-bearing for exclusion (removal + post-admission objection).
- **`redistribute_sender_key(...)`** is the substrate for all exclusion timings and for new-member own-key publication.
- **`device_prekey_bundle` publication, X3DH, Double Ratchet** — substrate for any pairwise control-plane operation.
- **Cert-chain validation for write authority** stays. It is the rule that makes "inviter-publishes-finalization" not just policy but crypto necessity.

## Proposed GitHub Issue Deltas

*(Plans for implementation phase. No GitHub changes until the decision is accepted.)*

### Keep (no change)
- **#43, #44, #48, #6, #4** — aligned or orthogonal.

### Modify
- **#69** — rescope to sibling-handoff bootstrap with join-time-forward access; retire payload-3-as-admission.
- **#59** — keep device-aware keys and peer routing; drop "every member must redistribute on new device" trigger; watch behavior serves objection visibility, not admission orchestration.
- **#73** — clarify periodic rotation is "hygiene," never "admission." Doc-only.

### Close As Superseded
Any issue scoped to "distribute sender keys to newly linked devices from every other sender." Exact candidates identified during implementation.

### Add
- **"Admin-quorum admission: inviter-orchestrated, transcript-bound, anchor-verified proposal/approval/publish for new teammates."** Schema for proposals (team-history anchor reference, pre-allocated `member_id`, frozen-governance-state digest covering admin roster, membership roster, and member→device mapping, finalizer_member_id); proposal-shell publication at initiation (before invitee response); signed-acceptance-transcript rows (bind device keys and `member_id` only — transport explicitly excluded); admin-approval-signature rows keyed by (proposal_id, admin_member_id) with device-key signatures validated against the anchor's member→device mapping and deduped per admin; invalidation on governance-state change (including device-mapping changes) or expiry; inviter-publishes-finalization. This is the ticket B5 lives under.
- **"Replace historical-access test for same-member linked-device bootstrap."** Retire the "requires real redistribution" test; add (a) B reads Bob's current traffic via A's peer sender key, (b) Bob rotating-excluding B cuts B off from Bob's subsequent traffic absent active proxying.
- **"Admission-event visibility and objection affordance."** Manager/Hub watch path that surfaces new `device_link` certs, new invitation proposals (so frozen-set admins can approve or withhold), and finalized admissions, with first-class objection affordance.
- **"Spec and architecture doc sweep for endpoint-truth language."** Rewrite read-confidentiality language. Add framing on rotation's two purposes, on transcript-bound admin-quorum, on trusted-inviter-finalizer, on governance-snapshot anchoring, on member/device approval bridge, on transport out of transcript, on early proposal-shell visibility.
- **"Member transport configuration."** Signed announce-endpoint mutation, verification rule (must be signed by a currently-linked device key of the announcing member), outgoing-routing update on the receiving side. Used by the invitation flow post-admission AND by existing members changing cloud providers. This is the ticket B7 lives under.

## Branch-Sized Chunks Of Follow-Up Work

### B1. Doc sweep: endpoint-truth rewrite

Touch `architecture.md`, `packages/small-sea-manager/spec.md`, `Documentation/open-architecture-questions.md`, `packages/cuttlefish/README.md`. Replace read-confidentiality language. Add paragraphs on rotation's two purposes, admin-quorum with transcript binding, governance-snapshot anchoring, inviter-as-finalizer, inviter-allocated `member_id`, non-durable proposals, and linked-device admission as a unilateral identity-owner act that satisfies the same crypto rules via cert issuance.

**Why first:** downstream branches cite the updated docs.

### B2. Admission-event visibility and objection affordance

When a new `device_link` cert, a new invitation proposal, or a finalized admission appears in team DB, every existing peer's Manager surfaces it promptly. Admins in the frozen set of an open proposal see it prominently so they can approve or withhold. First-class UI affordance for "object" (invokes exclusion primitive) on finalized admissions.

**Why this is load-bearing:** under quorum, visibility is what makes "non-approval" a meaningful act. Admins cannot decide on proposals they do not see.

### B3. Linked-device bootstrap: sibling peer-sender-key handoff

Extend `create_linked_device_bootstrap(...)` bundle with A's peer-sender-key snapshot. Update `finalize_linked_device_bootstrap(...)` to store them. Retire the old boundary test.

**Gated on:** B2.

### B4. Payload-3 reframe: B's own publication via the standard redistribute primitive

Replace bespoke payload-3 with B calling `redistribute_sender_key(...)` post-bootstrap.

### B5. Invitation-flow rework: inviter-orchestrated, transcript-bound admin-quorum

Largest follow-up chunk. Scope:

- Per-team settings: `admission_quorum` (default 1), `proposal_expiry`.
- New team-DB schema: proposal rows carrying proposal ID, nonce, inviter member ID (doubles as finalizer), team-history anchor reference (commit hash or link ID), pre-allocated invitee `member_id`, frozen-admin-set digest, state, created_at, expires_at; acceptance-transcript rows or fields keyed by proposal ID; admin-approval-signature rows keyed by (proposal_id, admin_member_id) carrying signatures over the full transcript digest; finalization mutation published by the inviter.
- Flow implementation:
  1. Inviter creates proposal (anchored to current team history; allocates invitee's `member_id`; records frozen-governance-state digest covering admin roster, membership roster, member→device mapping) and publishes the proposal shell to team DB. Team DB now contains a proposal row in "awaiting invitee" state; other admins see it immediately.
  2. Inviter delivers the proposal token OOB to the invitee.
  3. Invitee generates device keys; signs acceptance blob binding to the inviter-allocated `member_id` and the proposal ID/nonce. No cloud endpoint is bound at this step.
  4. Invitee returns the signed acceptance blob OOB (or via cloud-proxy).
  5. Inviter verifies acceptance; assembles the full transcript; signs their own approval (= 1 toward quorum); publishes transcript + approval as an update to the existing proposal row (state → "awaiting quorum").
  6. (Only if `quorum > 1`) Other admins sync, verify the now-complete transcript against the anchor and the frozen member→device mapping, and sign approvals that accrue as team-DB rows. Approvals by devices not linked to current admins at the anchor are rejected. Duplicate approvals from multiple devices of the same admin dedupe to one vote per `admin_member_id`.
  7. Inviter observes quorum met; signs and publishes the finalization mutation.
  8. Invitee observes finalization (via OOB notification from inviter plus team-repo read access delivered at step 2), then runs the member-transport-configuration flow (B7) to stand up their own incoming cloud endpoint and announce it to the team. Once transport is live, invitee publishes own sender key via standard `redistribute_sender_key(...)`.
- Anchor verification: every signer (including admin approvers and the finalizer) must verify that the frozen-governance-state digest in the proposal matches the state derivable from team history at the anchor, including the member→device mapping. Approvals that do not satisfy this check are rejected.
- Device-key validation on approvals: each admin approval signature is verified against a device key listed in the anchor's mapping for a current admin member. Approvals signed by post-anchor devices or by devices not linked to admins are rejected.
- Invalidation logic: on any admin-set-relevant change, membership change, or device-mapping change in team history after the anchor, open proposals become invalid. Proposals past `expires_at` also invalid. Invalid proposals cannot be finalized; attempted finalization fails with a clear error.
- Degeneration at `quorum = 1`: step 6 is skipped; inviter proceeds to 7 immediately after step 5.
- Edge cases: inviter removed or unreachable (proposal dies via invalidation/expiry, no fallback finalizer); concurrent admin approvals (all accrue; inviter observes threshold and publishes); approval from admin not in the frozen set (ignored); approval from a device not linked to an admin at the anchor (ignored); multiple approvals from different devices of the same admin (dedupe to one vote); approval from admin now-invalid under governance-change rule (ignored); invitee attempts own-admission write (rejected by cert-chain check); invitee submits a different transcript later (treated as new proposal, requires re-initiation).

**Gated on:** B2.

### B6. Issue hygiene

GitHub actions from "Proposed GitHub Issue Deltas." One-shot administrative task. Runs after B1 so rescoped issues can point at updated docs.

### B7. Member transport configuration

Independent capability: a member stands up or changes their incoming cloud endpoint and announces it to the team. The member writes a signed announce-endpoint mutation into team DB (signed by one of their own currently-linked device keys). Other members sync the mutation, verify the signature against the member's current device-mapping, and update their local mapping of member → endpoint for outgoing payloads.

This capability is needed independently of invitation: existing members need to change cloud providers, recover from lost cloud accounts, or add secondary incoming endpoints. B5 depends on it for full UX because a newly admitted invitee uses this same flow to bring their own transport online.

**Scope intentionally kept narrow:** the announce-endpoint mutation shape, signature-verification rule, outgoing-routing update. Out of scope: cloud-provider onboarding UX, billing integration, account recovery, migration of in-flight messages from an old endpoint.

**Relationship to B5:** B5 can technically land before B7 ships — a newly admitted member is cryptographically admitted but operationally unreachable until transport is configured. That is a coherent intermediate state but not one we want users to live in. Cleanest release order is B7 before B5, or B7 as a parallel workstream landing close to B5.

## Ordering Constraints

- **B1 first.**
- **B2 before B3 and B5.** Admission-widening branches require the visibility path. Under transcript-bound quorum, admins cannot approve or withhold on proposals they do not see.
- **B4 follows B3.**
- **B6 after B1.**
- **B7 before B5 for full UX**, or parallel and landing close to B5. B5 can technically land without B7 (admitted-but-inert invitee), but that is bad UX and should not be the shipping state.

B3, B4, B5 otherwise schedulable in any order once B2 has landed. B7 is otherwise independent and can move on its own schedule.

## Documentation Changes (Scoped Here, Executed In B1)

- `architecture.md` §"Fully Decentralized Team Management": rewrite rotation paragraph (exclusion + hygiene). Add paragraphs on admin-quorum, governance-snapshot anchoring (including member→device mapping), member/device approval bridge, inviter-as-finalizer, inviter-allocated `member_id`, early proposal-shell visibility, transport out of transcript, non-durable proposals.
- `packages/small-sea-manager/spec.md` §"Linked-device team bootstrap": rewrite historical-boundary and slice-scope subsections.
- `packages/small-sea-manager/spec.md` §"Invitations" and §"Invitation Protocol (detailed)": describe inviter-orchestrated, transcript-bound proposal/approval/publish model. Spell out that the inviter writes the finalization mutation (not the invitee) and that the `member_id` is inviter-allocated.
- `Documentation/open-architecture-questions.md` §5 "Identity Model": add a settled-decisions subsection citing the reframe, admin-quorum, transcript binding (with transport explicitly excluded), governance anchor (including member→device mapping), member/device approval bridge, inviter-as-finalizer, inviter-allocated `member_id`, early proposal-shell publication, non-durable proposals.
- `packages/cuttlefish/README.md` sender-keys and trust sections: reconcile language in B1.

## Validation (For This Meta-Plan Branch)

The deliverable is this document. "Done" when a skeptical reviewer can confirm:

1. Decision stated precisely enough that later branches can be measured against it. Each foundation-level crypto property — transcript binding, governance-snapshot anchor, inviter-as-canonical-finalizer, inviter-allocated `member_id`, non-durable proposals — is named explicitly at the top level, not buried.
2. Each follow-up chunk has a one-paragraph scope clearly smaller than a re-plan of the whole reframe.
3. GitHub issue deltas are executable.
4. Ordering constraints are spelled out.
5. No code changed; no GitHub state changed.

## Out Of Scope For This Branch

- Per-branch plans for B1–B6.
- Code changes.
- GitHub issue edits.
- Final sequencing of B3/B4/B5 beyond "Ordering Constraints."
- Write-acceptance local-override policy.
- Quorum-style governance for linked sibling-device admission.
- Deterministic-adoption-from-signed-approval-set as an alternative finalization strategy. This plan commits to inviter-publishes finalization; if later experience argues for deterministic adoption, it can be revisited.
- Fallback-finalizer design if the inviter cannot complete the flow. Current rule: proposal dies via invalidation/expiry.
- Formal-model writeup.

## Skeptic-Facing Wrap-Up

A reviewer accepting this meta-plan should be able to answer:

1. **What decision is being asked for?** Accept or reject the five-point model, including all sub-bullets of point 3.
2. **What changes in the codebase if accepted?** Nothing immediately. B1–B6 are the planned follow-ups.
3. **What existing work becomes obsolete?** Admission-flavored parts of #69's design and its "requires real redistribution" test. Invitation flow's invitee-publishes-own-admission pattern.
4. **What existing work stays correct?** #43's rotation-on-removal primitive. `device_link` cert chaining. `redistribute_sender_key(...)` as the exclusion + own-key-publication substrate.
5. **What is new?** Admin-quorum admission for new teammates with the following non-negotiable properties: inviter's orchestrating role, governance-snapshot anchor (covering admin roster, membership roster, and member→device mapping), member/device bridge for approvals with per-admin dedupe, inviter-allocated `member_id`, transcript binding over invitee's concrete device keys (explicitly NOT over transport state), early proposal-shell publication at initiation, inviter-published finalization, non-durable proposals. Plus a sibling capability (B7) for members to announce/update their own incoming cloud endpoint independently of admission.
6. **Where are foundation-level holes addressed?** TOCTOU → "Transcript binding." Bootstrap paradox → "Trusted-finalizer publication." Bearer-capability durability → "Non-durable proposals." Distributed validity → "Governance snapshot anchor." Admitting-authority determinism → "Inviter's orchestrating role" + "Trusted-finalizer publication." Team-local namespace ownership → "Inviter allocates the team-local `member_id`." Member-scoped policy vs. device-scoped crypto → "Member/device bridge for approvals." Mutable transport state inside an immutable artifact → "Transcript binding" (transport explicitly excluded) + B7 post-admission flow. Late admin visibility of proposals → "Approval ordering and proposal visibility" (shell published at initiation).
7. **What is not decided here?** Final sequencing of B3/B4/B5 beyond ordering constraints; exact expiry windows and concrete anchor format (belong to B5); exact superseded-issue list (requires fresh inventory); local-override policy for write acceptance; deterministic-adoption as alternative finalization; fallback-finalizer design; formal-model writeup.
