# Branch Plan: Admission-Event Visibility and Objection Affordance (B2)

**Branch:** `issue-99-admission-event-visibility`
**Base:** `main`
**Primary issue:** #99 "admission-event visibility and objection affordance"
**Kind:** Implementation branch. Code + micro tests.
**Related issues:** #97 (accepted trust-domain reframe), #100 (spec/doc sweep), #69 (linked-device bootstrap)
**Related prior plan:** `Archive/branch-plan-issue-97-trust-domain-reframe.md`
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`, `packages/small-sea-hub/spec.md`
**Related code of interest:** `packages/small-sea-hub/small_sea_hub/server.py`, `packages/small-sea-hub/small_sea_hub/backend.py`, `packages/small-sea-manager/small_sea_manager/web.py`, `packages/small-sea-manager/small_sea_manager/manager.py`, `packages/small-sea-manager/small_sea_manager/provisioning.py`, `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`, `packages/small-sea-manager/small_sea_manager/templates/fragments/invitations.html`, `packages/small-sea-manager/small_sea_manager/templates/fragments/members.html`

## Purpose

Make admission-related events visible quickly enough that governance actions are real, not ceremonial.

Per issue #99 and the issue-97 meta-plan, this branch is B2: when the team DB gains an admission-relevant artifact, the appropriate admins should see it promptly in Manager and be able to act. The branch must support four event classes:

1. New linked-device `device_link` certs.
2. New invitation proposal shells.
3. Completed invitation transcripts awaiting quorum.
4. Finalized admissions.

The current codebase only fully implements the older invitation flow, so this branch should build the visibility infrastructure in a way that can carry both current and future admission states. In other words: do not hard-code the UI around today's `invitation.status` values so tightly that B5 has to rip everything back out.

Current-vs-future expectation by class:

- New linked-device `device_link` certs: fully implementable in B2.
- New invitation proposal shells: event-model and watch-path support in B2; real proposal-shell production and actionable admin decisions depend on B5.
- Completed invitation transcripts awaiting quorum: event-model and watch-path support in B2; real awaiting-quorum production and multi-admin approval flows depend on B5.
- Finalized admissions: fully implementable in B2 for visibility; objection/exclusion affordance should resolve through the existing exclusion path, not a new governance primitive.

## Provisional Decisions Before Implementation

These are the assumptions B2 should start from. Early implementation work may confirm them or force a revision, but they should not remain unstated until mid-branch.

1. **Hub watch path:** assume the existing Hub watcher path can be extended or reused with only small changes, and that B2 should preserve the current boundary where Hub wakes sessions and Manager interprets team state. Phase 2 confirms this assumption first before broader UI work proceeds.
2. **Approval scope in B2:** B2 does **not** implement multi-admin quorum approval. Any actionable "approve" control in B2 must be limited to flows that are coherent in the current model, and should be labeled to avoid implying B5-style quorum support. For future-state proposal-shell / awaiting-quorum events, B2 may expose non-actionable placeholders or clearly disabled controls, but should not claim real quorum approvals.
3. **Ignore persistence:** ignored/dismissed prompts must survive process restarts, so B2 should plan on persisted Manager-owned local state rather than in-memory bookkeeping.

## Design Direction

### 1. Keep Hub small; derive admission meaning in Manager

The Hub already has watcher infrastructure and berth-level wakeups. That makes it the right place to signal "team state changed; re-read now," but not the right place to own invitation/governance semantics.

Plan shape:

- Extend or reuse Hub watch signaling so Manager sessions wake promptly when the local team repo changes in an admission-relevant way.
- Have Manager re-read its own team DB and derive admission-event prompts locally.
- Keep the boundary clean: Hub transports change notifications; Manager interprets team-governance meaning.

This preserves the architecture rule that the Manager remains the only direct reader/writer of team berth databases while still letting the Hub be the gateway for runtime coordination.

### 2. Introduce a future-facing admission-event model

Manager should gain a local derivation layer that turns team DB state into a small set of UI events/actions rather than rendering raw invitation rows directly. That layer should be able to represent:

- informational visibility events
- proposals awaiting admin attention
- finalized admissions that can be objected to / excluded
- ignored or dismissed prompts without mutating the underlying governance artifact

The exact persisted schema can stay minimal in B2, but the code shape should leave room for B5 proposal-shell and quorum states without forcing a rewrite of the watch/UI plumbing.

Concrete deliverable: a small Python admission-event model in Manager code (for example, dataclasses or an equivalent typed structure) that is the single place where raw team DB state is translated into UI-ready event records. Reviewers should be able to point at one module/function family rather than infer the model from scattered conditionals.

### 3. Visibility must be prompt and targeted

Admins in the relevant governance set need to see actionable prompts prominently. Non-admins may still see neutral informational changes where appropriate, but they must not get approve/ignore controls that imply authority they do not have.

### 4. Objection means exclusion, not a separate governance primitive

For finalized admissions, the user-facing affordance may say "Object" or "Exclude," but the implementation should resolve to the existing exclusion/rotation path rather than inventing a competing mechanism. The UI language should make that consequence legible.

### 5. Persist ignore/dismiss state explicitly

B2 should use persisted, Manager-owned local state for dismissed prompts so ignore behavior survives restarts and sync cycles. The exact mechanism can be finalized during implementation, but it should be an explicit design choice up front: e.g. a small Manager-local table or similar durable store keyed by admission-event identity and disposition state. In-memory suppression is not sufficient.

## Expected Change Areas

### Hub integration

- `packages/small-sea-hub/small_sea_hub/server.py`
- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/small-sea-hub/spec.md`

Likely work:

- confirm whether existing `/notifications/watch` structural wakeups are sufficient or need an added admission-focused signal path
- ensure local team-repo updates trigger prompt wakeups for active Manager sessions
- document any new watch contract precisely in `packages/small-sea-hub/spec.md`

### Manager event derivation and UI

- `packages/small-sea-manager/small_sea_manager/manager.py`
- `packages/small-sea-manager/small_sea_manager/web.py`
- `packages/small-sea-manager/small_sea_manager/templates/fragments/invitations.html`
- `packages/small-sea-manager/small_sea_manager/templates/fragments/members.html`
- possibly new template fragments for admission prompts / event cards

Likely work:

- derive admission-event summaries from team DB state
- surface those events prominently in the team detail view
- add explicit action affordances for current-model actions and future-facing placeholders without implying B5 quorum support
- add explicit object/exclude affordance for finalized admissions
- keep current invitation creation/acceptance flow working while adding the event layer
- extend or replace `packages/small-sea-manager/small_sea_manager/templates/fragments/invitations.html` so invitation rows become one input to a broader admission-events presentation rather than the only surface

### Manager provisioning / data model support

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`
- `packages/small-sea-manager/spec.md`

Likely work:

- expose the data Manager needs to distinguish newly visible admission artifacts from already-seen ones
- add minimal persisted local metadata for dismissals / ignored prompts without mutating the core governance artifact
- avoid locking B2 to the old `invitation` schema in ways that block B5

## Implementation Approach

### Phase 1: Map the current state to the target event model

First, confirm the provisional Hub-watch assumption above: start by checking whether the current `/notifications/watch` structural wakeup behavior is enough to trigger timely Manager re-reads after relevant team-state changes. If that assumption fails, revise the branch shape before investing in the event model/UI.

Then read the existing linked-device and invitation flows end to end and define the minimal event taxonomy B2 needs. For each target event class from issue #99, answer:

- what concrete DB artifact or derived condition will represent it today?
- who should see it?
- what action buttons, if any, should appear?
- what local state, if any, is needed to stop resurfacing a prompt the user already chose to ignore?

This phase should end with a small, explicit Manager-side event model implemented in code, rather than ad hoc conditionals in templates.

### Phase 2: Wire prompt wakeups through Hub watch integration

Use the existing Hub watcher path if it can wake Manager reliably on relevant team-repo changes. Only add new watch semantics if the existing empty-`updated` structural signal or current peer-count path is insufficient.

This phase exists to confirm or revise the provisional watch decision made above, not to discover the question for the first time.

Question to resolve explicitly:

- Can Manager learn "re-read team state now" quickly enough from the current Hub behavior, or do we need a distinct admission/update axis?

Prefer the smallest Hub change that gives Manager prompt wakeups.

### Phase 3: Build Manager prompt surfacing

Add a visible admission-events area in the Manager team UI. That area should:

- show new linked-device admissions distinctly from teammate invitations
- distinguish "needs admin attention" from "informational only"
- expose explicit controls instead of forcing the user to infer meaning from status text
- degrade gracefully when only the old invitation flow exists locally

### Phase 4: Add explicit action handling

Implement the user actions that B2 promises:

- current-model approval/finalization action only where the existing invitation flow makes that coherent
- ignore
- object/exclude

Where B5 functionality does not exist yet, do not fake it. Either:

- implement the action against the current model when that is coherent, or
- land the visible affordance behind code paths that are clearly marked as future-facing and are only enabled when the underlying state exists

The branch should not claim quorum approval support unless the underlying proposal/approval artifacts are actually implemented. In particular, B2 should avoid labeling any control simply as `approve` if it would suggest "another admin adds one vote toward quorum"; rename the action to fit the current model unless real multi-admin approval exists.

### Phase 5: Tighten docs and micro tests

Update specs only where runtime behavior or watch contracts changed. Then add micro tests that prove the full visibility loop, not just isolated helper behavior.

## Validation

Done when a skeptical reviewer can verify all three groups below.

### Goal: admission events surface promptly and correctly

1. When a new linked-device `device_link` cert appears in team DB, Manager surfaces it without requiring a manual full-page refresh or unrelated user action.
2. When an invitation/proposal artifact appears or changes into an admin-actionable state, the relevant admin sees an explicit prompt in Manager soon after sync.
3. When a finalized admission appears, Manager surfaces a distinct objection/exclusion affordance rather than only passive status text.
4. Ignore/dismiss behavior works locally and predictably: ignored prompts do not immediately reappear on the next poll unless the underlying governance state materially changed.
5. Ignore/dismiss state survives Manager process restarts.

### Goal: the implementation is future-safe for B5

6. The Manager UI is driven by a small admission-event derivation layer, not by scattered template conditionals keyed only to today's `invitation.status`.
7. The Hub does not learn invitation/governance semantics beyond what is necessary to wake Manager sessions; governance interpretation remains in Manager.
8. The event model has documented, unused extension points for proposal-shell and awaiting-quorum states, reviewable by inspection even if those states are not yet produced by runtime code in B2.
9. No branch-local design choice hard-codes the old single-row invitation model as the permanent shape of admissions.

### Goal: repo integrity and confidence

10. Micro tests cover the full path from state change to prompt visibility for at least:
   - linked-device visibility
   - invitation/proposal visibility
   - finalized-admission objection visibility
11. Existing invitation-flow and Hub watch micro tests still pass, or are updated with a clear rationale that preserves behavior.
12. The Hub-as-gateway rule is preserved: no new direct network paths are introduced in Manager or other packages.
13. The Manager-database exclusivity rule is preserved: no non-Manager package starts reading team `core.db` directly for admission semantics.

## Out Of Scope

- The full B5 quorum implementation itself.
- Reworking the cryptographic admission transcript format.
- Changing team-governance policy semantics beyond what is needed to surface visibility and invoke existing exclusion behavior.
- Backward-compatibility shims for obsolete invitation states beyond what is necessary to keep the current pre-alpha code coherent.

## Wrap-Up Notes

When this branch is complete:

1. Update this plan with what actually landed and any deltas from the initial approach.
2. Archive it as `Archive/branch-plan-issue-99-admission-event-visibility.md`.
3. Call out any remaining B5 dependencies explicitly so a later branch can pick them up without re-discovering the visibility boundary.
