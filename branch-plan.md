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

- the existing micro-test flow depends on a pre-bootstrap team-baseline setup
  step analogous to a missing "payload 0": the joining device is manually made
  aware of the team before linked-team bootstrap begins
- the implemented/tested flow appears to bootstrap a new device from one
  already-live sibling device of the same member
- there is not yet evidence that every other active sender device in the team
  automatically redistributes sender-key material to the new device
- there is not yet evidence that the "payload 3" return trip has a settled
  product transport beyond manual or test-local handoff
- the strongest existing historical-boundary test depends on Cuttlefish
  behavior, and this repo has treated some of that crypto surface as placeholder
  code during pre-alpha development
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
   - the branch does not overclaim end-to-end coverage where "payload 0"
     team discovery or "payload 3" return transport are still manual or
     separately owned concerns
   - a newly linked device can become an honest recipient for future encrypted
     team traffic
   - the historical-access boundary is enforced honestly
   - any intentionally incomplete product behavior is called out plainly rather
     than being mistaken for a broken bootstrap
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

The branch should still document the user-visible consequence clearly: without a
follow-up redistribution path, the new device may be able to read Alice's
future traffic before it can read future traffic from every other active sender
in the team.

### 3. Prefer clean clarification over backward-compatible shims

This repo is pre-alpha. If the issue text, specs, comments, or helper names are
misleading after the reorganization, the branch should correct them directly
instead of preserving stale terminology.

### 4. Treat transport and UX caveats as first-class acceptance criteria

Even if this branch keeps manual or out-of-band exchange in place, it should
say so explicitly. A bootstrap flow that works cryptographically but leaves the
user "partially blind" or stuck on an unspecified payload handoff should not be
described as more complete than it really is.

That applies to both ends of the flow:

- "payload 0": how the new device discovers that Team X exists and obtains a
  readable baseline before team bootstrap
- "payload 3": how the new device's return distribution reaches the authorizing
  sibling device

### 5. Do not treat placeholder crypto tests as stronger proof than they are

If the branch cites Cuttlefish-backed tests as evidence for the historical
access boundary, it should also state what those tests really prove in a
pre-alpha repo where parts of the crypto layer may still be placeholder code.

That means distinguishing:

- protocol/state-transition evidence inside this repo
- cryptographic/security evidence that would require a firmer trust claim about
  the underlying implementation

## In Scope

### 1. Assumption audit against the current codebase

Compare issue #69's original assumptions with the current state of:

- team discovery / baseline availability before linked-team bootstrap begins
- `small-sea-note-to-self` device-local bootstrap storage
- Manager bootstrap orchestration
- linked-device trust material issuance
- existing micro tests
- current specs and architecture docs

Capture the result in this plan during development, but also publish the final
audit outcome in a permanent home before merge.

Minimum permanent target:

- update `packages/small-sea-manager/spec.md` with the current linked-device
  bootstrap slice and its explicit boundaries

Optional additional target if helpful:

- a closing or status comment on GitHub issue `#69` summarizing what changed and
  which follow-up gaps remain

### 2. Validate the currently implemented same-member bootstrap slice end to end

Confirm that the present flow really provides all of the following:

- the branch is honest about the precondition that the joining device already
  knows Team X exists and has a readable baseline, or else narrows the claim so
  it does not pretend that discovery is solved here
- the new linked device gets a fresh team-device key and honest local sender
  state
- the already-live sibling device verifies the linked-device request correctly
- the sibling device issues the `device_link` cert before releasing team crypto
  material
- the new device can decrypt future encrypted team bundles after bootstrap
- the new device cannot decrypt pre-bootstrap sender-key history
- retry/finalize behavior remains idempotent for interrupted local execution
- prepare-stage interrupted-flow behavior is either made safe/idempotent or
  documented as a current limitation
- the current transport story for the joining device's return payload is stated
  honestly in code or docs rather than being left implicit in test-only wiring

### 3. Tighten implementation only where the audit finds a real gap

If the audit shows a mismatch between the intended same-member flow and the
actual code, fix only that mismatch.

Examples of acceptable work here:

- filling a missing verification step
- tightening persistence or idempotency behavior
- adding or correcting a missing micro test
- updating a spec that still describes the pre-reorganization layout
- replacing a brittle raw path reference in tests with a helper-backed lookup if
  that improves integrity without broadening scope

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

That decision should explicitly address:

- whether the missing "payload 0" team-discovery/baseline story is part of #69
  or an acknowledged prerequisite owned elsewhere
- whether missing Bob/other-sender redistribution is a blocker for closing #69
- whether payload 3 transport is intentionally manual in this slice
- what user-visible expectation we set for linked-device history access
- whether per-team bootstrap is intentionally required independently for each
  known team on a linked device

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
- silently treating product/UX caveats as solved when they are only solved in
  tests or by manual operator handoff
- turning this branch into a full multi-team orchestration feature unless the
  audit shows that #69 already requires more than per-team bootstrap semantics

## Implementation Notes

- The old archived branch plan for #69 is now historical context, not current
  truth. Reuse only the parts that still match the reorganized package layout.
- Current evidence suggests the center of gravity has shifted from speculative
  design into verification and cleanup around already-landed functionality.
- Any code changes should preserve the architectural rule that Small Sea
  internet traffic goes through the Hub; local bootstrap orchestration and local
  micro tests should stay local-first.
- The current micro test is not a full product-flow proof because it manually
  establishes the joining device's knowledge of the team and baseline before
  bootstrap begins. The branch should name that prerequisite explicitly instead
  of implying it is already covered.
- The branch should be careful not to overclaim "bootstrap complete" if the
  actual outcome is "same-member bootstrap works, broader sender visibility
  still depends on later redistribution."
- If payload 3 currently depends on manual return transport, that should be
  named as the current slice boundary, not hidden behind direct function calls
  in micro tests.
- The honest historical boundary is a security choice, but it is also a product
  expectation problem. If the code keeps the current forward-only behavior, the
  docs/specs should say that prominently enough that a user would not infer
  full history sync from the phrase "linked device."
- The branch should examine prepare-stage re-entry and decide whether repeated
  `prepare_linked_device_team_join(...)` should reuse an in-flight bootstrap
  session, invalidate the old one explicitly, or remain a documented limitation.
- The audit should note fragile couplings even if they are not all fixed here,
  including direct imports of private Cuttlefish helpers and raw test-path
  construction that bypasses path helpers.

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
  the newly linked device, while describing this as repo-local protocol evidence
  rather than definitive crypto assurance if the underlying Cuttlefish layer is
  still placeholder
- if possible, add or tighten one focused check that makes the current
  redistribution boundary visible rather than leaving it as an unstated
  assumption

Preferred focused checks if this branch adds tests:

- a "Bob exists too" scenario where Device B completes same-member bootstrap but
  still lacks Bob's sender-key distribution afterward
- a negative cross-member scenario showing that a join request for Member B
  cannot be accepted as if it belonged to Member A
- a prepare-stage re-entry scenario covering crash/retry before finalize

### Integrity proof

- confirm specs and code references point to the current package ownership
  (`small-sea-note-to-self` vs older Manager-owned assumptions)
- avoid widening coupling between Manager, Hub, and NoteToSelf storage just to
  satisfy stale issue wording
- prefer small, local edits over broad protocol churn
- document any remaining non-goals explicitly so the branch does not overclaim
- make any manual transport assumptions around payload 3 explicit so a reviewer
  can distinguish protocol completeness from test harness convenience
- make the "payload 0" prerequisite equally explicit so a reviewer can
  distinguish team discovery/baseline delivery from team bootstrap itself
- flag fragile couplings discovered during the audit, even if some are deferred,
  such as private-symbol imports from Cuttlefish

### Skeptic-facing wrap-up

The final branch summary should answer these questions directly:

1. What exact claim from issue #69 is now proven true?
2. What exact claim is intentionally still not solved here?
3. Which tests or code paths prove the honest historical boundary?
4. Why does the final code fit the reorganized architecture better than the old
   issue assumptions did?
5. What happens today for "other senders in the team" after the linked device
   is bootstrapped?
6. Is payload 3 still a manual return-trip in this slice, and if so, where is
   that boundary documented?
7. What is the explicit "payload 0" prerequisite for team discovery and readable
   baseline state before linked-team bootstrap starts?
8. Which findings are protocol/product-boundary clarifications versus true
   cryptographic assurances?
