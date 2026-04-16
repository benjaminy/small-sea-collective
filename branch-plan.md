# Branch Plan: Issue 69 Linked-Device Encrypted Team Bootstrap

**Branch:** `codex-issue-69-linked-device-encrypted-team-bootstrap`  
**Base:** `main`  
**Primary issue:** #69 "Bootstrap encrypted team access for a newly linked device"  
**Related issues:** #59, #43, #48, #58  
**Related docs:** `README.md`, `architecture.md`, `packages/small-sea-manager/spec.md`, `packages/cuttlefish/README.md`  
**Related code:** `packages/small-sea-manager/small_sea_manager/provisioning.py`, `packages/small-sea-manager/small_sea_manager/manager.py`, `packages/small-sea-note-to-self/small_sea_note_to_self/db.py`, `packages/small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`, `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`  
**Related archive plans:** `Archive/branch-plan-issue-69-linked-device-encrypted-team-bootstrap.md`, `Archive/branch-plan-issue-59-sender-device-runtime-identity.md`

## Context

Issue #69 was written before a significant repo reorganization and before some
of the linked-device bootstrap work landed. The old assumptions are no longer a
clean match for the current tree.

What appears to already be true today:

- identity join and per-team join are explicitly separate in `architecture.md`
  and `packages/small-sea-manager/spec.md`
- NoteToSelf device-local state now lives in the dedicated
  `small-sea-note-to-self` package rather than being owned entirely inside the
  Manager package
- linked-team bootstrap persistence already exists in device-local NoteToSelf
  storage via `linked_team_bootstrap_session` and
  `pending_linked_team_bootstrap`
- `TeamManager` already exposes the linked-team bootstrap flow:
  `prepare_linked_device_team_join`, `create_linked_device_bootstrap`,
  `finalize_linked_device_bootstrap`, and
  `complete_linked_device_bootstrap`
- there is already focused micro-test coverage for same-member linked-team
  bootstrap in `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`

What still looks unresolved relative to the original issue wording:

- the implemented/tested flow appears to bootstrap a new device from one
  already-live sibling device of the same member
- there is not yet evidence that every other active sender device in the team
  automatically redistributes sender-key material to the new device
- the issue text still reads like a design-and-implement ticket, while the code
  now looks closer to "audit, tighten, document, and decide whether the
  remaining gap belongs in a follow-up issue"

This branch should start by treating issue #69 as an assumptions audit, not as
greenfield design work.

## Problem Statement

We need an honest answer to this question in the reorganized codebase:

"Is issue #69 already satisfied by the same-member linked-device bootstrap flow
that now exists, or is there still a real implementation gap between current
behavior and the issue's intended promise?"

If the answer is "mostly satisfied," the branch should close the integrity gap
between implementation, specs, tests, and issue text. If the answer is "not yet
satisfied," the branch should implement only the smallest missing slice needed
to make the issue true without smuggling in broader sender-key redistribution
work.

## Proposed Goal

After this branch lands:

1. the repo has an up-to-date written account of what issue #69 means in the
   reorganized architecture
2. the current same-member linked-device bootstrap flow is either:
   - validated and documented as the intended scope of #69, or
   - tightened with the smallest missing fixes needed to make that claim true
3. the branch leaves clear boundaries around what is still out of scope,
   especially cross-member or all-senders redistribution behavior
4. a smart skeptic can inspect the branch and see concrete evidence that:
   - a newly linked device can become an honest recipient for future encrypted
     team traffic
   - the historical-access boundary is enforced honestly
   - repo integrity was maintained while reconciling the stale issue assumptions

## Scope Decisions Already Made

### 1. Treat the currently implemented same-member flow as the first-class candidate scope

The branch should begin from the strongest current evidence in the tree:
same-member linked-team bootstrap already exists as code and micro tests.

We should not ignore that and re-plan the branch as if #69 were untouched.

### 2. Separate same-member bootstrap from broader team-wide redistribution

If Alice links Device B and Device A bootstraps it into Team X, that is a
different slice from "Bob's devices automatically notice Device B and
redistribute their sender keys too."

Unless the audit proves otherwise, the latter should remain follow-up work
rather than being silently folded into this branch.

### 3. Prefer clean clarification over backward-compatible shims

This repo is pre-alpha. If the issue text, specs, comments, or helper names are
misleading after the reorganization, the branch should correct them directly
instead of preserving stale terminology.

## In Scope

### 1. Assumption audit against the current codebase

Compare issue #69's original assumptions with the current state of:

- `small-sea-note-to-self` device-local bootstrap storage
- Manager bootstrap orchestration
- linked-device trust material issuance
- existing micro tests
- current specs and architecture docs

Capture the result in this plan and in any nearby specs or issue-facing notes
that need updating.

### 2. Validate the currently implemented same-member bootstrap slice end to end

Confirm that the present flow really provides all of the following:

- the new linked device gets a fresh team-device key and honest local sender
  state
- the already-live sibling device verifies the linked-device request correctly
- the sibling device issues the `device_link` cert before releasing team crypto
  material
- the new device can decrypt future encrypted team bundles after bootstrap
- the new device cannot decrypt pre-bootstrap sender-key history
- retry/finalize behavior remains idempotent for interrupted local execution

### 3. Tighten implementation only where the audit finds a real gap

If the audit shows a mismatch between the intended same-member flow and the
actual code, fix only that mismatch.

Examples of acceptable work here:

- filling a missing verification step
- tightening persistence or idempotency behavior
- adding or correcting a missing micro test
- updating a spec that still describes the pre-reorganization layout

Examples of work that do **not** belong here unless the audit proves they are
already required for #69:

- general sender-key rotation policy
- all-senders automatic redistribution
- full async Hub-mediated prekey publication infrastructure
- broad NoteToSelf refresh/discovery work

### 4. Decide the post-branch issue boundary honestly

By the end of the branch we should know which of these is true:

- `#69` is complete once the same-member bootstrap slice is validated and
  documented
- `#69` still needs a narrow finishing change in the same-member flow
- the remaining gap is actually a different issue and should be split out

## Out Of Scope

- redesigning identity bootstrap
- reworking the Hub gateway model
- adding compatibility layers for old storage layouts
- broad peer-routing/watch behavior beyond what the existing same-member flow
  needs
- solving historical export of old encrypted sender-key traffic
- inventing a larger "all devices in the team instantly redistribute to the new
  device" mechanism unless we explicitly discover that the current issue already
  promised that and the branch is intentionally expanded

## Implementation Notes

- The old archived branch plan for #69 is now historical context, not current
  truth. Reuse only the parts that still match the reorganized package layout.
- Current evidence suggests the center of gravity has shifted from speculative
  design into verification and cleanup around already-landed functionality.
- Any code changes should preserve the architectural rule that Small Sea
  internet traffic goes through the Hub; local bootstrap orchestration and local
  micro tests should stay local-first.

## Validation

The validation bar for this branch should be unusually explicit so a skeptical
reviewer can see both functional success and repo-integrity preservation.

### Functional proof

- run the linked-device bootstrap micro tests in
  `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`
- run any adjacent sender-key or identity/bootstrap micro tests touched by the
  changes
- if the audit reveals a missing behavior claim, add a focused micro test that
  fails before the fix and passes after it
- confirm with tests that pre-bootstrap encrypted history is still unreadable on
  the newly linked device

### Integrity proof

- confirm specs and code references point to the current package ownership
  (`small-sea-note-to-self` vs older Manager-owned assumptions)
- avoid widening coupling between Manager, Hub, and NoteToSelf storage just to
  satisfy stale issue wording
- prefer small, local edits over broad protocol churn
- document any remaining non-goals explicitly so the branch does not overclaim

### Skeptic-facing wrap-up

The final branch summary should answer these questions directly:

1. What exact claim from issue #69 is now proven true?
2. What exact claim is intentionally still not solved here?
3. Which tests or code paths prove the honest historical boundary?
4. Why does the final code fit the reorganized architecture better than the old
   issue assumptions did?
