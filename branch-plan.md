# Branch Plan: Trust-Domain Reframe (Meta-Plan)

**Branch:** `issue-97-trust-domain-reframe`
**Base:** `main`
**Primary issue:** #97 "Evaluate read-access trust domain for linked team devices"
**Kind:** Meta-plan. Output is a decision + an inventory of follow-up branches, not code.
**Related issues (inputs):** #69, #59, #43, #48, #6, #4, #44, #73
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`, `packages/cuttlefish/README.md`, `Documentation/open-architecture-questions.md`
**Related code of interest:** `packages/small-sea-manager/small_sea_manager/provisioning.py` (linked-device bootstrap, rotation, redistribution), `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`

## Purpose

Issue #97 surfaced an architectural question that cuts across linked-device bootstrap (#69), sender-key redistribution (#43), and peer/device runtime (#59): the protocol has been describing a read-access confidentiality boundary that the endpoint model cannot actually enforce. This branch exists to decide whether to reframe the protocol to match reality, and if so, to inventory the follow-up work.

This branch does **not** implement any of the follow-up work. It does not touch GitHub issues. The GitHub issue deltas proposed below are executed in the *implementation phase* of this branch, once the decision is accepted. Until then, this branch is decision + inventory only.

## The Decision To Accept Or Reject

Adopt the following model as the honest foundation for Small Sea's read/write access design:

1. **Read access is an endpoint property of an identity, not a protocol-enforceable boundary.** Any currently-admitted party (teammate or sibling device) can, in principle, proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this and should stop pretending to.
2. **Admission is a purely local handoff by the inviter.** No team-wide cryptographic ceremony is required for a new device or a new teammate to become readable. The inviter supplies whatever the invitee needs (current team state, copies of peer sender keys the inviter holds, the invitee's own bootstrap material).
3. **Rotation has exactly two purposes:** (a) **exclusion** (expressing withdrawn consent when a party is removed or when an existing member objects to a new admission), and (b) **hygiene** (routine, semantically neutral). Rotation is never used to *admit* a party.
4. **Write authority is a separate axis, and it is cryptographically enforceable.** Peers accept a device's signed bundles only when that device's `device_link` / membership cert chains back to something they already trust. Refusing to accept a newly-admitted party's signatures is an independent lever from refusing to distribute fresh sender keys to them.
5. **Read-objection and write-objection are independent.** A peer can rotate-excluding a new party (read objection) without also refusing their signatures (write objection), or vice versa, or both. The UI should expose these as separate choices.

The proxy-anyway attack remains possible under this model. What changes is that it is no longer passive protocol fiction — it requires the admitter to actively violate an explicit rotation decision by a peer, and it is detectable in principle (the excluded party shouldn't be able to respond coherently to post-rotation traffic they can't decrypt on their own).

## Why This Frame

The strongest argument against the current "Bob must redistribute his sender key to B before B can honestly read Bob's traffic" rule is that it cannot survive contact with endpoint trust. If Alice already has Device A with read access and she links Device B, nothing in the protocol prevents A from copying plaintext or receiver state to B. The ceremony describing B as "not yet a legitimate reader" is therefore performative, not a real confidentiality boundary.

The same argument applies symmetrically to add-member: if Alice invites Carol and Alice is willing to proxy, Bob's refusal to redistribute his sender key to Carol does not prevent Carol from reading Bob's current traffic. What it *does* prevent is Carol having a durable, independent capability on Bob's future traffic. That is a real guarantee, but its scope is narrower than the protocol has been claiming.

The reframe makes those narrower guarantees explicit and keeps them where the cryptography can actually enforce them (rotation-on-exclusion, cert-chain-validated sends), while dropping the parts that were never enforceable (admission-time read confidentiality against the admitter).

## Strongest Counter-Argument Considered

Formal confidentiality analysis is cleaner when the written protocol property is "only explicitly-admitted readers can decrypt." Embracing endpoint reality means the formal property becomes "honest endpoints within an admitted identity share read access," which is a softer statement.

This branch rejects that counter-argument for the following reasons: Small Sea is pre-alpha, user-facing, and targeted at real collaboration rather than formal-verification deliverables; the softer property is the true one, and writing the false-but-cleaner one down does not make it enforceable; and keeping the stricter language degrades the spec by making it describe a fiction. If a later formal-analysis workstream needs a tighter model, it can describe the rotation-on-exclusion guarantee precisely.

## What This Simplifies

- **#69 (linked-device bootstrap) collapses.** The joining device B is bootstrapped from a sibling device A by receiving (i) the team's current state, (ii) A's copies of the peer sender keys A already holds, (iii) material for B to publish its own device key / sender key. No round-trip to every other team member is required for B to become a legitimate reader. Join-time-forward becomes the honest and natural historical-access policy.
- **Payload 3 in the current #69 flow changes role.** It is no longer "B reporting back so the authorizing sibling can tell peers to admit B." It becomes B's own sender-key publication to peers, which is structurally identical to any other device's sender-key publication and goes over the same redistribution primitive that #43 already delivered.
- **`test_linked_device_bootstrap_requires_real_redistribution_for_other_senders` becomes wrong in spirit.** Under the new model, B *should* be able to read future traffic from Bob using the peer sender key A already has. That test needs to be replaced with one that asserts what is actually protocol-enforceable (e.g., Bob can *object* to B by rotating with exclusion, and after that rotation B cannot decrypt Bob's new traffic without A's active proxying).
- **Invitation flow language straightens out.** The acceptance handshake stops describing a read-confidentiality event it was not actually performing. Invitation becomes: inviter hands the invitee what they need; existing peers learn about the new member and can object by rotating or by refusing signatures, independently.

## What Survives Unchanged

- **#43 rotation-on-removal stays correct and load-bearing.** This is the one place where the cryptography really does enforce a confidentiality boundary (against an ex-member after the honest-majority has rotated excluding them). The implementation landed in the `issue-43-sender-key-rotation` archive plan does not need to be redone; its justification just narrows from "rotation enforces all membership-change confidentiality" to "rotation enforces exclusion-on-removal confidentiality."
- **The `redistribute_sender_key(...)` primitive stays as-is.** Its callers change (see below), but the primitive — encrypted pairwise distribution of a fresh sender key to a chosen set of target devices — is exactly what both "object to new admission" and "hygiene rotation" need.
- **`device_prekey_bundle` publication, X3DH, and Double Ratchet wiring stay.** These are the substrate for any pairwise control-plane operation; the reframe does not change the substrate.
- **Cert-chain validation for write authority stays.** `device_link` and membership certs continue to gate whether a peer accepts a device's signed bundles as legitimate team traffic.

## Proposed GitHub Issue Deltas

*(These are plans for the implementation phase of this branch. No GitHub changes happen until the decision is accepted.)*

### Keep (no change)

- **#43** — already aligned with the new frame once its justification is reread as "rotation-on-exclusion." No scope change needed.
- **#44** — sender-key storage revisit; orthogonal to this reframe.
- **#48** — Manager multi-device NoteToSelf sync and team discovery; orthogonal (sits above read/write access design).
- **#6** — identity model question; the reframe refines one of its open questions but does not resolve the whole issue.
- **#4** — Cuttlefish integration; orthogonal at the integration layer.

### Modify

- **#69** — rescope. Drop the "Bob must redistribute to B" framing entirely. Reframe as: "bootstrap B by sibling handoff of everything B needs (team state + A's peer sender keys + B's publication material), with join-time-forward historical access." The existing same-member bootstrap code is largely salvageable; the parts to remove are the payload-3-as-admission semantics and the "every sender redistributes to B" deferred-follow-up language.
- **#59** — rescope. Device-aware sender keys and peer routing remain in scope, but the "when a new linked device appears, every member must redistribute to it" trigger goes away. What #59 still owns: device-scoped sender keys on the send side, peer-table device dimension, per-device Hub routing, and the watch behavior that surfaces new device-link events (now for the purpose of *objection opportunity*, not admission orchestration).
- **#73** (periodic sender-key rotation policy) — keep, but clarify that periodic rotation is the "hygiene" bucket, never the "admission" bucket. The frame is already compatible; this is a doc-only clarification on the issue.

### Close As Superseded

- Any open issue whose scope was specifically "distribute sender keys to newly linked devices from every other sender" should be closed as superseded by this reframe. Candidate identification happens during the implementation phase, not here — the inventory needs a fresh pass through the open issue list rather than a memory-based recollection.

### Add

- **New issue — "Specify read-objection and write-objection as independent UI/protocol axes."** Scope: the UI/UX and the protocol-level representation of a peer's choice to (a) rotate-excluding a newly admitted party, (b) refuse that party's signatures, or (c) both. Today these are tangled implicitly in the membership-change story.
- **New issue — "Replace the historical-access test for same-member linked-device bootstrap."** Scope: retire `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders`; add a test that B can read Bob's current traffic post-bootstrap using A's copy of Bob's sender key, and a test that Bob rotating-with-exclusion cuts B off from Bob's subsequent traffic.
- **New issue — "Spec and architecture doc sweep for endpoint-truth language."** Scope: spec.md and architecture.md both contain language describing a read-confidentiality boundary the reframe says does not exist. Sweep and rewrite.
- **New issue — "Linked-device bootstrap: sibling hands off peer sender-key snapshot."** Scope: extend the `create_linked_device_bootstrap` bundle to include A's current copies of peer sender keys, so B can read other senders' current traffic without any other member's participation. Probably the first implementation branch after this meta-plan lands.

## Branch-Sized Chunks Of Follow-Up Work

Each is a sketch. None of these gets a full branch plan in this branch; each gets its own when it starts.

### B1. Doc sweep: endpoint-truth rewrite

Touch `architecture.md`, `packages/small-sea-manager/spec.md`, `Documentation/open-architecture-questions.md`, and `packages/cuttlefish/README.md` (at least the Sender Keys / Web of Trust sections). Replace read-confidentiality language that asserts a protocol-enforceable boundary with the endpoint-truth framing. Add explicit paragraphs on rotation's two purposes (exclusion, hygiene) and on read-vs-write objection as independent axes. No code changes.

**Why first:** subsequent implementation branches will cite the updated docs as their reference. Inverting the order risks code branches inventing their own framings.

### B2. Linked-device bootstrap: sibling peer-sender-key handoff

Extend the bootstrap bundle produced by `create_linked_device_bootstrap(...)` to include A's current snapshot of peer sender keys. Update `finalize_linked_device_bootstrap(...)` to store those as local `peer_sender_key` rows on B. Retire the "B cannot read Bob's future traffic until Bob redistributes" boundary and replace the test asserting it.

**Why:** this is the smallest code branch that realizes the reframe's main user-facing simplification of #69.

### B3. Payload-3 reframe: B's own sender-key publication via the standard redistribution primitive

Replace the current "B returns a sender distribution payload to the authorizing sibling, which then tells each peer to admit B" path with "B uses `redistribute_sender_key(...)` with target set = all peer devices in the team" once bootstrap is complete. Peers process it through the existing `receive_sender_key_distribution(...)`. No special admission ceremony.

**Why:** collapses a bespoke path into the primitive #43 already proved. Reduces code volume and removes the conceptual duplicate.

### B4. Read-objection / write-objection as explicit UI + protocol concepts

Specify and implement the two independent levers: "when a new member or device appears, I want to rotate-excluding them" and "when a new member or device appears, I will refuse their signatures." Today the Manager has no UI for either, and the cert-refusal side of the write-objection axis is implicit in cert-chain evaluation logic that happens at verification time.

**Why:** the reframe names these as first-class. Without explicit UI they exist only as rotation side effects and silent cert-verification refusals.

### B5. Peer-discovery watch behavior for admission visibility

Make sure that when a new `device_link` cert or a new member appears in the team DB, every existing peer device surfaces that event with enough time and context for the user to decide whether to object. Today this is covered only loosely in #59's peer-routing-watches work. The reframe raises its importance: if admission-time read confidentiality against the admitter was fiction, observability-and-objection is the real consent mechanism, and its latency bounds matter.

**Why:** an objection window with no mechanism to reach the user is as weak as the old fiction. This chunk can likely reuse most of #59's watch infrastructure.

### B6. Issue hygiene: close superseded, retitle rescoped, add new

The GitHub actions listed in "Proposed GitHub Issue Deltas" above. This is a separate chunk because it is a one-shot administrative task that should happen after B1 so the rescoped issues can point at updated docs.

## Documentation Changes (Scoped Here, Executed In Implementation Phase)

- `architecture.md` §"Fully Decentralized Team Management": rewrite the paragraph on key rotation to reflect exclusion + hygiene as the two purposes. Drop any implication that rotation gates admission.
- `packages/small-sea-manager/spec.md` §"Linked-device team bootstrap": rewrite the "Historical boundary and visibility" bullets and the "Scope of the current slice" boundary statement. Remove the "not yet redistributed by every other sender" framing.
- `Documentation/open-architecture-questions.md` §5 "Identity Model": add a settled-decisions subsection citing the reframe. Move "what happens to encrypted data if a device is lost" into the exclusion/rotation framing explicitly.
- `packages/cuttlefish/README.md` sender-keys and trust sections: check and reconcile. (Scope-confirm in B1; may only need light edits.)

## Validation (For This Meta-Plan Branch)

This branch's deliverable is this document. It is "done" when a skeptical reviewer can read it and confirm:

1. The decision at the top is stated precisely enough that a later branch can be measured against it. A reviewer who adopts the decision should be able to resolve downstream design ambiguities by pointing at section "The Decision To Accept Or Reject."
2. The frame is distinguished from what it replaces, and the strongest counter-argument is written down rather than skipped.
3. Each named follow-up chunk has a one-paragraph scope that is clearly smaller than a re-plan of the whole reframe. No chunk silently imports a full #69-scale redesign.
4. The GitHub issue deltas are executable: each "modify / close / add" entry names what specifically changes, not just which issue is touched.
5. No code is changed on this branch; no GitHub state is changed on this branch. Both are deferred to the implementation phase, per the agreed scope of a meta-plan.

## Out Of Scope For This Branch

- Writing per-branch plans for B1–B6. Each chunk gets its own branch and its own `branch-plan.md` when started.
- Touching code in `provisioning.py`, tests, or schemas.
- Opening, closing, or editing GitHub issues.
- Deciding the order of B1–B6 beyond the "B1 first, B6 after B1" constraint noted above. Final sequencing happens when the decision is accepted and work starts.
- Producing a formal-model writeup of the new trust model. If that becomes useful, it is a separate research branch.

## Skeptic-Facing Wrap-Up

A reviewer accepting this meta-plan should be able to answer:

1. What exactly is the decision being asked for? *Accept or reject the five-point model in "The Decision To Accept Or Reject."*
2. What changes in the codebase if this is accepted? *Nothing immediately; B1–B5 are the planned code-touching follow-ups, each as its own branch.*
3. What existing work becomes obsolete? *The admission-flavored parts of #69's design and its "requires real redistribution for other senders" test. The implementation code is largely reusable.*
4. What existing work stays correct? *#43's rotation-on-removal mechanics and the redistribution primitive. `device_link` cert chaining for write authority.*
5. Where is the strongest counter-argument addressed? *Section "Strongest Counter-Argument Considered."*
6. What is not decided here? *Final sequencing of B1–B6; whether any currently-open issue should be closed as fully superseded (requires a fresh inventory pass during implementation); formal-model writeup.*
