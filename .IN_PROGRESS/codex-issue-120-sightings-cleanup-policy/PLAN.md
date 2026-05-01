# Branch Plan: Hub App-Bootstrap Sighting Cleanup Policy

**Branch:** `codex-issue-120-sightings-cleanup-policy`
**Base:** `main`
**Primary issue:** #120 "Decide cleanup policy for Hub app-bootstrap sightings"
**Related context:** #111 app-bootstrap sightings, #118 Manager sightings UI, #119 app-bootstrap client helper.
**Reference plan:** `Archive/branch-plan-issue-111-app-bootstrap-sightings.md`.
**Kind:** Policy decision plus narrow implementation and micro tests.

## Purpose

Issue #111 intentionally left Hub unknown-app sightings "more or less forever" in v1.
That was the right first slice because the Hub needed to be an observation point, not a provisioning authority.
Issue #120 should now decide what kind of state sightings are:

- permanent local audit history
- active prompts that disappear once resolved
- stale observations that age out after the app stops asking
- some explicit combination of the above

The branch should turn that decision into documented behavior, code, and micro tests.
The end result should be boring to reason about: Hub sightings remain local observations, Manager remains the authority that decides whether a prompt is resolved, and the local Hub database does not grow forever from old bootstrap attempts.

## Working Policy To Validate

The initial policy for this branch is:

1. Hub sightings are **active local observations**, not permanent audit history.
2. Resolved sightings are cleared explicitly by Manager after Manager re-evaluates current NoteToSelf/team state and proves that no prompt remains, even if Manager-local disposition would hide the row from the UI.
3. Stale unresolved sightings age out from the Hub after a 30-day no-retry window.
4. Dismissed prompts are still Manager-owned local disposition state.
5. If a dismissed but unresolved app keeps retrying, the Hub row keeps getting bumped and Manager keeps suppressing it locally.
6. If a cleaned-up or aged-out app retries later, the Hub records a fresh sighting.

The plan should revisit this policy before coding.
If the final decision changes, update this section first and make the implementation match the written policy.

## Non-Negotiable Invariants

1. The Hub must not read Manager-owned NoteToSelf or team DBs to decide whether a sighting is resolved.
2. Apps must not gain any ability to list, clear, or mutate sightings.
3. Sightings remain local Hub state and are never synced to peers.
4. Manager disposition tables remain Manager-owned.
5. Clearing a sighting must never register or activate an app.
6. Clearing or aging out a sighting must not suppress future app requests.
7. The cleanup path must preserve the Hub-as-gateway rule.
8. The implementation must keep using "micro tests" terminology.

## Current State

The Hub stores sightings in `unknown_app_sighting` in `small_sea_collective_local.db`.
The key is `(participant_hex, app_name, team_name, client_name)`.
Repeated requests update `last_seen_at`, increment `seen_count`, and replace `reason`.

Manager reads `GET /sightings` through a confirmed Core NoteToSelf session.
`TeamManager.refresh_app_sightings()` calls `provisioning.current_app_sighting_prompt(...)`, which re-evaluates the raw Hub row against current local state.
If the app has been registered and activated, Manager currently returns no prompt but leaves the Hub row in place.

That means resolved rows are hidden from users but remain queryable forever.
Unresolved rows also remain forever unless hidden by Manager-local disposition.

## Decision Points

### D1. What is a sighting?

Working decision: a sighting is an active local observation used to drive bootstrap repair.
It is not a durable audit log.

Rationale:

- The current table lacks audit semantics such as actor, lifecycle events, or reviewed disposition.
- The row is deduped by app/team/client tuple, so it already behaves like the latest state of a prompt rather than a history log.
- Keeping resolved rows forever makes `GET /sightings` increasingly unlike the Manager concept of "current prompts."

### D2. Who clears resolved sightings?

Working decision: Manager clears resolved sightings explicitly through a narrow Hub API.

Rationale:

- Only Manager can safely know that participant registration and team activation now exist.
- The Hub should not inspect Manager DBs or infer resolution from its own local state.
- Explicit clear keeps the operation reviewable and testable.

### D3. How do stale unresolved sightings age out?

Working decision: the Hub opportunistically prunes sightings whose `last_seen_at` is older than the configured stale window when Manager lists sightings.
The default stale window is 30 days.

Rationale:

- A sighting that has not been bumped for a long time probably represents an app that stopped trying.
- Listing is the canonical authenticated Manager path and gives the cleanup one predictable trigger.
- Pruning during app-driven record would couple an ordinary bootstrap failure to mass deletion work.
- Opportunistic pruning on list avoids a background worker.
- A future retry recreates the row, so cleanup is not a durable rejection.

The 30-day window matches the active-observation framing.
If the app has not retried in a month, the user has probably moved on.

### D4. How are dismissed rows treated?

Working decision: dismissal affects display only.
Manager evaluates whether the sighting is resolved before applying disposition.
Resolved rows are cleared even when they were previously dismissed; dismissed but unresolved rows remain.

Rationale:

- Dismissal is a Manager presentation decision.
- If an app keeps retrying, the Hub should continue to reflect that observation.
- Manager can still suppress the prompt using local disposition state.
- Participant-level disposition is keyed by `app_name`, so allowing dismissal to pin storage would make one UI preference preserve many Hub rows across teams and clients.

### D5. What is the cleanup endpoint shape?

Working decision: use `POST /sightings/clear` with an exact tuple body.

```http
POST /sightings/clear
Authorization: Bearer <Core NoteToSelf session token>
Content-Type: application/json

{
  "app_name": "SharedFileVault",
  "team_name": "ProjectX",
  "client_name": "shared-file-vault:default"
}
```

Rationale:

- The table has no surrogate id, and adding one would be schema churn without functional gain.
- `DELETE` with a body is poorly supported by some HTTP stacks.
- The tuple body matches the existing unique key and the `GET /sightings` row shape.
- The Hub derives `participant_hex` from the session token and does not accept it from the caller.
- `team_name`, `app_name`, and `client_name` are all required.
  There is no wildcard delete.
- The endpoint is idempotent: if no row matches, it returns success with `deleted_count = 0`.

The implementation should use the same `_require_session` dependency as `GET /sightings`, including the same `ss_session.app_name == Settings().app_name` Manager/Core guard.
That means the auth convention remains `Authorization: Bearer <token>`.

## Branch Contract

The branch is successful if all of the following are true:

1. The docs state a clear sighting lifecycle policy.
2. Manager clears Hub sightings only after re-evaluation says they are resolved.
3. Hub exposes only the minimum authenticated cleanup surface needed by Manager.
4. Stale rows age out predictably on Manager listing, without background polling.
5. Retrying an app after cleanup or age-out creates a fresh sighting.
6. Dismissed unresolved rows remain suppressible by Manager and continue to bump if the app retries.
7. Micro tests prove the policy at Hub, client, and Manager boundaries.

## Implementation Sketch

### Phase 0 - Freeze Policy

- Treat this plan's decisions as the branch policy unless implementation finds a contradiction.
- Record the final lifecycle in `packages/small-sea-hub/spec.md` and `packages/small-sea-manager/spec.md` before larger code edits.
- Keep the branch plan under `.IN_PROGRESS/codex-issue-120-sightings-cleanup-policy/PLAN.md`, matching the current AGENTS.md workflow for nontrivial work.

### Phase 1 - Hub Cleanup Primitives

- Add a Hub backend method that deletes a sighting by `(participant_hex, app_name, team_name, client_name)`.
- Add a Hub backend method that prunes stale sightings by `last_seen_at`.
- Add a module-level default stale window constant of 30 days, with a `SmallSeaBackend(...)` constructor parameter for tests.
- Introduce a `_now()` method or constructor-injected clock and route both `record_unknown_app_sighting(...)` and stale pruning through it.
- Invoke stale pruning from `list_unknown_app_sightings(...)` only.
- Add an authenticated HTTP endpoint for Manager-driven clear.
- Keep authorization identical to `GET /sightings`: `_require_session`, then the Manager/Core `Settings().app_name` guard.
- Require exact `app_name`, `team_name`, and `client_name` fields in the clear payload.
  Treat empty strings as literal values, not wildcards.
- Return success with `deleted_count = 0` when no row matches.

Exit gate:
Hub micro tests cover successful clear, clear idempotency, unauthorized clear, participant scoping, retry-after-clear, stale pruning on list, and no pruning on record alone.

### Phase 2 - Client Helper

- Add a small client-session helper for clearing app sightings.
- Keep it scoped to confirmed sessions, parallel to `Session.app_sightings()`.
- Do not expose cleanup from ordinary app bootstrap clients.
- Build the payload directly from the sighting tuple keys: `app_name`, `team_name`, and `client_name`.

Exit gate:
Client micro tests prove the request body shape and that bootstrap exceptions are not involved in cleanup.

### Phase 3 - Manager Integration

- In `TeamManager.refresh_app_sightings()`, evaluate `current_app_sighting_prompt(...)` before applying Manager-local disposition.
- When `current_app_sighting_prompt(...)` returns `None`, call cleanup with the loop-scope sighting tuple.
  No new tracking structure is needed.
- Apply `app_sighting_dismissed(...)` only to rows that still have a current prompt.
- Do not clear rows whose team is not locally cloned or rows with any other still-actionable prompt.
- If cleanup fails during refresh, surface the failure to the Manager web layer.
  The existing web pattern is a non-fatal banner: "Saved locally, but could not refresh sightings. Reconnect to Hub and Refresh. (...)" after action-triggered refreshes, and an inline error on explicit refresh.

Exit gate:
Manager micro tests prove resolved rows are cleared even when dismissed, dismissed unresolved rows are not cleared, rows whose team is not locally cloned are not cleared, cleanup failures render the chosen non-fatal UI error, and refresh output remains current prompts rather than raw Hub rows.

### Phase 4 - Docs And Wrap-Up

- Update `architecture.md` only if the top-level lifecycle needs a concise architectural sentence.
- Update Hub spec with lifecycle, endpoint, stale window, and retry behavior.
- Update Hub spec to say sightings are not synced to peers and are not exposed to apps.
- Update Manager spec with the explicit clear-after-resolution rule.
- Add `.IN_PROGRESS/codex-issue-120-sightings-cleanup-policy/FOLLOW-UP.md` only if the branch discovers real follow-up work.
- Create the final design record and review note after implementation.

## Validation Plan

The validation needs to convince a skeptical reviewer of two things:
the issue goal was actually decided and implemented, and the app-bootstrap trust boundary stayed intact.

### Policy Evidence

- Docs define whether sightings are active observations, stale rows, dismissals, or audit history.
- Tests use that same vocabulary.
- There is no hidden second policy in code comments or UI text.

### Hub Boundary Evidence

- A micro test shows ordinary app sessions cannot clear sightings.
- A micro test shows a Manager/Core session can clear only the current participant's row.
- A micro test shows the Hub does not need NoteToSelf/team DB reads for cleanup.
- A code search verifies cleanup writes only `small_sea_collective_local.db`.

### Manager Correctness Evidence

- A micro test starts with an old raw Hub row that is now resolved by local registration and activation.
  After `refresh_app_sightings()`, Manager returns no prompt and the Hub row is gone.
- A micro test starts with a dismissed but resolved row.
  After refresh, Manager returns no prompt and the Hub row is gone.
- A micro test starts with a dismissed but unresolved row.
  After refresh, Manager returns no prompt and the Hub row remains.
- A micro test starts with a row whose team is not locally cloned.
  After refresh, Manager keeps the conservative prompt and the Hub row remains.
- A micro test verifies retry-after-clear records a new sighting with `seen_count = 1`.

### Stale Policy Evidence

- A micro test inserts one row just before and one row just after the stale cutoff.
  Only the older row is pruned.
- A micro test verifies `record_unknown_app_sighting(...)` does not prune unrelated stale rows.
- A micro test verifies fresh retries update `last_seen_at` and avoid accidental pruning.
- The stale clock is injectable or otherwise deterministic in tests.

### Regression Suite

Run at least:

```sh
uv run pytest packages/small-sea-hub/tests/test_app_bootstrap.py
uv run pytest packages/small-sea-manager/tests/test_app_sightings_ui.py
uv run pytest packages/small-sea-client/tests/test_client.py
git diff --check
```

If the implementation touches shared session behavior, also run:

```sh
uv run pytest packages/small-sea-hub/tests
uv run pytest packages/small-sea-manager/tests packages/small-sea-client/tests
```

## Risks

**Over-clearing active ambiguity.**
Manager must clear only when re-evaluation returns `None`.
Ambiguous friendly-name rows and unknown-team rows must remain visible or suppressed by existing disposition rules.

**Turning cleanup into authority.**
Deleting a local Hub observation must not grant access, register apps, activate apps, or rewrite Manager DBs.

**Clock flakiness.**
Stale pruning must use deterministic timestamps in tests.
Avoid sleeping tests.

**List-clear race.**
An app can retry between Manager listing a row and clearing it.
That is benign for resolved rows because resolution is based on berth existence, not `last_seen_at`; after the berth exists, the app's next request should succeed instead of recording another sighting.

**API widening.**
Do not add generic sighting mutation APIs.
This branch needs a narrow clear operation and stale prune behavior, not a Hub-admin database console.

## Resolved Clarifications

1. Use a 30-day stale window.
2. Store the default as a module-level constant and allow constructor injection for tests.
3. Use `POST /sightings/clear` with an exact tuple body, not a surrogate id.
4. Clear is idempotent and returns `deleted_count`.
5. Cleanup failures surface as non-fatal Manager refresh errors, following the existing web pattern.
6. Branch planning lives in `.IN_PROGRESS/{branch slug}/PLAN.md` per the current AGENTS.md instructions.
