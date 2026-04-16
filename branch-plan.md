# Branch Plan: Linked-Device Bootstrap Create Retry Semantics

**Branch:** `codex-linked-device-bootstrap-create-retry`  
**Base:** `main`  
**Primary follow-up:** tighten `create_linked_device_bootstrap(...)` retry semantics after the `#69` audit/tightening branch  
**Related issue:** #69 "Bootstrap encrypted team access for a newly linked device"  
**Related docs:** `packages/small-sea-manager/spec.md`, `architecture.md`  
**Related code:** `packages/small-sea-manager/small_sea_manager/provisioning.py`, `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`  
**Related archive plans:** `Archive/branch-plan-codex-issue-69-linked-device-encrypted-team-bootstrap.md`

## Context

The previous `#69` follow-up branch tightened and documented most of the
same-member linked-device bootstrap flow:

- `prepare_linked_device_team_join(...)` rejects a second in-flight bootstrap
  for the same team
- `finalize_linked_device_bootstrap(...)` retry behavior is covered and treated
  as idempotent once the response payload is stored
- `complete_linked_device_bootstrap(...)` now tolerates a replayed payload after
  peer sender state has already been stored
- `packages/small-sea-manager/spec.md` now documents the current slice and its
  product/protocol boundaries

The explicit remaining limitation is `create_linked_device_bootstrap(...)`.

Today that function does all of the following on the authorizing device:

- verifies the joining device request
- issues a `device_link` cert
- stores team-db state
- records a pending bootstrap breadcrumb
- emits the bootstrap bundle

That means the authorizing side has at least two distinct retry problems:

- **replay of a completed create**: the first call succeeded and returned a
  bootstrap bundle, then the same join request bundle is submitted again
- **crash recovery during create**: the first call crashed after some state was
  written but before a bundle was returned to the caller

This branch exists to make that behavior honest and predictable.

## Problem Statement

We need one clear answer to this question:

"If the authorizing device receives the same valid join request bundle twice,
whether due to crash recovery, operator retry, or duplicate delivery, what
should `create_linked_device_bootstrap(...)` do?"

The branch should not leave the answer implicit in SQLite side effects or git
history accidents.

It should also be explicit about whether it handles:

- replay of a completed create
- crash recovery from partial authorizer-side writes

If it only solves one of those, the other should be documented as an intentional
remaining limitation.

## Proposed Goal

After this branch lands:

1. `create_linked_device_bootstrap(...)` has an explicit retry policy for
   duplicate valid join requests
2. that policy is enforced by code and covered by focused micro tests
3. the branch does not broaden scope into payload-0 discovery, payload-3
   transport, or all-senders redistribution
4. the resulting behavior is documented in `packages/small-sea-manager/spec.md`
   as part of the same-member linked-device bootstrap slice

## Scope Decisions Already Made

### 1. Keep this branch narrowly authorizer-side

This branch is about duplicate submission / retry behavior in
`create_linked_device_bootstrap(...)` on the authorizing device.

It is not a new bootstrap design branch.

### 2. Prefer one deterministic duplicate policy

The branch should settle on one of these shapes and implement it cleanly:

- idempotent replay: same valid request returns a semantically identical result
  without corrupting state
- explicit rejection: same valid request is rejected with a clear, intentional
  error after prior success

Given the rest of the bootstrap flow already leans toward retry safety, the
default candidate should be idempotent replay unless the audit shows a strong
reason not to do that.

For this branch, "same result" does **not** need to mean bitwise-identical
bootstrap bundle bytes. The bundle contains encrypted content and may involve
fresh randomness. The meaningful identity is likely:

- same `bootstrap_id`
- same `device_link` cert / trust effect
- same logical bootstrap payload content and follow-up behavior
- no duplicate persistent side effects

Tests should assert semantic equivalence at that level, not raw ciphertext
equality, unless the implementation deliberately stores and replays the exact
serialized response.

That means the branch should consciously choose one implementation strategy if
it goes with idempotent replay:

- **store-and-replay**: persist the original bootstrap bundle (or the exact
  response ingredients needed to reproduce it byte-for-byte) and return that on
  retry
- **re-derive-and-re-encrypt**: rebuild a logically equivalent bundle on retry,
  accepting that encrypted bytes may differ

Before choosing re-derive-and-re-encrypt, the branch should confirm whether
`finalize_linked_device_bootstrap(...)` cares about exact response bytes versus
only the signed semantic fields inside the bundle.

### 3. Do not confuse request replay with a new bootstrap attempt

A second call with the exact same join request bundle is not the same thing as a
new `prepare_linked_device_team_join(...)` run on the joining device. The branch
should be careful to distinguish:

- replay of the same prepared request
- a later brand-new request with a different bootstrap ID and different
  ephemeral material

If this branch does not settle the "new bootstrap ID while another create-side
breadcrumb is still pending for the same team" case, it should name that
explicitly as a remaining limitation rather than leaving it implicit.

## In Scope

### 1. Audit current `create_linked_device_bootstrap(...)` side effects

Trace exactly what happens on first successful create:

- cert issuance
- team DB writes
- pending bootstrap breadcrumb writes
- bootstrap bundle construction
- any git commit side effects

The audit should identify which pieces already behave idempotently and which do
not.

It should separately identify:

- replay behavior after a completed successful create
- crash-recovery behavior after partial writes but before a bundle was returned
- whether current tests/fixtures already give us a real git repo in the team
  `Sync/` directory so git-history assertions are actually possible without
  extra harness work

### 2. Choose and implement one retry policy

The branch should decide whether duplicate replay of the same valid join request
should:

- return the same effective bootstrap result, or
- fail deliberately with a stable, documented reason

Whichever choice we make, it should be intentional, narrow, and test-backed.

The branch should also explicitly decide whether crash-mid-create recovery is in
scope here or is being deferred. If deferred, the branch should document the
current limitation clearly.

If crash-mid-create is deferred, the branch should describe the exact stuck
state, not just say "recovery is deferred." In particular, it should state what
the system looks like if a crash lands after cert issuance but before breadcrumb
write and bundle return.

### 3. Add focused micro tests

Minimum required coverage:

- one duplicate-submission scenario on the authorizing device using the exact
  same join request bundle twice
- one assertion about resulting cert history / pending bootstrap state so a
  skeptic can see whether replay duplicates or reuses state
- one assertion about git history state so a skeptic can see whether replay
  creates an extra commit for the same logical create step

If the branch chooses idempotent replay, the test should prove that replay does
not create spurious duplicate cert rows or inconsistent breadcrumbs.

If the branch chooses store-and-replay, the test should prove the stored replay
path is what later finalize expects.

If the branch chooses re-derive-and-re-encrypt, the test should prove the
newly generated bundle is still acceptable to
`finalize_linked_device_bootstrap(...)`.

### 4. Update permanent docs

Update `packages/small-sea-manager/spec.md` so the same-member linked-device
bootstrap section says what `create_linked_device_bootstrap(...)` does on retry,
with a citation to the backing test or code path.

## Out Of Scope

- payload-0 team discovery or baseline delivery
- payload-3 return transport
- broader sender redistribution for other team members
- redesigning the linked-device bootstrap payload shapes
- general sender-key rotation policy
- a broad Cuttlefish hardening branch
- the "new bootstrap ID while another create-side breadcrumb for the same team
  is still pending" case, unless the implementation naturally settles it as
  part of the retry policy work

## Implementation Notes

- The repo is pre-alpha, so if current behavior is accidental or confusing, this
  branch should choose the clearest design rather than preserving awkward
  behavior for compatibility.
- The branch should treat duplicate git commits as a concrete correctness issue,
  not just cosmetic noise. If replay currently creates extra commits for the
  same logical create step, that is probably a bug.
- This branch should reuse the current `#69` documentation language instead of
  inventing a parallel description of the bootstrap flow.
- If the branch relies on git-history assertions, it should first confirm the
  playground/test fixture actually leaves a live git repo in the team `Sync/`
  directory. If not, the plan should either add the needed fixture support or
  narrow the validation claim honestly.

## Validation

### Functional proof

- run `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`
- add a focused duplicate-create micro test
- if replay is idempotent, prove repeated create does not break later finalize
  or complete behavior
- if replay is rejected, prove the rejection is stable and intentional rather
  than a downstream accidental failure
- if crash-mid-create recovery is in scope, add a focused interrupted-create
  test and prove the retried behavior is the intended one

### Integrity proof

- confirm the branch does not widen coupling between Manager and NoteToSelf
  storage just to implement retry behavior
- confirm any `spec.md` update cites the test or code path backing the retry
  claim
- avoid broad edits outside the linked-device bootstrap seam
- confirm the chosen retry policy does not create duplicate git commits for the
  same logical create step
- if crash-mid-create is deferred, confirm `spec.md` describes the actual
  resulting stuck state rather than naming it only as an abstract limitation

## Skeptic-facing wrap-up

The final branch summary should answer:

1. What exactly happens now if the same valid join request bundle is submitted
   twice to `create_linked_device_bootstrap(...)`?
2. Why is that behavior the right one for this pre-alpha bootstrap flow?
3. Which test proves the retry policy?
4. Does the replay create duplicate certs, duplicate pending breadcrumbs, or
   extra commits?
5. If crash-mid-create is deferred, what exact state is the system left in
   after the crash and how would an operator recognize that?
6. What remains intentionally outside this branch?
