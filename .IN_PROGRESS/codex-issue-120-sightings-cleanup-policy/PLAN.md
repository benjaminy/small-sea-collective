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
2. Resolved sightings are cleared automatically by Manager refresh after Manager re-evaluates current NoteToSelf/team state and proves that no prompt remains, even if Manager-local disposition would hide the row from the UI.
3. Stale unresolved sightings age out from the Hub after a 30-day no-retry window.
4. Dismissed prompts are still Manager-owned local disposition state.
5. If a dismissed but unresolved app keeps retrying, the Hub row keeps getting bumped and Manager keeps suppressing it locally.
6. If a dismissed but unresolved app stops retrying, it is still stale and may be pruned after the no-retry window.
7. If a cleaned-up or aged-out app retries later, the Hub records a fresh sighting.

The plan should revisit this policy before coding.
If the final decision changes, update this section first and make the implementation match the written policy.
The 30-day value is a v1 product heuristic, not an empirically proven constant.
It should be easy to change later if real usage suggests 14, 90, or some other window.

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

Working decision: Manager automatically clears resolved sightings during `TeamManager.refresh_app_sightings()` through a narrow Hub API.
The "explicit" part is the dedicated Hub endpoint, not a separate human click.

Rationale:

- Only Manager can safely know that participant registration and team activation now exist.
- The Hub should not inspect Manager DBs or infer resolution from its own local state.
- Explicit clear keeps the operation reviewable and testable.

### D3. How do stale unresolved sightings age out?

Working decision: the Hub prunes sightings whose `last_seen_at` is older than the configured stale window through a dedicated Manager-only `POST /sightings/prune-stale` endpoint.
The default stale window is 30 days.
Pruning is scoped to the participant derived from the Manager/Core session.
Manager refresh lists and evaluates sightings before pruning stale rows, then returns prompts computed from that pre-prune snapshot.

Rationale:

- A sighting that has not been bumped for a long time probably represents an app that stopped trying.
- Manager refresh is the canonical authenticated path and gives the cleanup one predictable trigger.
- `GET /sightings` remains read-only.
- Listing before pruning prevents the first refresh after a long absence from silently erasing old observations before the user sees them.
- Pruning during app-driven record would couple an ordinary bootstrap failure to mass deletion work.
- Explicit Manager-triggered pruning avoids a background worker without making a GET endpoint mutate state.
- Participant-scoped pruning preserves the boundary that a Manager session for participant A only mutates participant A's sighting rows.
- A future retry recreates the row, so cleanup is not a durable rejection.

The 30-day window matches the active-observation framing.
If the app has not retried in a month, the user has probably moved on.
Rows for abandoned participants whose Manager never refreshes again may remain.
That is an accepted v1 gap rather than a reason to let one participant's session delete another participant's observations.
The "shown once before pruning" behavior is participant-scoped, not Manager-installation-scoped.
If two Manager installations for the same participant refresh against the same Hub state, whichever one prunes first may prevent the other from seeing that stale row.
That is an accepted v1 limitation.

Sighting timestamps used for stale comparison must be canonical UTC ISO-8601 strings with exactly six fractional digits and a `+00:00` offset.
For example, an instant with zero microseconds is stored as `2026-05-01T12:00:00.000000+00:00`, not `2026-05-01T12:00:00+00:00`.
That makes lexicographic SQL comparison match chronological order for Hub-written sighting timestamps.
We considered an integer epoch-microseconds column for numeric comparison, but it would add schema surface without enough v1 benefit once canonical timestamp strings are enforced.

### D4. How are dismissed rows treated?

Working decision: dismissal affects display only.
Manager evaluates whether the sighting is resolved before applying disposition.
Resolved rows are cleared even when they were previously dismissed.
Dismissed but unresolved rows remain while they are fresh, but they are not exempt from stale pruning if the app stops retrying.

Rationale:

- Dismissal is a Manager presentation decision.
- If an app keeps retrying, the Hub should continue to reflect that observation.
- Manager can still suppress the prompt using local disposition state.
- We considered "dismissed means keep" and rejected it because it would turn a UI preference into storage retention policy.

### D5. What is the cleanup endpoint shape?

Working decision: use `POST /sightings/clear` with an exact tuple body plus a `last_seen_at` precondition from the list snapshot.
The `team_name` wire field is nullable because raw sightings may have `team_name = null`.
Manager must echo tuple values from `GET /sightings` without normalizing them.

```http
POST /sightings/clear
Authorization: Bearer <Core NoteToSelf session token>
Content-Type: application/json

{
  "app_name": "SharedFileVault",
  "team_name": "ProjectX",
  "client_name": "shared-file-vault:default",
  "last_seen_at": "2026-05-01T12:00:00.000000+00:00"
}
```

The timestamp above is illustrative.
The actual precondition is exact string equality against the `last_seen_at` value returned by `GET /sightings`.
That value is a canonical Hub-written timestamp string.

Response:

```json
{
  "deleted_count": 1
}
```

Rationale:

- The table has no surrogate id, and adding one would be schema churn without functional gain.
- `DELETE` with a body is poorly supported by some HTTP stacks.
- The tuple body matches the existing unique key and the `GET /sightings` row shape.
- The Hub derives `participant_hex` from the session token and does not accept it from the caller.
- `team_name`, `app_name`, `client_name`, and `last_seen_at` keys are all required.
  `team_name` may be JSON `null`; empty strings are literal values; there is no wildcard delete.
- The backend must match `team_name IS NULL` for JSON `null` and `team_name = ?` otherwise.
- The endpoint deletes only if the row still has the same `last_seen_at` string Manager evaluated.
- The endpoint is idempotent: if no row matches, or if a retry bumped `last_seen_at`, it returns success with `deleted_count = 0`.
  Manager treats `deleted_count = 0` as non-fatal.
- Manager and the client helper must never parse and reformat `last_seen_at` for the clear precondition.

The implementation should use the same `_require_session` dependency as `GET /sightings`, including the same `ss_session.app_name == Settings().app_name` Manager/Core guard.
That means the auth convention remains `Authorization: Bearer <token>`.

### D6. What is the stale prune endpoint shape?

Working decision: use `POST /sightings/prune-stale` with no required request body.
The endpoint should also accept `{}` because the existing client helper shape posts JSON bodies.

```http
POST /sightings/prune-stale
Authorization: Bearer <Core NoteToSelf session token>

<empty body>
```

Response:

```json
{
  "pruned_count": 3
}
```

The endpoint returns `200` on success.
Authorization failures use the same `401`/`403` behavior as `GET /sightings`.
The Hub derives `participant_hex` from the Manager/Core session token, applies the 30-day default stale window, and prunes only that participant's rows.

## Branch Contract

The branch is successful if all of the following are true:

1. The docs state a clear sighting lifecycle policy.
2. Manager clears Hub sightings only after re-evaluation says they are resolved.
3. Hub exposes only the minimum authenticated cleanup surface needed by Manager.
4. Stale rows age out predictably on Manager refresh, without background polling or mutation-on-GET.
5. Retrying an app after cleanup or age-out creates a fresh sighting.
6. Dismissed unresolved rows remain suppressible by Manager and continue to bump if the app retries.
7. Micro tests prove the policy at Hub, client, and Manager boundaries.

## Implementation Sketch

### Phase 0 - Freeze Policy

- Treat this plan's decisions as the branch policy unless implementation finds a contradiction.
- Record the final lifecycle policy paragraph in `packages/small-sea-hub/spec.md` and `packages/small-sea-manager/spec.md` before larger code edits.
  This is the policy pass; endpoint request/response details can land in Phase 4 after the API shape is implemented.
- Verify the no-flap property before Phase 1 coding.
  Once participant registration and team activation exist, Hub `_resolve_berth(...)` must open the session and `request_session(...)` must not call `record_unknown_app_sighting(...)` for that same tuple.
  If that property fails, stop and fix or re-plan before adding cleanup endpoints.
- Keep the branch plan under `.IN_PROGRESS/codex-issue-120-sightings-cleanup-policy/PLAN.md`, matching the current AGENTS.md workflow for nontrivial work.
  The historical reference plan path `Archive/branch-plan-issue-111-app-bootstrap-sightings.md` has been confirmed to exist.

### Phase 1 - Hub Cleanup Primitives

- Add a Hub backend method that deletes a sighting by `(participant_hex, app_name, team_name, client_name)`.
- Add a Hub backend method that prunes stale sightings by `last_seen_at`.
- Add a module-level default stale window constant of 30 days, with a `SmallSeaBackend(...)` constructor parameter for tests.
  Do not expose an environment variable or production config knob on this branch.
- Introduce a `_now()` method or constructor-injected clock and route both `record_unknown_app_sighting(...)` and stale pruning through it.
  Other `datetime.now(...)` call sites in `backend.py` are out of scope unless they directly participate in sighting cleanup.
- Introduce one timestamp formatting helper for sighting timestamps.
  It must normalize to UTC and use `isoformat(timespec="microseconds")`, preserving the `+00:00` offset.
  Both `record_unknown_app_sighting(...)` and prune cutoff construction must use this helper.
- Add a Manager-only `POST /sightings/prune-stale` endpoint that calls stale pruning scoped to the session participant.
  Do not prune from `record_unknown_app_sighting(...)`, `list_unknown_app_sightings(...)`, or the clear endpoint.
- Keep `GET /sightings` read-only.
- Compare stale timestamps as UTC ISO-8601 strings in SQL.
  This relies on the canonical six-fractional-digit timestamp format above; micro tests must cover an instant whose microsecond value is zero.
- Add an authenticated HTTP endpoint for Manager-driven clear.
- Keep authorization identical to `GET /sightings`: `_require_session`, then the Manager/Core `Settings().app_name` guard.
  Prefer a shared `_require_manager_session` dependency/helper so `GET /sightings`, `POST /sightings/clear`, and `POST /sightings/prune-stale` cannot drift.
- Require exact `app_name`, `team_name`, `client_name`, and `last_seen_at` fields in the clear payload.
  `team_name` is `Optional[str]`; treat empty strings as literal values, not wildcards.
- Return success with `deleted_count = 0` when no row matches.

Exit gate:
Hub micro tests cover successful clear, clear idempotency, guarded-clear mismatch, unauthorized clear, participant scoping, retry-after-clear, no-flap after resolved retry, stale pruning through `POST /sightings/prune-stale`, canonical timestamp formatting for zero-microsecond instants, read-only `GET /sightings`, and no pruning on record alone.

### Phase 2 - Client Helper

- Add a small client-session helper for clearing app sightings.
- Add a small client-session helper for pruning stale app sightings.
- Keep it scoped to confirmed sessions, parallel to `Session.app_sightings()`.
- Do not expose cleanup from ordinary app bootstrap clients.
- Build the clear payload directly from the sighting tuple keys: `app_name`, `team_name`, `client_name`, and `last_seen_at`.
  Preserve `team_name = None` and the `last_seen_at` string exactly as returned by the Hub.
- The prune helper may send `{}` because `SmallSeaClient._post(...)` currently requires a JSON object.
  The Hub endpoint itself must not require callers to send a JSON body.
- Update client-side docs or README material that describes `Session.app_sightings()` so the new clear helper is discoverable to Manager-side code.

Exit gate:
Client micro tests prove the clear request body shape, the prune helper path, and that bootstrap exceptions are not involved in cleanup.

### Phase 3 - Manager Integration

- In `TeamManager.refresh_app_sightings()`, evaluate `current_app_sighting_prompt(...)` before applying Manager-local disposition.
- During refresh, call `Session.app_sightings()` first, evaluate and clear resolved rows from that snapshot, then call the client prune helper once.
  Return prompts computed from the pre-prune snapshot so a long-absent Manager shows stale observations once before pruning removes them from future refreshes.
- When `current_app_sighting_prompt(...)` returns `None`, call cleanup with the loop-scope sighting tuple.
  No new tracking structure is needed.
- Apply `app_sighting_dismissed(...)` only to rows that still have a current prompt.
- The cleanup rule is exactly "clear iff `current_app_sighting_prompt(...)` returns `None`."
  Rows whose team is not locally cloned and all other still-actionable rows are excluded by that rule because the prompt is non-`None`.
- Accept one HTTP cleanup call per resolved row in v1.
  If a future branch sees enough rows for this to matter, it can add a batch endpoint.
- The new ordering intentionally does extra local DB work for dismissed-but-still-active rows.
  That cost is acceptable because it prevents dismissal from pinning resolved rows forever.
- If prune or any per-row cleanup fails during refresh, continue evaluating remaining rows when possible, collect the failures, and surface one summarized warning after the loop.
  The implementation may use a small result object or an exception carrying partial prompts, but the behavior is fixed: prompts already computed from the list snapshot are rendered alongside the warning.
  Resolved rows whose clear call failed are still omitted from prompts because their current prompt is `None`; the warning tells the user cleanup did not complete.
  If a resolved row is cleared and the later prune call fails, the resolved row remains omitted and the warning covers only the prune failure.
  If a stale unresolved row is evaluated and prune fails, the row is shown from the pre-prune snapshot and may appear again on a later refresh.
  The existing web pattern can carry the warning as a non-fatal refresh message: "Saved locally, but could not refresh sightings. Reconnect to Hub and Refresh. (...)" after action-triggered refreshes, and an inline error on explicit refresh.

Exit gate:
Manager micro tests prove stale pruning is called after listing/evaluation, resolved rows are cleared even when dismissed, guarded-clear mismatch is non-fatal, dismissed unresolved rows are not cleared, rows whose team is not locally cloned are not cleared, cleanup failures render the chosen non-fatal UI error alongside computed prompts, and refresh output remains current prompts rather than raw Hub rows.

### Phase 4 - Docs And Wrap-Up

- Update `architecture.md` only if the top-level lifecycle needs a concise architectural sentence.
- Update Hub spec with lifecycle, endpoint, stale window, and retry behavior.
- Update Hub spec to say sightings are not synced to peers and are not exposed to apps.
- Update Manager spec with the explicit clear-after-resolution rule.
- Preserve or add a Manager/Hub parity micro test for `current_app_sighting_prompt(...)` versus Hub `_resolve_berth` rejection predicates.
  Contract: every case where Hub `_resolve_berth` would reject an app-bootstrap request must produce a non-`None` prompt from `current_app_sighting_prompt(...)`.
  The converse is not required: Manager may conservatively show a prompt for a tuple that a fresh Hub request would now accept, especially when Manager lacks enough local team state to prove resolution.
  Cleanup makes predicate drift more costly because a false "resolved" result can delete the observation that would have exposed the mismatch.
- Document the resolved-app no-flap invariant verified in Phase 0.
- Update any OpenAPI/schema surface if the project has one for Hub endpoints.
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
- A micro test shows participant-scoped stale pruning does not delete another participant's stale row.
- A micro test shows `GET /sightings` does not prune or otherwise mutate rows.
- A micro test shows the Hub does not need NoteToSelf/team DB reads for cleanup.
- A code search verifies cleanup writes only `small_sea_collective_local.db`.
  Concretely, grep cleanup paths for SQL writes or path references involving `core.db`, NoteToSelf, or team DBs; those must not appear.

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
  This is mostly existing upsert behavior; this branch should preserve it rather than invent a second creation path.
- A micro test verifies a clear request with stale `last_seen_at` returns `deleted_count = 0` and leaves the bumped row in place.
- A micro test verifies `team_name = null` sightings can be cleared by echoing JSON `null` in the clear payload.
- A parity micro test proves Hub-rejected bootstrap states stay non-`None` in Manager prompt evaluation.
- A micro test proves resolved rows do not flap by being cleared and then immediately re-recorded on the next successful app request.

### Stale Policy Evidence

- A micro test calls `POST /sightings/prune-stale` with one row just before and one row just after the stale cutoff.
  Only the older row is pruned.
- A Manager refresh micro test proves stale rows are shown once from the pre-prune snapshot, then absent on the next refresh.
- A Manager refresh micro test proves a dismissed unresolved row that has gone stale is shown zero times because dismissal suppresses display, but is still pruned and can be recreated by a future retry.
- A micro test verifies `record_unknown_app_sighting(...)` does not prune unrelated stale rows.
- A micro test verifies `POST /sightings/clear` does not prune unrelated stale rows.
- A micro test verifies fresh retries update `last_seen_at` and avoid accidental pruning.
- The stale clock is injectable or otherwise deterministic in tests.
- A micro test pins canonical timestamp formatting and stale comparison for an instant with `microsecond == 0`.

### Regression Suite

Run at least:

```sh
uv run pytest packages/small-sea-hub/tests/test_app_bootstrap.py
uv run pytest packages/small-sea-manager/tests/test_app_sightings_ui.py
uv run pytest packages/small-sea-client/tests/test_client.py
```

Also run the whitespace check deliberately, separate from the micro tests:

```sh
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
The clear endpoint uses a `last_seen_at` precondition, so a freshly bumped row survives with `deleted_count = 0`.
That is slightly more conservative than strictly necessary for resolved rows, but it preserves the exact observation Manager did not evaluate.

**API widening.**
Do not add generic sighting mutation APIs.
This branch needs a narrow clear operation and stale prune behavior, not a Hub-admin database console.

## Resolved Clarifications

1. Use a 30-day stale window.
2. Store the default as a module-level constant and allow constructor injection for tests.
3. Use `POST /sightings/clear` with an exact tuple body, not a surrogate id.
4. Clear includes a `last_seen_at` precondition, is idempotent, and returns `deleted_count`.
5. Cleanup failures surface as non-fatal Manager refresh errors, following the existing web pattern.
6. Branch planning lives in `.IN_PROGRESS/{branch slug}/PLAN.md` per the current AGENTS.md instructions.
7. Stale pruning is participant-scoped.
8. Stale window and clock injection are in-process constructor seams for tests, not production config.
9. V1 accepts one cleanup HTTP call per resolved row.
10. Stale pruning happens through `POST /sightings/prune-stale`; `GET /sightings` remains read-only.
11. Manager refresh lists and evaluates before pruning so stale rows are visible once after long absence.
12. `team_name` is nullable on the clear wire shape and matched with `IS NULL`.
13. `last_seen_at` preconditions use exact string equality from the Hub list response.
14. Hub-written sighting timestamps use canonical UTC ISO-8601 strings with exactly six fractional digits and `+00:00`.
15. The no-flap property is a Phase 0 prerequisite, not a Phase 4 assumption.
16. Dismissed unresolved rows are still eligible for stale pruning when the app stops retrying.
17. The stale "shown once" guarantee is participant-scoped; multiple Manager installations for one participant are an accepted v1 limitation.
18. `POST /sightings/prune-stale` has no required body, though `{}` is accepted.
19. Manager/Hub prompt parity is one-way: Hub rejection implies Manager prompt, but not necessarily the reverse.
20. `git diff --check` is a deliberate whitespace check, separate from the micro test suite.
