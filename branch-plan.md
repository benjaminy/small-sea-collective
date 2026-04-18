# Branch Plan: Linked-Device Bootstrap Reframe

**Branch:** `issue-69-linked-device-bootstrap`  
**Base:** `main`  
**Primary issue:** #69 "Bootstrap encrypted team access for a newly linked device"  
**Also closes:** #101 "historical-access test replacement"  
**Kind:** Implementation branch. Code + spec + micro tests.  
**Related prior plan:** `Archive/branch-plan-issue-97-trust-domain-reframe.md`  
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`  
**Primary code area:** `packages/small-sea-manager/small_sea_manager/provisioning.py`  
**Primary micro tests:** `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`

## Purpose

Issue #69 is the implementation follow-up to the trust-domain reframe accepted in #97.

The old linked-device flow still treats readability from other senders as if it only becomes legitimate after each sender separately redistributes to the new device. That is the wrong model for a same-member sibling bootstrap. If Alice's existing device can already read Bob's traffic, that device can hand Alice's new device the receiver state it already holds. The honest bootstrap should model that reality directly.

This branch should therefore make linked-device bootstrap do exactly three things:

- let an already-readable sibling hand off the current team-readable state it already possesses
- let the newly linked device read join-time-forward traffic immediately after bootstrap finalization
- move the new device's own sender-key publication onto the normal `redistribute_sender_key(...)` path instead of a bespoke "payload 3" ceremony

## Repo Context This Plan Assumes

- Small Sea is local-first and pre-alpha; clean protocol shape is preferred over compatibility shims.
- Linked-device bootstrap is a **same-member** flow, not general teammate admission.
- Read access is **endpoint-trust-scoped**, not a cryptographic boundary between sibling devices.
- All internet transport concerns remain out of scope here; this branch is about local bootstrap state and protocol shape inside Manager.
- The current implementation seam is:
  `prepare_linked_device_team_join(...)` ->
  `create_linked_device_bootstrap(...)` ->
  `finalize_linked_device_bootstrap(...)` ->
  `complete_linked_device_bootstrap(...)`
- The current implementation already requires a pre-existing team baseline on the joining device (`Payload 0 prerequisite` in the spec and `_copy_team_baseline(...)` in tests). That remains true in this branch.

## Current State

Today the authorizing sibling encrypts only its **own** sender distribution into the bootstrap bundle. On the new device, `finalize_linked_device_bootstrap(...)` stores that as a peer sender key, initializes the new device's own sender key, and returns a bespoke follow-up artifact. The sibling then consumes that artifact in `complete_linked_device_bootstrap(...)` and stores the new device's sender key.

That leaves a gap relative to issue #69:

- the new device does **not** receive peer sender-key receiver state already held by the sibling
- the test suite still encodes the old assumption that Bob must redistribute before the new device can honestly read Bob's traffic
- the flow still has a dedicated payload-3 handoff instead of using ordinary sender-key redistribution

## Branch Outcome

When this branch is done, the linked-device bootstrap flow for one team should be:

1. The joining device prepares a join request exactly as it does now.
2. The authorizing sibling issues the `device_link` cert and encrypts a bootstrap bundle containing:
   - the sibling's own sender distribution
   - the sibling's current snapshot of peer sender-key receiver state for this team
3. The joining device finalizes bootstrap, stores that handed-off receiver state locally, publishes its Team-X prekey bundle as it already does today, and returns a simple success result.
4. After finalization, the joining device publishes its own sender key through `redistribute_sender_key(...)`.
5. Other devices, including the authorizing sibling in micro tests, receive that redistribution through the normal `receive_sender_key_distribution(...)` path.

The special-purpose `complete_linked_device_bootstrap(...)` step is no longer part of the live protocol.

## In Scope

- Change the bootstrap plaintext so it can carry peer sender-key handoff, not just the authorizer's own sender distribution.
- Update finalize so it stores the sibling's peer snapshot on the new device.
- Retire payload-3 as a bespoke protocol step.
- Update the spec to describe linked-device bootstrap as a join-time-forward sibling handoff.
- Replace the obsolete historical-access micro test with tests that prove the new model.
- Keep create/finalize retry behavior solid enough to satisfy a skeptical reviewer.

## Out Of Scope

- Team discovery or baseline delivery for the joining device
- Hub-mediated delivery of redistributions
- Invitation / teammate-admission governance
- Periodic sender-key rotation policy (#73)
- Steady-state watch/routing behavior beyond the bootstrap seam (#59)
- Backward-compatible handling of older bootstrap bundles

## Design Decisions

### 1. Treat sibling handoff as the honest bootstrap primitive

The branch should implement the issue's intended trust model directly: the sibling hands off the receiver state it already has. We should not preserve test or API shape that implies "real readability" only appears after sender-by-sender redistribution.

This means the bootstrap bundle must include more than the sibling's own sender distribution. It must also carry the sibling's snapshot of peer sender-key state already present in local device storage for that team.

### 2. Use a structured bootstrap envelope

The ratchet-encrypted plaintext in `create_linked_device_bootstrap(...)` should become a JSON envelope rather than a single serialized distribution message.

Planned shape:

```json
{
  "own_sender_distribution": { "...": "..." },
  "peer_sender_distributions": [
    { "...": "..." }
  ]
}
```

`own_sender_distribution` preserves current behavior for the authorizing sibling's active sender stream. `peer_sender_distributions` contains serialized distributions reconstructed from the sibling's locally stored peer receiver records for the same team.

This is a deliberate wire-format break for pending old bootstrap bundles. That is acceptable in this pre-alpha branch and should be stated plainly in the spec.

### 3. Finalize becomes the end of bootstrap

`finalize_linked_device_bootstrap(...)` should:

- verify the bundle as it does now
- decrypt the envelope
- store the authorizer's sender distribution as a peer record
- store every handed-off peer distribution as a peer record
- publish the new device's Team-X prekey bundle as it already does today
- return a simple success payload such as `{"bootstrap_id_hex": ...}`

It should no longer emit `sender_distribution_payload`.

### 4. The new device's own sender key publication should use normal redistribution

After finalize, the new device should call `redistribute_sender_key(...)`. In tests, the returned artifacts can be passed directly into `receive_sender_key_distribution(...)` on the sibling side. This keeps the post-bootstrap behavior aligned with the standard sender-key machinery already used elsewhere in the codebase.

This branch should therefore remove the live role of `complete_linked_device_bootstrap(...)`. Because the repo is pre-alpha, the preferred outcome is to remove it from the flow entirely rather than preserve it for compatibility. If deleting it is noisier than helpful in this branch, a temporary `NotImplementedError` is acceptable, but the plan should treat that as a cleanup compromise, not the target design.

### 5. Do not let breadcrumb cleanup dictate protocol shape

The current draft plan was right to notice a problem: create-side replay and complete-side cleanup are coupled today. Once payload-3 is retired, that coupling no longer makes sense.

The clean branch goal is:

- keep authorizer-side replay/idempotency for repeated `create_linked_device_bootstrap(...)` calls with the same bootstrap id
- stop treating the pending row as proof that a special "complete" step still exists

This branch should prefer the smallest design that preserves replay semantics without reintroducing a fake protocol stage. Concretely, that means:

- do **not** move the protocol back toward payload-3 just to have a cleanup trigger
- do **not** weaken replay guarantees to paper over the cleanup question
- allow the pending bootstrap record to remain a create-side replay artifact for now, with cleanup either omitted or handled by a narrower follow-up if needed

If implementation reveals an even cleaner approach, it is acceptable, but the rewrite should explicitly avoid the earlier plan's muddled "clear it here, but maybe not really" state.

### 6. Join-time-forward means "current and future from the handed-off snapshot"

The new device should still be unable to read historical ciphertext from before the relevant sender-key state existed on that device. But once the sibling has handed off the current receiver snapshot, the new device should be able to read traffic encrypted after bootstrap finalization by those already-known senders.

The real enforced boundary remains later rotation with exclusion. The micro tests and spec should make that visible.

## Expected Change Areas

### `packages/small-sea-manager/small_sea_manager/provisioning.py`

- `create_linked_device_bootstrap(...)`
  - load peer sender-key records from local device storage for the team
  - serialize them into the encrypted bootstrap envelope
- `finalize_linked_device_bootstrap(...)`
  - parse the new envelope
  - store the handed-off peer sender-key state locally
  - remove bespoke payload-3 output
- `complete_linked_device_bootstrap(...)`
  - remove, or reduce to an explicit non-live stub if deletion is too disruptive for this branch

### `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`

- update the same-member round-trip micro test to use normal redistribution after finalize
- replace the obsolete "requires real redistribution for other senders" micro test
- add positive proof that handed-off peer state makes immediate join-time-forward readability real
- add negative proof that later rotate-with-exclusion still cuts the new device off
- retire complete-step idempotency coverage, or replace it with coverage around the new live flow

### `packages/small-sea-manager/spec.md`

- remove the B3 implementation-status hedging
- describe sibling handoff of peer sender-key state as the implemented design
- describe payload-3 as retired in favor of standard redistribution
- keep the baseline-clone prerequisite explicit

## Micro Test Plan

### 1. Update the existing same-member round-trip micro test

`test_linked_device_bootstrap_round_trip_same_member`

This should prove:

- bootstrap finalization succeeds without `complete_linked_device_bootstrap(...)`
- the new device can read new traffic from the authorizing sibling
- the new device can publish its own sender key through `redistribute_sender_key(...)`
- the sibling can receive that distribution through `receive_sender_key_distribution(...)`
- both sides can then exchange fresh traffic normally

### 2. Replace the obsolete historical-access test with a positive sibling-handoff test

Replace `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders`
with a micro test that sets up Bob as another sender already readable by the authorizing sibling.

This should prove:

- before bootstrap, the sibling can read Bob's traffic
- after bootstrap finalization, the new device has Bob's handed-off receiver state
- Bob can send a new post-bootstrap message and the new device can decrypt it without Bob taking any additional action

### 3. Add an exclusion-boundary micro test

Add a micro test showing that the honest sibling handoff does **not** erase the real boundary created by later rotation.

This should prove:

- after bootstrap, the new device can read Bob's current traffic
- Bob later rotates and redistributes while excluding the new device
- the new device cannot decrypt Bob's post-rotation traffic

### 4. Preserve retry/idempotency coverage for the live stages

At minimum, keep strong micro test coverage for:

- create replay returning the stored bundle without extra commit churn
- finalize retry remaining idempotent after an interrupted finalize path

If code changes alter return shapes, the assertions should change accordingly, but the behavior guarantee should remain.

## Validation

The branch is done when a skeptical reviewer can verify all of the following from code, spec, and micro tests:

1. The bootstrap bundle now carries both the authorizer's own sender distribution and the sibling's snapshot of peer sender-key state for the team.
2. `finalize_linked_device_bootstrap(...)` stores that handed-off peer state on the joining device.
3. A newly linked device can decrypt post-bootstrap traffic from another sender whose receiver state was handed off by the sibling, without any extra action by that sender.
4. The new device still cannot decrypt excluded post-rotation traffic after that sender rotates them out.
5. The new device's own sender-key publication happens through `redistribute_sender_key(...)`, not a bespoke payload-3 return artifact.
6. `complete_linked_device_bootstrap(...)` is no longer part of the live linked-device bootstrap flow.
7. The old micro test encoding the outdated trust model is gone or rewritten to assert the new behavior.
8. `packages/small-sea-manager/spec.md` describes the implemented flow accurately, including the baseline prerequisite and the new post-finalize redistribution step.
9. The branch does not introduce broader coupling or transport assumptions outside Manager's local bootstrap logic.

## Skeptic-Facing Integrity Checks

A smart skeptic reviewing this branch should be able to answer "yes" to both categories below.

### Goals accomplished

- Does the code now implement the issue's honest same-member bootstrap model rather than the obsolete sender-by-sender-admission model?
- Do the micro tests prove both the positive case (handoff grants immediate join-time-forward readability) and the negative case (later exclusion still bites)?

### Repo integrity maintained or improved

- Is the protocol shape simpler after the branch, with less bespoke bootstrap-only machinery?
- Did we reuse existing sender-key redistribution primitives instead of creating another parallel path?
- Does the spec now match the code more closely than before?
- Are retry/idempotency guarantees still covered for the stages that remain live?

## Wrap-Up Notes

When implementation is complete:

1. Update this plan to reflect what actually landed.
2. Move it to `Archive/branch-plan-issue-69-linked-device-bootstrap.md`.
3. Close #69 and #101 if the shipped behavior and tests match the validation section.
4. Record any true follow-up work separately instead of leaving protocol ambiguity in this plan.
