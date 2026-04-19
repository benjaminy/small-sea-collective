# Branch Plan: Skipped-Message Key Bootstrap Transfer (Issue #107)

**Branch:** `issue-107-skipped-message-key-bootstrap`
**Base:** `main`
**Primary issue:** #107 "Linked-device bootstrap does not transfer skipped-message caches for peer sender keys"
**Kind:** Implementation branch. Code + micro tests + narrow spec updates.
**Related issues:** #69 (linked-device bootstrap), #97 (trust-domain reframe / accepted read-boundary model)
**Related docs:** `README.md`, `architecture.md`, `packages/small-sea-manager/spec.md`
**Related code of interest:**
- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `packages/small-sea-manager/small_sea_manager/sender_keys.py`
- `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`

## Purpose

Today, linked-device bootstrap hands a new sibling device the sibling's current
peer sender-key chain positions, but it drops any cached
`skipped_message_keys`. That means the new device does **not** actually inherit
the full peer receiver state the sibling had at bootstrap time. If the
authorizing sibling had already cached skipped keys because messages arrived out
of order, the bootstrapped device silently loses the ability to decrypt those
specific pending messages.

That behavior is now out of step with the architecture direction already
documented in `architecture.md`: linked-device admission is a unilateral
identity-owner handoff of whatever receiver state the sibling currently holds.
The gap is not a fundamental trust boundary; it is a serialization omission in
the bootstrap payload.

This branch fixes that omission cleanly, keeps the existing join-time-forward
model, and updates repo-local protocol evidence so the code, tests, and spec
all describe the same behavior.

## Decision

We should treat skipped-message caches as part of the peer receiver-state
snapshot transferred during linked-device bootstrap.

Concretely:

1. The bootstrap plaintext gains a parallel `peer_sender_skipped_keys` field
   alongside `peer_sender_distributions`.
2. `SenderKeyDistributionMessage` remains unchanged. It still carries only the
   current sender-chain state.
3. The new field is bootstrap-local glue, not a protocol redesign and not an
   ongoing sync channel.
4. The semantic boundary remains: the new device gets whatever the sibling could
   decrypt **at bootstrap time**, no more and no less.
5. Repo-local docs and micro tests should stop describing skipped-key loss as an
   accepted limitation.

## Why This Branch Is Worth Doing

This is a small implementation change with outsized correctness value:

- It removes a silent decryption failure mode.
- It makes linked-device bootstrap match the stated endpoint-trust model more
  honestly.
- It avoids a post-merge trap where developers assume "peer sender keys
  transferred" means "the sibling's actual readable state transferred."
- It closes a gap now, while the handoff format is still pre-alpha and easy to
  refine.

## Current State

Today:

- `create_linked_device_bootstrap(...)` loads peer sender-key receiver records
  and serializes each one through `distribution_message_from_record(...)`.
- That serialization keeps only `group_id`, sender device key id, chain id,
  iteration, chain key, and signing public key.
- The inline comment in `provisioning.py` explicitly says
  `skipped_message_keys` are intentionally dropped because
  `SenderKeyDistributionMessage` cannot represent them.
- `finalize_linked_device_bootstrap(...)` rebuilds peer receiver records from
  those distribution messages, which produces records with empty
  `skipped_message_keys`.
- The current micro test
  `test_linked_device_bootstrap_peer_sender_keys_transferred` asserts the old
  limitation: a Bob message from before bootstrap remains unreadable on the new
  linked device.
- `packages/small-sea-manager/spec.md` also describes skipped-key loss as an
  accepted limitation and cites that test as evidence.

So the current codebase has a consistent but undesirable behavior. This branch
changes that behavior deliberately and should update all three layers together:
implementation, micro tests, and spec.

## Goals

When this branch is complete:

1. A bootstrapped linked device receives a complete peer receiver-state snapshot
   for each sender the authorizing sibling currently knows, including cached
   skipped-message keys.
2. If the authorizing sibling could decrypt an out-of-order peer message at
   bootstrap time because it had cached the needed skipped key, the new device
   can also decrypt that same message after finalize.
3. The branch preserves the existing join-time-forward boundary. We are not
   making arbitrary historical ciphertext readable; we are transferring the
   sibling's already-held receiver state.
4. Existing retry/idempotency behavior for bootstrap sessions still works.
5. The repo's protocol evidence is internally consistent: code, micro tests, and
   `packages/small-sea-manager/spec.md` all describe the new behavior.

## Non-Goals

This branch does **not**:

- Change `SenderKeyDistributionMessage`.
- Introduce ongoing synchronization of skipped keys after bootstrap.
- Change invitation flow, teammate admission, or sender-key redistribution.
- Add backward-compatibility shims beyond the narrow tolerance needed for
  already-created or replayed bootstrap bundles that may lack the new field.
- Broaden the read boundary beyond "what the sibling could already decrypt."

## Implementation Shape

### Payload Format

Extend the encrypted bootstrap plaintext with a sparse
`peer_sender_skipped_keys` object:

```json
{
  "own_sender_distribution": { "...": "..." },
  "peer_sender_distributions": [ { "...": "..." } ],
  "peer_sender_skipped_keys": {
    "<sender_device_key_id_hex>": {
      "<iteration>": "<message_key_hex>"
    }
  }
}
```

Properties:

- Keys are indexed by `sender_device_key_id.hex()` because that is the stable
  join point available on both create and finalize sides.
- Nested iteration keys are strings in JSON and become `int` again when loaded.
- The representation is sparse: omit peers with no skipped keys.
- If the field is absent, finalize treats it as `{}`. That is not a general
  compatibility policy; it is a narrow guard for already-stored bootstrap
  bundles and idempotent replay paths.

### Create-Side Changes

In `create_linked_device_bootstrap(...)`:

1. Load peer sender records once.
2. Build `peer_sender_distributions` from that in-memory list.
3. Build `peer_sender_skipped_keys` from the same list, reusing the existing
   hex-serialization logic already reflected by `_serialize_skipped(...)`.
4. Remove the stale inline comment that says skipped keys are intentionally
   dropped.

Important design constraint: do **not** query peer sender records twice. The
branch is small enough that duplicate DB access would be an unnecessary quality
regression and an easy thing for a reviewer to object to.

### Finalize-Side Changes

In `finalize_linked_device_bootstrap(...)`:

1. Read `peer_sender_skipped_keys = decrypted_payload.get("peer_sender_skipped_keys", {})`.
2. For each deserialized peer distribution, build the receiver record as today.
3. If that sender has transferred skipped keys, patch them onto the record
   before `save_peer_sender_key(...)`.
4. Preserve all existing validation, signature checks, ratchet handling, and
   retry/idempotency behavior around the bootstrap session itself.

This should be implemented as a narrow record-state restoration step, not as a
new alternate sender-key construction path.

### Helper Boundaries

The branch should prefer one of these two implementation styles:

1. Minimal inline serialization/deserialization inside `provisioning.py`, if the
   logic stays short and obvious.
2. Small helper(s) in `sender_keys.py` if that noticeably improves readability
   and avoids duplicating the string/int/hex conversions.

What we should avoid:

- Spreading ad hoc JSON-shape knowledge across several unrelated functions.
- Refactoring bootstrap or sender-key code more broadly "while we are here."
- Adding compatibility wrappers for non-existent future formats.

## Required Test Changes

The first draft understated this part. One new micro test is not enough; at
least one existing micro test must change because it currently encodes the old
behavior as correct.

### Existing Test To Update

`test_linked_device_bootstrap_peer_sender_keys_transferred` should stop proving
"pre-bootstrap Bob message unreadable on the new linked device" and instead
prove the new intended behavior.

That test name may still be fine, but its assertions need to change so it now
verifies that previously readable peer state remains readable after bootstrap,
not that the historical message fails.

### New Or Revised Scenario

The validating scenario should exercise the actual skipped-key path, not merely
"message from before bootstrap." The important case is:

1. Bob has a sender key.
2. Bob sends at least two messages.
3. Alice receives a later Bob message first, causing her receiver state to
   advance and cache one or more skipped keys.
4. Alice bootstraps sibling device B.
5. B finalizes bootstrap and receives Bob's current receiver state plus the
   cached skipped keys.
6. B can decrypt the earlier Bob message that requires one of those cached
   skipped keys.

That scenario is better evidence than the current test because it proves the
exact omission being fixed rather than a looser historical-read story.

### Test Coverage Expectations

Validation for this branch should include:

- A micro test covering the out-of-order/skipped-key handoff.
- Existing linked-device bootstrap micro tests still passing, especially:
  retry-after-interrupted-finalize idempotency, reentry rejection, and exclusion
  behavior.
- No test should start implying that linked-device bootstrap grants read access
  beyond the sibling's own readable snapshot.

## Required Spec Update

`packages/small-sea-manager/spec.md` must be updated in the same branch.

The current spec text says:

- the join-time-forward policy is evidenced by a test that currently expects a
  pre-bootstrap Bob message to remain unreadable; and
- skipped-key loss is an accepted limitation.

After this branch, that will no longer be true. The spec should instead say:

- linked-device bootstrap transfers the sibling's current peer receiver state,
  including cached skipped-message keys; and
- the remaining boundary is still join-time-forward in the sense that the new
  device receives only the sibling's current readable snapshot, not arbitrary
  earlier ciphertext outside that state.

This is a narrow spec correction, not an architecture rewrite.

## Reviewer-Facing Risks And Edge Cases

These are the places most likely to cause implementation mistakes or post-merge
regret if we do not name them up front.

### 1. Mistaking "historical message" for "skipped-key case"

A message from before bootstrap is not automatically a skipped-key case. The
test needs to force out-of-order delivery so the sibling truly has a cached
message key to transfer.

### 2. Keying The Transfer Map Incorrectly

If we index by the wrong identifier, finalize may silently attach skipped keys
to the wrong receiver record or to none at all. The map should join on
`sender_device_key_id`, not list order and not chain id alone.

### 3. Accidentally Overwriting Other Record Fields

Finalize should patch only `skipped_message_keys` onto the receiver record that
was built from the distribution message. It should not bypass
`receiver_record_from_distribution(...)` or start manually reconstructing the
whole record.

### 4. Breaking Idempotent Replay

`finalize_linked_device_bootstrap(...)` can be retried. Re-running it with the
same bootstrap bundle must remain safe. Restoring skipped keys by
`save_peer_sender_key(...)` should stay naturally idempotent.

### 5. Over-Broad Compatibility Work

This is pre-alpha. We should not build a versioned migration framework here.
The only compatibility accommodation worth keeping is "field absent means empty"
for in-flight or replayed bootstrap bundles.

### 6. Spec Drift After Merge

If code and tests change but `spec.md` does not, the next design discussion will
start from false premises. This branch should close that loop immediately.

### 7. Payload Bloat Anxiety

Skipped-key caches can grow. For this branch we should accept that risk rather
than invent a pruning policy in the handoff format. If later experience shows
the payload becomes too large, that is a separate follow-up branch with real
measurements behind it.

## Concrete Work Plan

1. Update `create_linked_device_bootstrap(...)` to serialize peer skipped keys
   into the encrypted bootstrap plaintext.
2. Update `finalize_linked_device_bootstrap(...)` to restore those skipped keys
   onto peer receiver records before saving.
3. Remove or rewrite stale comments describing skipped-key loss as intentional.
4. Update linked-device bootstrap micro tests so the transferred-state behavior
   is asserted correctly.
5. Update `packages/small-sea-manager/spec.md` so protocol evidence matches the
   new behavior.
6. Run the relevant micro test slice for linked-device bootstrap and sender-key
   behavior.

## Validation

A skeptical reviewer should be able to convince themselves of both branch
correctness and repo integrity.

### Goals Accomplished

The reviewer can verify:

1. The bootstrap plaintext now contains peer skipped-message data in addition to
   peer distribution messages.
2. The create side derives both payload fields from one load of peer sender
   records.
3. The finalize side restores skipped keys onto the correct receiver records
   before persisting them.
4. A linked-device bootstrap micro test now demonstrates that a skipped-key case
   survives the handoff.
5. Retry/idempotency micro tests still pass.
6. `packages/small-sea-manager/spec.md` no longer describes skipped-key loss as
   intended behavior.

### Repo Integrity

The reviewer can also verify:

1. The branch does not alter `SenderKeyDistributionMessage` or unrelated sender
   key flows.
2. The change remains localized to linked-device bootstrap and closely related
   spec/test files.
3. There is no new network behavior, no Hub-boundary violation, and no DB access
   expansion outside `small-sea-manager`.
4. The implementation does not add broad compatibility scaffolding inconsistent
   with the repo's pre-alpha stance.
5. The micro tests prove the intended semantics more directly than before,
   reducing future ambiguity instead of increasing it.

## Expected Files To Change

At minimum:

- `branch-plan.md`
- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`
- `packages/small-sea-manager/spec.md`

Possibly:

- `packages/small-sea-manager/small_sea_manager/sender_keys.py`

That file should change only if a tiny helper extraction genuinely improves the
implementation. It is not a goal to move logic there for its own sake.

## Out Of Scope

- Any post-bootstrap ongoing state sync.
- Any sender-key pruning or compaction policy.
- Any invitation or teammate-admission redesign.
- Any change to team DB schema or cloud/Hub behavior.
- Archiving this plan into `Archive/` as part of this branch's implementation
  step. That happens when the branch wraps, not now.

## Skeptic-Facing Wrap-Up

The committee should be able to answer these questions from this plan alone:

1. What bug are we fixing?
   Linked-device bootstrap drops peer `skipped_message_keys`, causing silent
   loss of decryptability for out-of-order peer messages the sibling could
   already read.
2. What semantic boundary remains after the fix?
   Join-time-forward snapshot transfer: the new device gets the sibling's
   current readable peer state, not arbitrary historical ciphertext.
3. What closely related work must land with the code?
   Micro test updates and a `packages/small-sea-manager/spec.md` correction.
4. What are the main ways we could regret a sloppy implementation?
   Testing the wrong scenario, keying the transfer map incorrectly, breaking
   idempotent finalize, or letting spec/test evidence drift from the code.
