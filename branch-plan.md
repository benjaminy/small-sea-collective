# Branch Plan: Linked-Device Bootstrap — Sibling Handoff + Payload-3 Reframe

**Branch:** `issue-69-linked-device-bootstrap`
**Base:** `main`
**Primary issue:** #69 "Bootstrap encrypted team access for a newly linked device"
**Also closes:** #101 "historical-access test replacement"
**Kind:** Implementation branch. Code + micro tests.
**Related prior plans:** `Archive/branch-plan-issue-97-trust-domain-reframe.md` (B3 + B4)
**Related docs:** `packages/small-sea-manager/spec.md` (§"Linked-device team bootstrap")
**Related code:** `packages/small-sea-manager/small_sea_manager/provisioning.py`, `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`

## Purpose

Two follow-up chunks from the accepted trust-domain reframe land together here:

- **B3** — The authorizing sibling now includes its full snapshot of peer sender-key receiver state in the bootstrap bundle. The new device can read current and future peer traffic from the moment of bootstrap without waiting for each sender to redistribute.
- **B4** — Payload-3 is retired as a bespoke OOB exchange. The new device publishes its own sender key via the standard `redistribute_sender_key(...)` primitive after finalization, just like any other member.

Both changes are expressible in the same set of files and belong in the same branch.

## Current State

`create_linked_device_bootstrap` encrypts only the sibling's own sender distribution through X3DH + ratchet. `finalize_linked_device_bootstrap` decrypts it, saves it as a peer record, and returns a `sender_distribution_payload` (payload-3) for the new device's own sender key. `complete_linked_device_bootstrap` on the sibling side consumes payload-3 and stores the new device's sender key as a peer record.

`test_linked_device_bootstrap_requires_real_redistribution_for_other_senders` explicitly asserts that after bootstrap the new device cannot read Bob's sender traffic — this test was correct under the old model and is wrong in spirit under the accepted reframe.

The spec §"Linked-device team bootstrap" already describes the accepted design with a "B3 scope" implementation-status note marking the gap.

## Scope

**In scope:**

- Extend the ratchet plaintext in `create_linked_device_bootstrap` to include all peer sender distributions from the sibling's local DB
- Update `finalize_linked_device_bootstrap` to deserialize and store all peer sender distributions; retire the payload-3 return value
- Retire `complete_linked_device_bootstrap` (or hollow it out to a no-op stub with a deprecation note)
- Update pending-breadcrumb lifecycle: clear at end of `create_linked_device_bootstrap` (after bundle is written) rather than in `complete`
- Retire `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders`
- Update `test_linked_device_bootstrap_round_trip_same_member` for the new flow (no payload-3 step; new device calls `redistribute_sender_key`, sibling receives via `receive_sender_key_distribution`)
- Add test: new device can read current and future peer traffic after sibling handoff
- Add test: later rotate-with-exclusion by another sender cuts the new device off from that sender's subsequent traffic
- Retire/update idempotency tests for `complete_linked_device_bootstrap` (retire the test; the complete function is no longer part of the live flow)
- Spec update: remove the implementation-status note, update the step list, update the payload-3 transport status note to describe redirect to `redistribute_sender_key`

**Out of scope:**

- Hub-mediated redistribution delivery (tests simulate delivery by calling `receive_sender_key_distribution` directly)
- Periodic sender-key rotation policy (#73)
- Invitation-flow rework (B5 / #98)
- Broad new-teammate admission governance

## Design Decisions

### 1. Ratchet plaintext format change

The plaintext encrypted in `create_linked_device_bootstrap` changes from a bare serialized distribution message to a JSON envelope:

```json
{
  "own_sender_distribution": { ... },
  "peer_sender_distributions": [ { ... }, { ... } ]
}
```

`own_sender_distribution` carries the sibling's own distribution (existing behavior). `peer_sender_distributions` carries all peer distributions loaded from the sibling's `device_local` DB for this team, serialized in the same `serialize_distribution_message(...)` format.

`finalize_linked_device_bootstrap` deserializes both fields. It saves each peer distribution via `save_peer_sender_key(...)` → `receiver_record_from_distribution(...)`, same as it currently saves the sibling's own distribution.

This is a wire-format break between the old and new bootstrap bundles. Any pending bootstrap bundle created before this branch lands is incompatible. That is acceptable for a pre-alpha codebase. The branch plan should note the break explicitly in the spec.

### 2. Payload-3 retired; new device calls `redistribute_sender_key`

`finalize_linked_device_bootstrap` no longer returns `sender_distribution_payload`. Its return value becomes `{"bootstrap_id_hex": "..."}` (success signal).

After finalize, the calling code (Manager API or test) is responsible for calling `redistribute_sender_key(...)` to publish the new device's sender key to all other trusted devices. This is the standard redistribution path used by any member who rotates or initializes a sender key.

`complete_linked_device_bootstrap` is retired. The function body is replaced with a `raise NotImplementedError` or removed entirely. All call sites in tests are updated.

### 3. Pending-breadcrumb lifecycle

The pending bootstrap breadcrumb on the sibling side is currently cleared by `complete_linked_device_bootstrap`. With complete retired, it is cleared at the end of `create_linked_device_bootstrap` once the bundle has been committed to the pending breadcrumb table (i.e., right before return). The store-and-replay idempotency for a repeated create call with the same join request is preserved: if a breadcrumb already exists, the stored bundle is returned immediately, and then the row is not re-cleared since it's already gone.

Wait — if we clear after writing the breadcrumb, the row would be gone immediately. That breaks the store-and-replay check: a second call to create with the same join request would find no breadcrumb and issue a fresh cert. This is a behavior change.

**Resolution:** Keep the breadcrumb row alive until the new device's cert is visible in the team DB git log (too complex), OR accept that the create store-and-replay now only works if the two create calls happen within the same invocation (not useful). **Simplest viable option:** clear the breadcrumb in `create_linked_device_bootstrap` by leaving clearing as a separate, lower-priority cleanup step and removing the `_row_count` assertions from the round-trip test that currently check pending breadcrumb count. The idempotency test for `create_replay` is kept as-is since that test only cares that the stored bundle is returned, not that the breadcrumb is then cleared.

Concretely:
- Remove the `assert _row_count(...pending_linked_team_bootstrap...) == 0` assertion from `test_linked_device_bootstrap_round_trip_same_member` (that check was contingent on `complete` clearing it)
- The create-replay idempotency test is unaffected since it doesn't check the count after the fact
- Leave a TODO comment in `create_linked_device_bootstrap` noting that breadcrumb clearing belongs to a future cleanup pass

### 4. `redistribute_sender_key` requires sibling's prekey bundle on new device

After `finalize_linked_device_bootstrap`, the new device calls `redistribute_sender_key(...)` to send its own sender key to all other trusted devices. This requires that the team DB on the new device contains prekey bundles for all recipients (so the redistribution can be X3DH-encrypted per recipient).

In the current test setup, `_copy_team_baseline` copies the team sync dir from root1 to root2 before bootstrap. After finalize, the new device's own prekey bundle is published to the team DB (already done in finalize via `_publish_local_device_prekey_bundle`). The sibling's prekey bundle was present in the copied baseline. So `redistribute_sender_key` on the new device (root2) should produce at least one artifact targeting the sibling.

The sibling then calls `receive_sender_key_distribution(...)` with that artifact to store the new device's sender key as a peer record. In tests this is done directly; in production this would go through Hub.

### 5. Join-time-forward policy is now real, not aspirational

After this branch, the new device genuinely inherits the sibling's peer sender-key snapshot and can decrypt current and future traffic from those senders without any sender needing to take an additional step. This matches the accepted trust-domain reframe and should be stated plainly in the spec without qualifications about future redistribution requirements.

## Expected Change Areas

### `packages/small-sea-manager/small_sea_manager/provisioning.py`

- `create_linked_device_bootstrap`: change plaintext to the JSON envelope; load all peer sender distributions from local DB and include them in `peer_sender_distributions`
- `finalize_linked_device_bootstrap`: parse the new envelope; save own + peer distributions; remove payload-3 return, return `{"bootstrap_id_hex": "..."}`
- `complete_linked_device_bootstrap`: retire (raise NotImplementedError or remove)

### `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`

- Remove `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders`
- Update `test_linked_device_bootstrap_round_trip_same_member`:
  - Remove `complete_linked_device_bootstrap` call
  - Add: new device calls `redistribute_sender_key`; sibling calls `receive_sender_key_distribution` with the artifact
  - Assert sibling now has new device's sender key (via redistribution, not payload-3)
  - Remove `assert _row_count(...pending_linked_team_bootstrap...) == 0`
- Add `test_linked_device_bootstrap_peer_sender_keys_transferred`: new device can read current/future Bob traffic after sibling handoff without Bob doing anything
- Add `test_linked_device_bootstrap_exclusion_cuts_off_peer`: after sibling handoff gives new device Bob's sender key, Bob calls `rotate_team_sender_key` + `redistribute_sender_key` (excluding new device), and new device cannot decrypt post-rotation Bob traffic
- Retire `test_linked_device_bootstrap_retry_after_interrupted_complete_is_idempotent` (complete no longer exists)
- Other idempotency tests (`create_replay`, `finalize_retry`) are unchanged or minimally updated

### `packages/small-sea-manager/spec.md`

- Remove the B3 implementation-status note
- Update the step list: finalize no longer emits payload-3; add step "new device calls `redistribute_sender_key(...)` to publish its own sender key to all trusted peers"
- Remove step 4 (`complete_linked_device_bootstrap`) or replace it with the redistribution receive step
- Update the payload-3 transport status note to say payload-3 is retired; new device uses standard redistribution
- Update the "join-time-forward" description to remove the "interim state" hedging; the handoff is now real

## Validation

Done when a skeptical reviewer can confirm every item below from code or tests:

1. `create_linked_device_bootstrap` encodes both own and peer sender distributions in the bootstrap bundle.
2. `finalize_linked_device_bootstrap` stores all peer sender distributions from the bundle.
3. A newly bootstrapped device can decrypt a current (post-bootstrap) message from a remote sender (Bob) without Bob taking any additional step.
4. If Bob later rotates-with-exclusion targeting the new device, the new device cannot decrypt Bob's post-rotation traffic.
5. After finalize, the new device calls `redistribute_sender_key(...)` and the sibling can receive the new device's sender key via `receive_sender_key_distribution(...)`.
6. `complete_linked_device_bootstrap` is no longer part of the live bootstrap flow (function removed or raises NotImplementedError).
7. `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders` is gone.
8. The spec §"Linked-device team bootstrap" accurately describes the implemented flow with no B3-scope hedging.

## Concrete Micro Tests To Expect

1. **Existing `test_linked_device_bootstrap_round_trip_same_member` updated**: same-member round trip works without payload-3; sibling receives new device's sender key via redistribution.
2. **New `test_linked_device_bootstrap_peer_sender_keys_transferred`**: setup has Bob as a remote sender with a sender key on the sibling; after sibling bootstrap, new device can decrypt a Bob message that was encrypted post-bootstrap.
3. **New `test_linked_device_bootstrap_exclusion_cuts_off_peer`**: Bob rotates-excluding new device; new device cannot decrypt Bob's post-rotation messages; the pre-exclusion session is still intact (new device can still decrypt the pre-rotation Bob message that arrived during the handoff window).
4. **Existing create-replay idempotency test unchanged**: calling create twice with the same join request still returns the stored bundle.
5. **Existing finalize-retry idempotency test still passes** (or is updated minimally if the return shape changes).

## Out Of Scope

- Revocation-cert infrastructure for signer trust
- Multi-team bootstrap automation
- Hub-mediated redistribution delivery path
- Admission-event visibility for device_link certs (#99, already closed)
- Invitation-flow transcript binding (B5 / #98)
- Periodic sender-key rotation (#73)

## Wrap-Up Notes

When the branch is complete:

1. Update this plan to reflect what actually landed, especially whether any pending-breadcrumb behavior was changed beyond the documented decision.
2. Archive it as `Archive/branch-plan-issue-69-linked-device-bootstrap.md`.
3. Close issues #69 and #101.
4. Note any follow-up work: e.g., pending-breadcrumb cleanup, Hub-mediated redistribution delivery path.
