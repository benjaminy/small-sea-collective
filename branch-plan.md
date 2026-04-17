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

This iteration folds in committee feedback that the first draft did not yet reflect:

- Objection is no longer a distinct primitive. It is the same rotation-with-exclusion machinery applied either at admission-time or later.
- A new design concept is introduced: **admin-quorum admission** for new teammates. `quorum = 1` is the default and behaves like today's informal invite flow. Stricter teams can set `X > 1`; the eligible admin set is frozen at proposal creation.
- A dedicated invitation-flow rework chunk (B5) is added. The first draft's branch inventory mostly covered linked-device bootstrap; the accepted model also changes add-member semantics.
- Write-objection as an independent axis is dropped from the top-level decision. Cert-chain validation is the only write-acceptance gate named here; local-override policies on top of cert checks are deferred.
- An explicit ordering constraint is added: visibility/approval infrastructure must land before or alongside branches that widen admission-by-handoff.

## The Decision To Accept Or Reject

Adopt the following model as the honest foundation for Small Sea's read/write access design:

1. **Read access is effectively endpoint-trust-scoped.** Any currently-admitted party (teammate or sibling device) can, in principle, proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this and should stop pretending to.
2. **Linked sibling device admission is a unilateral identity-owner act.** The existing sibling hands off whatever the new device needs (current team state, copies of peer sender keys the sibling holds, the joining device's own publication material). Other teammates observe the new device via its `device_link` cert and may object post-hoc by exclusion (see point 5).
3. **New-teammate admission is a proposal → approvals → finalize flow with per-team admin quorum.**
   - `quorum = 1` is the default. The inviter's own creation counts as the single required approval, and the flow behaves like today's informal invite flow end-to-end.
   - `quorum > 1` is available for stricter teams. The invitee does not become a member until `X` distinct approvals from the frozen admin set are on the proposal.
   - The **eligible admin set is frozen at proposal creation.** Approvals from admins not in the frozen set are ignored; approvals from admins who were in the set but have since been removed still count.
   - Under quorum, objection-before-admission is simply withholding approval. No rotation is needed because the member never became admitted.
4. **Rotation has exactly two purposes:** (a) **exclusion** — removal, or post-admission objection, both handled by the same rotate-with-exclusion primitive; and (b) **hygiene** — routine, semantically neutral. Rotation is never used to *admit* a party.
5. **Write acceptance is cert-chain validated.** Peers accept a device's signed bundles only when that device's `device_link` / membership cert chains back to something they already trust. Additional local-override policies on top of cert checks are out of scope for this decision and deferred to a separate design question.

The proxy-anyway attack remains possible under this model. What changes is that it is no longer passive protocol fiction — it requires the admitter to actively violate an explicit decision (non-approval, rotation, or removal) by peers, and the excluded party cannot respond coherently to post-rotation traffic without continued live collusion.

## Why This Frame

The first-order argument is that the current "Bob must redistribute his sender key to B before B can honestly read Bob's traffic" rule cannot survive contact with endpoint trust. If Alice already has Device A with read access and she links Device B, nothing in the protocol prevents A from copying plaintext or receiver state to B. The ceremony describing B as "not yet a legitimate reader" is therefore performative, not a real confidentiality boundary. The same reasoning applies symmetrically to add-teammate: if Alice invites Carol and Alice is willing to proxy, Bob's refusal to redistribute his sender key does not prevent Carol from reading Bob's current traffic.

The sharper, second-order argument — the one that motivates admin-quorum admission — is that the *real* unresolved problem is not "can the protocol stop Alice from proxying to Carol?" (it cannot) but "how does the framework reduce ugly admit/remove churn when Alice and Bob disagree about Carol?" Without admin-quorum, disagreement surfaces as admit-then-object cycles: Alice admits Carol unilaterally, Bob rotates-excluding Carol immediately, and the team burns through repeated admission/removal/rotation cycles. Moving the disagreement earlier — to a proposal/approval stage before any admission actually happens — is what a team-governance primitive should be buying, and it is what the reframe gains by separating "local handoff" from "team-recognized admission."

This reframe therefore commits to two parallel simplifications: the protocol stops claiming a read-confidentiality boundary it never enforced, and the admission flow gains an explicit governance primitive (quorum) that actually addresses the churn problem the old fiction was papering over.

## Strongest Counter-Argument Considered

Formal confidentiality analysis is cleaner when the written protocol property is "only explicitly-admitted readers can decrypt." Embracing endpoint reality means the formal property becomes "honest endpoints within an admitted identity share read access," which is a softer statement.

This branch rejects that counter-argument for the following reasons: Small Sea is pre-alpha, user-facing, and targeted at real collaboration rather than formal-verification deliverables; the softer property is the true one, and writing the false-but-cleaner one down does not make it enforceable; and keeping the stricter language degrades the spec by making it describe a fiction. If a later formal-analysis workstream needs a tighter model, it can describe the rotation-on-exclusion guarantee precisely.

A second, narrower counter-argument: "admin-quorum adds complexity for users who do not want it." The default `quorum = 1` is specifically designed to rebut this. Small, informal teams pay no extra friction; the feature only surfaces when a team opts into stricter policy.

## What This Simplifies

- **#69 (linked-device bootstrap) collapses.** The joining device B is bootstrapped from a sibling device A by receiving (i) the team's current state, (ii) A's copies of the peer sender keys A already holds, and (iii) material for B to publish its own device key / sender key. No round-trip to every other team member is required for B to become a legitimate reader. Join-time-forward becomes the honest and natural historical-access policy.
- **Payload 3 in the current #69 flow changes role.** It stops being "B reporting back so the authorizing sibling can tell peers to admit B." It becomes B's own sender-key publication to peers, which is structurally identical to any other device's sender-key publication and goes over the standard `redistribute_sender_key(...)` primitive from #43.
- **`test_linked_device_bootstrap_requires_real_redistribution_for_other_senders` becomes wrong in spirit.** Under the new model, B *should* be able to read future traffic from Bob using the peer sender key A already has. That test is retired and replaced with one that asserts what is actually protocol-enforceable (e.g., Bob can rotate-excluding B, and after that rotation B cannot decrypt Bob's new traffic without A's active proxying).
- **Invitation flow language straightens out.** The acceptance handshake stops describing a read-confidentiality event it was not actually performing. Under quorum = 1 the end-to-end user experience is unchanged from today; under quorum > 1 the flow adds an explicit proposal state with approvals before the invitee becomes a member.
- **Rotation gains a clean two-purpose mental model.** Exclusion (one primitive; applies whether triggered by removal or by post-admission objection) plus hygiene (periodic, semantically neutral). The admission case disappears from rotation's vocabulary entirely.

## What Survives Unchanged

- **#43 rotation-on-removal stays correct and load-bearing.** It is the one place where the cryptography really does enforce a confidentiality boundary (against an ex-member after the honest-majority rotates excluding them). The archived `issue-43-sender-key-rotation` branch's code does not need to be redone; its justification narrows from "rotation enforces all membership-change confidentiality" to "rotation enforces exclusion-on-removal confidentiality." Post-admission objection reuses the same primitive.
- **The `redistribute_sender_key(...)` primitive stays as-is and carries more of the load.** It is the mechanism behind all three exclusion timings (removal, post-admission objection, and — unchanged — post-bootstrap publication by a newly-admitted device).
- **`device_prekey_bundle` publication, X3DH, and Double Ratchet wiring stay.** These are the substrate for any pairwise control-plane operation.
- **Cert-chain validation for write authority stays.** `device_link` and membership certs continue to gate whether a peer accepts a device's signed bundles as legitimate team traffic.

## Proposed GitHub Issue Deltas

*(These are plans for the implementation phase of this branch. No GitHub changes happen until the decision is accepted.)*

### Keep (no change)

- **#43** — already aligned once its justification is reread as "rotation-on-exclusion." No scope change.
- **#44** — sender-key storage revisit; orthogonal.
- **#48** — Manager multi-device NoteToSelf sync and team discovery; orthogonal.
- **#6** — identity model; the reframe refines an open question but does not resolve the whole issue.
- **#4** — Cuttlefish integration; orthogonal.

### Modify

- **#69** — rescope. Drop the "Bob must redistribute to B" framing. Reframe as: bootstrap B by sibling handoff of everything B needs (team state + A's peer sender keys + B's publication material), with join-time-forward historical access. Existing same-member bootstrap code is largely salvageable; the payload-3-as-admission semantics are what gets retired.
- **#59** — rescope. Device-aware sender keys and peer routing stay in scope; the "when a new linked device appears, every member must redistribute to it" trigger goes away. What #59 still owns: device-scoped send keys, peer-table device dimension, per-device Hub routing, and watch behavior that surfaces new device-link events (now for objection visibility, not admission orchestration).
- **#73** (periodic sender-key rotation) — clarify that periodic rotation is the "hygiene" bucket, never the "admission" bucket. Doc-only adjustment to the issue text.

### Close As Superseded

Any open issue whose scope is specifically "distribute sender keys to newly linked devices from every other sender" should close as superseded. Exact candidate identification happens during the implementation phase — it needs a fresh pass through the open issue list rather than a memory-based recollection.

### Add

- **New issue — "Admin-quorum admission: proposal/approval/finalize model for new teammates."** Scope: per-team quorum setting, frozen admin set semantics, new schema for proposals and approvals, signed-approval rows, quorum-met finalize trigger. This is the ticket the invitation-flow rework branch (B5) lives under.
- **New issue — "Replace historical-access test for same-member linked-device bootstrap."** Scope: retire `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders`; add tests that (a) B can read Bob's current traffic post-bootstrap using A's copy of Bob's sender key, and (b) Bob rotating-excluding B cuts B off from Bob's subsequent traffic absent active proxying.
- **New issue — "Admission-event visibility and objection affordance."** Scope: the Manager/Hub watch path that surfaces new `device_link` certs, new invitation proposals, and finalized admissions to the user promptly, with an objection affordance that invokes the rotate-with-exclusion primitive. Distinct from the quorum mechanics themselves — this is the ambient visibility that makes objection possible.
- **New issue — "Spec and architecture doc sweep for endpoint-truth language."** Scope: rewrite the read-confidentiality language in spec.md, architecture.md, open-architecture-questions.md, and cuttlefish/README.md. Add explicit framing on rotation's two purposes and on admin-quorum.

(The first draft named a "read-objection / write-objection as independent axes" issue; it is intentionally dropped. Exclusion is one primitive; write-objection-as-local-policy is deferred.)

## Branch-Sized Chunks Of Follow-Up Work

Each is a sketch. None of these gets a full branch plan in this branch; each gets its own `branch-plan.md` when started.

### B1. Doc sweep: endpoint-truth rewrite

Touch `architecture.md`, `packages/small-sea-manager/spec.md`, `Documentation/open-architecture-questions.md`, and `packages/cuttlefish/README.md` (Sender Keys / Web of Trust sections). Replace read-confidentiality language that asserts a protocol-enforceable boundary with the endpoint-truth framing. Add explicit paragraphs on rotation's two purposes (exclusion, hygiene), on admin-quorum admission for new teammates, and on linked-device admission as a unilateral identity-owner act. No code changes.

**Why first:** all subsequent implementation branches will cite the updated docs as their reference.

### B2. Admission-event visibility and objection affordance

Make sure that when a new `device_link` cert, a new invitation proposal, or a finalized admission appears in the team DB, every existing peer's Manager surfaces that event promptly with enough context for the user to decide whether to act. Provide a first-class UI affordance for "object" (which invokes the existing exclusion primitive) on finalized admissions. This chunk can reuse most of #59's watch infrastructure.

**Why this is load-bearing:** if admission-time read-confidentiality against the admitter was fiction, observability-and-objection is the real consent mechanism, and its latency and discoverability matter. This chunk must land before any branch that widens admission-by-handoff.

### B3. Linked-device bootstrap: sibling peer-sender-key handoff

Extend the bootstrap bundle produced by `create_linked_device_bootstrap(...)` to include A's current snapshot of peer sender keys. Update `finalize_linked_device_bootstrap(...)` to store those as local `peer_sender_key` rows on B. Retire the "B cannot read Bob's future traffic until Bob redistributes" boundary and replace the test asserting it.

**Gated on:** B2 landing first.

### B4. Payload-3 reframe: B's own sender-key publication via the standard redistribute primitive

Replace the current "B returns a sender distribution payload to the authorizing sibling, which then tells each peer to admit B" path with "B uses `redistribute_sender_key(...)` with target set = all peer devices in the team" once bootstrap is complete. Peers process through the existing `receive_sender_key_distribution(...)`. No special admission ceremony.

**Why:** collapses a bespoke path into the primitive #43 already proved.

### B5. Invitation-flow rework: admin-quorum proposal/approval/finalize

This is the largest follow-up chunk. Scope:

- Add a per-team `admission_quorum` setting (team settings schema; default = 1).
- Replace the current immediate-admission `invitation` model with a proposal/approval/finalize state machine. Under quorum = 1 the end-to-end external behavior is unchanged.
- Schema: add a snapshot of the eligible admin set to each proposal row (frozen at creation); add signed-approval rows keyed by (proposal_id, admin_member_id); add finalization state and trigger.
- Flow: inviter creates a proposal (counts as 1 approval automatically); out-of-band token delivery to invitee remains; invitee can do local acceptance work but cannot finalize team-DB membership until quorum is met; invitee/Manager watches for quorum-met and then finalizes.
- Edge cases documented in the branch plan: admin removed after approving, inviter removed after proposing, concurrent proposals for the same invitee, approval from an admin not in the frozen set (ignored).

**Gated on:** B2 landing first. Under the agreed ordering constraint, admission-governance changes that widen admission must not ship before the visibility path does.

### B6. Issue hygiene: close superseded, retitle rescoped, add new

The GitHub actions listed in "Proposed GitHub Issue Deltas." One-shot administrative task. Runs after B1 so the rescoped issues can point at updated docs.

## Ordering Constraints

Explicit, committee-required:

- **B1 first.** Docs anchor the shared vocabulary. Everything downstream references them.
- **B2 before B3 and B5.** Any branch that widens admission-by-handoff — linked-device or teammate — must not ship before the minimum visibility/objection path exists. B2 is that path.
- **B4 follows B3.** B4's reframe of payload-3 depends on the sibling handoff in B3.
- **B6 after B1, ideally after the code branches it depends on are scoped.** Pure administrative dependency.

B3, B4, and B5 can otherwise be scheduled in any order once B2 has landed. They touch largely disjoint code.

## Documentation Changes (Scoped Here, Executed In B1)

- `architecture.md` §"Fully Decentralized Team Management": rewrite the paragraph on key rotation to reflect exclusion + hygiene as the two purposes. Drop any implication that rotation gates admission. Add a short paragraph on admin-quorum.
- `packages/small-sea-manager/spec.md` §"Linked-device team bootstrap": rewrite the "Historical boundary and visibility" and "Scope of the current slice" sections. Remove the "not yet redistributed by every other sender" framing.
- `packages/small-sea-manager/spec.md` §"Invitations" and §"Invitation Protocol (detailed)": describe the proposal/approval/finalize model. Call out that `quorum = 1` is default and behaviorally equivalent to today.
- `Documentation/open-architecture-questions.md` §5 "Identity Model": add a settled-decisions subsection citing the reframe. Include admin-quorum admission and the exclusion-is-one-primitive rule.
- `packages/cuttlefish/README.md` sender-keys and trust sections: reconcile language. Scope-confirm in B1; may only need light edits.

## Validation (For This Meta-Plan Branch)

This branch's deliverable is this document. It is "done" when a skeptical reviewer can read it and confirm:

1. The decision at the top is stated precisely enough that a later branch can be measured against it. A reviewer who adopts the decision should be able to resolve downstream design ambiguities by pointing at "The Decision To Accept Or Reject."
2. The frame is distinguished from what it replaces, the strongest counter-arguments are written down, and the churn-reduction motivation for admin-quorum is explicit.
3. Each named follow-up chunk has a one-paragraph scope that is clearly smaller than a re-plan of the whole reframe. No chunk silently imports a full #69-scale or invitation-flow-scale redesign.
4. The GitHub issue deltas are executable: each "modify / close / add" entry names what specifically changes.
5. Ordering constraints are spelled out so a later sequencer does not accidentally ship B3 or B5 before B2.
6. No code is changed on this branch; no GitHub state is changed on this branch.

## Out Of Scope For This Branch

- Writing per-branch plans for B1–B6. Each chunk gets its own branch and its own `branch-plan.md` when started.
- Touching code in `provisioning.py`, tests, or schemas.
- Opening, closing, or editing GitHub issues.
- Final sequencing of B3, B4, B5 beyond the constraints in "Ordering Constraints."
- Write-acceptance local-override policy. Cert-chain validation is the named gate here; any additional local policy ("I will refuse this admitted device's signatures on my clone") is deferred as a separate design question.
- Any quorum-like governance for linked sibling-device admission. Committee scoped quorum to teammate admission only; linked-device remains unilateral. A future design could revisit this per-team-policy-style.
- Formal-model writeup of the new trust model.

## Skeptic-Facing Wrap-Up

A reviewer accepting this meta-plan should be able to answer:

1. What exactly is the decision being asked for? *Accept or reject the five-point model in "The Decision To Accept Or Reject."*
2. What changes in the codebase if this is accepted? *Nothing immediately; B1–B6 are the planned follow-ups, each as its own branch.*
3. What existing work becomes obsolete? *The admission-flavored parts of #69's design and its "requires real redistribution" test. The invitation-flow's immediate-admission model, replaced by proposal/approval/finalize with default quorum = 1.*
4. What existing work stays correct? *#43's rotation-on-removal primitive (now doing double duty as the post-admission objection mechanism). `device_link` cert chaining for write acceptance. `redistribute_sender_key(...)` as the substrate for all exclusion timings.*
5. What is new? *Admin-quorum admission for new teammates, with frozen eligible-admin set at proposal creation, default quorum = 1.*
6. Where is the strongest counter-argument addressed? *"Strongest Counter-Argument Considered."*
7. What is not decided here? *Final sequencing of B3/B4/B5 beyond the ordering constraint; exact superseded-issue list (requires fresh inventory pass during implementation); local-override policy for write acceptance; formal-model writeup.*
