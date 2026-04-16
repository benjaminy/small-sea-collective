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

That means a crash or duplicate submission on the authorizing side may land in
an awkward state between "cert already issued / breadcrumb already stored" and
"same request retried." This branch exists to make that behavior honest and
predictable.

## Problem Statement

We need one clear answer to this question:

"If the authorizing device receives the same valid join request bundle twice,
whether due to crash recovery, operator retry, or duplicate delivery, what
should `create_linked_device_bootstrap(...)` do?"

The branch should not leave the answer implicit in SQLite side effects or git
history accidents.

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

### 3. Do not confuse request replay with a new bootstrap attempt

A second call with the exact same join request bundle is not the same thing as a
new `prepare_linked_device_team_join(...)` run on the joining device. The branch
should be careful to distinguish:

- replay of the same prepared request
- a later brand-new request with a different bootstrap ID and different
  ephemeral material

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

### 2. Choose and implement one retry policy

The branch should decide whether duplicate replay of the same valid join request
should:

- return the same effective bootstrap result, or
- fail deliberately with a stable, documented reason

Whichever choice we make, it should be intentional, narrow, and test-backed.

### 3. Add focused micro tests

Minimum required coverage:

- one duplicate-submission scenario on the authorizing device using the exact
  same join request bundle twice
- one assertion about resulting cert history / pending bootstrap state so a
  skeptic can see whether replay duplicates or reuses state

If the branch chooses idempotent replay, the test should prove that replay does
not create spurious duplicate cert rows or inconsistent breadcrumbs.

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

## Implementation Notes

- The repo is pre-alpha, so if current behavior is accidental or confusing, this
  branch should choose the clearest design rather than preserving awkward
  behavior for compatibility.
- The branch should watch for git side effects carefully: if replay currently
  creates extra commits for the same logical create step, that is probably a
  bug, not harmless noise.
- This branch should reuse the current `#69` documentation language instead of
  inventing a parallel description of the bootstrap flow.

## Validation

### Functional proof

- run `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`
- add a focused duplicate-create micro test
- if replay is idempotent, prove repeated create does not break later finalize
  or complete behavior
- if replay is rejected, prove the rejection is stable and intentional rather
  than a downstream accidental failure

### Integrity proof

- confirm the branch does not widen coupling between Manager and NoteToSelf
  storage just to implement retry behavior
- confirm any `spec.md` update cites the test or code path backing the retry
  claim
- avoid broad edits outside the linked-device bootstrap seam

## Skeptic-facing wrap-up

The final branch summary should answer:

1. What exactly happens now if the same valid join request bundle is submitted
   twice to `create_linked_device_bootstrap(...)`?
2. Why is that behavior the right one for this pre-alpha bootstrap flow?
3. Which test proves the retry policy?
4. Does the replay create duplicate certs, duplicate pending breadcrumbs, or
   extra commits?
5. What remains intentionally outside this branch?
