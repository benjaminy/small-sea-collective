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

### 3. Visibility must be prompt and targeted

Admins in the relevant governance set need to see actionable prompts prominently. Non-admins may still see neutral informational changes where appropriate, but they must not get approve/ignore controls that imply authority they do not have.

### 4. Objection means exclusion, not a separate governance primitive

For finalized admissions, the user-facing affordance may say "Object" or "Exclude," but the implementation should resolve to the existing exclusion/rotation path rather than inventing a competing mechanism. The UI language should make that consequence legible.

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
- add explicit action affordances for approve / ignore on open proposals
- add explicit object/exclude affordance for finalized admissions
- keep current invitation creation/acceptance flow working while adding the event layer

### Manager provisioning / data model support

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`
- `packages/small-sea-manager/spec.md`

Likely work:

- expose the data Manager needs to distinguish newly visible admission artifacts from already-seen ones
- if needed, add minimal local metadata for dismissals / ignored prompts without mutating the core governance artifact
- avoid locking B2 to the old `invitation` schema in ways that block B5

## Implementation Approach

### Phase 1: Map the current state to the target event model

Read the existing linked-device and invitation flows end to end and define the minimal event taxonomy B2 needs. For each target event class from issue #99, answer:

- what concrete DB artifact or derived condition will represent it today?
- who should see it?
- what action buttons, if any, should appear?
- what local state, if any, is needed to stop resurfacing a prompt the user already chose to ignore?

This phase should end with a small, explicit Manager-side event model rather than ad hoc conditionals in templates.

### Phase 2: Wire prompt wakeups through Hub watch integration

Use the existing Hub watcher path if it can wake Manager reliably on relevant team-repo changes. Only add new watch semantics if the existing empty-`updated` structural signal or current peer-count path is insufficient.

Key question to settle during implementation:

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

- approve
- ignore
- object/exclude

Where B5 functionality does not exist yet, do not fake it. Either:

- implement the action against the current model when that is coherent, or
- land the visible affordance behind code paths that are clearly marked as future-facing and are only enabled when the underlying state exists

The branch should not claim quorum approval support unless the underlying proposal/approval artifacts are actually implemented.

### Phase 5: Tighten docs and micro tests

Update specs only where runtime behavior or watch contracts changed. Then add micro tests that prove the full visibility loop, not just isolated helper behavior.

## Validation

Done when a skeptical reviewer can verify all three groups below.

### Goal: admission events surface promptly and correctly

1. When a new linked-device `device_link` cert appears in team DB, Manager surfaces it without requiring a manual full-page refresh or unrelated user action.
2. When an invitation/proposal artifact appears or changes into an admin-actionable state, the relevant admin sees an explicit prompt in Manager soon after sync.
3. When a finalized admission appears, Manager surfaces a distinct objection/exclusion affordance rather than only passive status text.
4. Ignore/dismiss behavior works locally and predictably: ignored prompts do not immediately reappear on the next poll unless the underlying governance state materially changed.

### Goal: the implementation is future-safe for B5

5. The Manager UI is driven by a small admission-event derivation layer, not by scattered template conditionals keyed only to today's `invitation.status`.
6. The Hub does not learn invitation/governance semantics beyond what is necessary to wake Manager sessions; governance interpretation remains in Manager.
7. The code can represent all four issue-99 event classes, including proposal-shell and awaiting-quorum states, without redesigning the watch boundary.
8. No branch-local design choice hard-codes the old single-row invitation model as the permanent shape of admissions.

### Goal: repo integrity and confidence

9. Micro tests cover the full path from state change to prompt visibility for at least:
   - linked-device visibility
   - invitation/proposal visibility
   - finalized-admission objection visibility
10. Existing invitation-flow and Hub watch micro tests still pass, or are updated with a clear rationale that preserves behavior.
11. The Hub-as-gateway rule is preserved: no new direct network paths are introduced in Manager or other packages.
12. The Manager-database exclusivity rule is preserved: no non-Manager package starts reading team `core.db` directly for admission semantics.

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
