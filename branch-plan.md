# Branch Plan: disentangle PR 112

## Goal

Resolve the merge conflict between PR 112 (`issue-59-device-link-visibility`) and current `main` without losing either branch's behavior.

## Conflict Shape

- PR 112 adds device-link admission notification disposition helpers, including local `notified` state.
- Current `main` adds app-bootstrap sighting dismissal helpers for participant-level and team-level prompts.
- Both branches appended helper functions in `small_sea_manager/provisioning.py`, producing one additive content conflict.

## Implementation

- Keep PR 112's `mark_admission_event_notified(...)` helper.
- Keep `main`'s app-sighting disposition stores and dismissal/filter helpers.
- Avoid schema compatibility shims beyond the existing versioned local-store rebuild logic already in the PR.

## Validation

To convince a skeptical reviewer that the conflict is resolved correctly:

- Confirm the merge has no remaining conflict markers or whitespace errors.
- Run the PR 112 micro test that proves notification backlog seeding and `notified` rows suppress repeated push candidates while keeping UI cards visible.
- Run the app-bootstrap sighting micro tests from `main` to prove participant and team dismissal behavior still works after the helper block is combined.
- Run the broader manager/hub micro-test slice if the focused tests reveal coupling or if later edits touch shared session, Hub, or NoteToSelf behavior.

## Integrity Checks

- The resolution is local to the conflicting helper block and keeps storage ownership unchanged:
  - Manager continues to own local sidecar/device-local disposition state.
  - Hub continues to access this behavior through the Manager-facing admission-event helper surface.
- The combined code preserves low coupling by keeping admission notification disposition and app-sighting dismissal as separate helpers, sharing only the existing local DB path conventions.
