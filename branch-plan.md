# Branch Plan: Manager UI for app-bootstrap sightings review

**Branch:** `issue-118-manager-sightings-ui`
**Base:** `main`
**Primary issue:** #118 "Manager UI for app-bootstrap sightings review"
**Predecessors:** #111 (sightings + Manager plumbing), #122 (Core registration through generic primitives)
**Kind:** Thin web/UI slice on already-built primitives. Should be small.

## Purpose

Issue #111 landed everything *except* the human-facing review surface:

- Hub records app-bootstrap sightings, exposes them at `GET /sightings`, and rejects with structured reasons.
- Manager has `TeamManager.refresh_app_sightings`, `dismiss_participant_app_sighting`, `dismiss_team_app_sighting`, plus the generic `provisioning.register_app_for_participant` / `activate_app_for_team` primitives.
- Issue #122 removed the Core bypass so those primitives are the only registration writers.

The Manager web UI today has no way to surface any of that. A user whose Vault session was rejected with `participant_berth_missing` cannot see the sighting, register the app, activate it for a team, or dismiss the prompt without dropping into Python.

This branch fills exactly that gap: a thin htmx surface in `small_sea_manager.web` that walks the v1 four-reason loop end-to-end, plus the minimum `TeamManager` wrappers and template fragments needed to do so. The already-landed Hub feed and disposition plumbing stays the source of truth, with one necessary Manager-side refinement: refresh must re-evaluate old Hub observations against current local registration state so resolved prompts do not linger forever.

## Branch Contract

The branch is successful if all of the following are true:

1. From the index page, a logged-in user with an active NoteToSelf hub session can click **Refresh** and see all live, non-dismissed Hub sightings for their participant.
2. For each sighting reason, the UI offers exactly the next correct action set — no more, no less:
   - `app_unknown` → Register participant app, Activate for team, Dismiss participant prompt, Dismiss team prompt.
   - `participant_berth_missing` → Register participant app, Dismiss participant prompt, Dismiss team prompt.
   - `team_berth_missing` → Activate for team, Dismiss participant prompt, Dismiss team prompt.
   - `app_friendly_name_ambiguous` → Dismiss participant prompt, Dismiss team prompt; no register/activate action; surface explanatory text.
3. Dismissing a participant-level prompt removes the row from the rendered list across all teams; dismissing a team-scoped prompt removes only the team-scoped variant.
4. The full Vault loop (rejection → refresh → register → activate → success) can be driven entirely from the Manager web UI in a micro test, with no manual `provisioning.*` calls outside fixtures.
5. The web layer adds no new business logic: every route delegates to `TeamManager` methods, which delegate to the existing `provisioning` module.

If any of those is fuzzy at implementation time, stop and update this plan.

## Cut Line

**Must land**

- Two new `TeamManager` wrappers (`register_app_for_participant`, `activate_app_for_team`) so `web.py` follows the established pattern of "web -> TeamManager -> provisioning."
- A Manager-side current-state recheck for Hub observations so stale `app_unknown` rows become the current missing piece, or disappear once participant registration and team activation both exist. This belongs below `TeamManager`, not in Jinja or route code.
- A web refresh endpoint (route frozen in Phase 0) plus four POST routes: register, activate, dismiss-participant, dismiss-team.
- One `fragments/app_sightings.html` partial showing the live list with reason-aware action buttons.
- An entry on `index.html` (top-level, since sightings are participant-scoped, not team-scoped).
- Micro tests covering the full bootstrap loop through the web client, plus per-reason render checks and per-route happy-path checks.
- Spec note in `packages/small-sea-manager/spec.md` recording that the web surface now exposes the existing primitives.

**First things to cut if needed**

- `app_friendly_name_ambiguous` explanation polish beyond "two apps share this name; resolve in CLI."
- Any indicator of how many teams a participant-level dismissal will silence on this device beyond plain text.
- Display of historical metadata (`first_seen_at`, `seen_count`) beyond a single tooltip / muted line — the loop works without it.

**Out of scope (deferred to follow-up issues)**

- Background polling of sightings (issue #111 froze refresh as user-triggered for v1).
- Cross-device sighting visibility (#121).
- App-side bootstrap helper UX (#119).
- App unification UI (#113).
- Sync-side materialization opt-out (#117).
- Any change to the Hub `/sightings` shape or Hub-side filtering/disposition behavior.

## Phase 0 — Decisions to freeze before coding

The branch should commit the answers below in `branch-plan.md` *before* any tests or routes are written.

1. **Route shape.** The Manager web app does not currently have a sightings route. Working choice for v1:
   - `POST /app-sightings/refresh` returns the rendered fragment with the latest sightings (refresh is explicit, per #111).
   - `POST /app-sightings/register` (form: `app_name`).
   - `POST /app-sightings/activate` (form: `team_name`, `app_name`).
   - `POST /app-sightings/dismiss-participant` (form: `app_name`).
   - `POST /app-sightings/dismiss-team` (form: `team_name`, `app_name`).
   All five return the `app_sightings.html` fragment so the list re-renders in place after each action. Plain `GET` is intentionally skipped: refresh has a real network side effect (Hub round-trip via NoteToSelf session), so a verb-mismatched GET would be misleading.
2. **TeamManager wrappers.** Add `TeamManager.register_app_for_participant(app_name)` and `TeamManager.activate_app_for_team(team_name, app_name)` that delegate to `provisioning`. They mirror the existing `dismiss_*` wrapper pattern and exist solely to keep `web.py` from importing `provisioning` directly. They must not introduce any per-app or per-reason branching.
3. **Where it lives in the page.** Top-level card on `index.html`, between the existing Teams sidebar/main column and the Cloud-storage card. Sightings are participant-scoped, span multiple teams, and predate any team being joined locally, so they cannot live inside `team_detail.html`.
4. **Empty-state and unauthenticated-state copy.** If the NoteToSelf hub session is not active, the card shows "Connect to Hub to refresh sightings" and disables Refresh. If the session is active and refresh returns zero sightings, the card shows "No app-bootstrap prompts." No silent failure paths; any exception from `refresh_app_sightings()` renders an inline error in the same fragment.
5. **No new manager-locally stored state.** The card is stateless on the server: each Refresh round-trips the Hub, then applies deterministic local disposition/current-state filtering. The branch must not introduce any "last refresh" cache, snooze table, or notification queue. The four shipped reasons are the only contract.
6. **Stored observation vs current prompt.** Hub rows are observations and their `reason` only changes when an app retries. The Manager UI needs current prompts, so `TeamManager.refresh_app_sightings()` should return sightings after local re-evaluation:
   - dismissed rows are suppressed as today;
   - fully resolved rows are suppressed;
   - unresolved rows use the current missing reason (`app_unknown`, `participant_berth_missing`, `team_berth_missing`, or `app_friendly_name_ambiguous`), even if the Hub row's stored reason is older.
   This keeps the Hub contract unchanged while making the UI honest after each Manager action.

If any of these change during implementation, update this plan first.

## Phase 0.5 — Failing micro test skeleton first

Before any web-side code lands, write the failing skeleton for the end-to-end test in `packages/small-sea-manager/tests/test_manager.py` (or a new `test_app_sightings_ui.py` if it grows past ~150 lines):

```
test_vault_bootstrap_loop_via_manager_ui
  fresh participant + team, NoteToSelf passthrough session active
  Vault calls /sessions/request -> 409 app_unknown
  POST /app-sightings/refresh         -> fragment lists one row, "Register" + "Activate" + "Dismiss" buttons present
  POST /app-sightings/register        -> fragment lists same row but reason now team_berth_missing
  POST /app-sightings/activate        -> fragment shows "No app-bootstrap prompts"
  Vault retries /sessions/request     -> 200
```

The negative-path skeletons (per-reason render, dismissal hides row, dismissal does not register) should also exist in red form before Phase 1 starts, so contract drift can't slip in later. The exit gate for Phase 0.5 is: tests are red because routes are 404 or the fragment is missing, not because assertions disagree on wording.

## Phase 1 — TeamManager wrappers + web routes

- Add a provisioning-level helper used by `TeamManager.refresh_app_sightings()` to re-evaluate each Hub sighting against local NoteToSelf/team DB state. It should mirror the already-shipped Hub v1 predicates closely enough to answer only this question: "is this prompt still actionable, and if so, which of the four reasons is current?" It must not write state.
- Add `register_app_for_participant(self, app_name)` and `activate_app_for_team(self, team_name, app_name)` to `TeamManager` as one-line delegates to `provisioning`.
- Add the five routes in §Phase 0 #1 to `web.py`. Each route follows the existing Manager web pattern: catch the exception, render the same fragment with `error=str(e)`, and otherwise return the fragment only.
- The `register` route calls `mgr.register_app_for_participant(app_name)` then re-runs `mgr.refresh_app_sightings()` so the rendered list reflects the new reason. Same pattern for `activate`, `dismiss-participant`, and `dismiss-team`.
- Hub I/O is still only reached transitively through `TeamManager.refresh_app_sightings()`. The action routes also refresh after the local mutation, but every such call is the consequence of a deliberate user POST and still stays behind `TeamManager`.

Exit gate: green current-state refresh tests plus per-route happy-path tests; `web.py` contains no SQL, no Hub HTTP client calls, no `provisioning` import, and no reason-specific branching outside the fragment context passed to Jinja.

## Phase 2 — Template + index wiring

- New `fragments/app_sightings.html`. For each sighting:
  - Header line: `app_name` + reason badge + `team_name` when present + compact sighting metadata (`last_seen_at` or `seen_count`, depending on the Hub payload already available).
  - Reason explanation (one short sentence per reason).
  - Action buttons gated by reason:
    - `app_unknown` → Register participant app, Activate for team, Dismiss participant prompt, Dismiss team prompt.
    - `participant_berth_missing` → Register participant app, Dismiss participant prompt, Dismiss team prompt.
    - `team_berth_missing` → Activate for team, Dismiss participant prompt, Dismiss team prompt.
    - `app_friendly_name_ambiguous` → Dismiss participant prompt, Dismiss team prompt. Add a one-liner pointing the user at the CLI / future unification work.
- Empty state: explicit copy ("No app-bootstrap prompts.").
- Hub-disconnected state: render a passive card with the Refresh button disabled and the "Connect to Hub…" hint, so the user is not silently shown a stale list.
- Wire it into `index.html` as its own card with `hx-target` pointing at the fragment id. The Refresh button is the only way to populate the list; no auto-load on page render (refresh has a network cost and `index.html` must remain useful even when the Hub is offline).

Exit gate: render tests for each of the four reasons assert the expected form targets/button labels are present and the forbidden ones are absent. Empty-state and Hub-disconnected states each have a render test.

## Phase 3 — End-to-end micro tests + integrity probes

- Land the Phase 0.5 end-to-end test in green form.
- Add per-reason render tests (Phase 2 exit gate).
- Add current-state refresh tests: after participant registration, an old `app_unknown` Hub observation is returned as `team_berth_missing`; after team activation too, the same observation is suppressed as resolved.
- Add a dismissal test: participant-level dismissal hides every team-scoped variant of the same `app_name`; team-scoped dismissal hides only the (team, app) pair.
- Add a "dismissal does not register" test: after `POST /app-sightings/dismiss-participant`, NoteToSelf has zero `app` rows for the dismissed app and zero `team_app_berth` rows. This proves the UI does not silently call the registration primitive when the user picks Dismiss.
- Add an integrity test or explicit validation grep proving the web layer has no direct provisioning or Hub boundary violations:
  - `rg "small_sea_manager import provisioning|from small_sea_manager import provisioning|sqlite3|httpx|app_sighting_dismissed|INSERT|UPDATE|DELETE" packages/small-sea-manager/small_sea_manager/web.py` returns nothing.
  - `rg "register_app_for_participant\\(|activate_app_for_team\\(|dismiss_participant_app_sighting\\(|dismiss_team_app_sighting\\(" packages/small-sea-manager/small_sea_manager/web.py` returns only the route calls on `mgr`, not imports or helper functions.
- Re-run `packages/small-sea-hub/tests/test_app_bootstrap.py` to confirm no Hub-side regression.

Exit gate: every assertion in §Validation Strategy can be pointed at a named test or grep result.

## Phase 4 — Spec / doc sweep

- `packages/small-sea-manager/spec.md`: under §App Management / Hub sightings, add a short subsection describing the user-visible web flow and the route names. Make clear the routes are a thin presentation layer on top of the existing primitives.
- `packages/small-sea-manager/spec.md` open-questions: nothing new opens; if anything closes (e.g. "how does a user dismiss a prompt"), strike it.
- `architecture.md` does not need changes. The trust boundary, two-level model, and disposition semantics already in the spec remain unchanged; this branch is presentation-only.

## Validation Strategy (smart-skeptic test)

A reviewer should be able to convince themselves of all of the following without leaving the repo.

**The four-reason contract is real, not cosmetic.**
- One render test per reason asserts the exact set of action buttons (#114-style strict matching, not "contains").
- One render test asserts that `app_friendly_name_ambiguous` deliberately exposes only Dismiss buttons.
- Current-state refresh tests prove the UI is not replaying stale Hub reasons after Manager actions.
- The end-to-end test exercises the reason transition `app_unknown → team_berth_missing → success` via the UI.

**The web layer is thin.**
- `rg` over `packages/small-sea-manager/small_sea_manager/web.py` for `INSERT`, `UPDATE`, `DELETE`, `sqlite3`, `httpx`, `provisioning`, or `app_sighting_dismissed` returns nothing for new code.
- Every new action route is structurally a single try/except wrapping one local `mgr.<method>(...)` call followed by a `mgr.refresh_app_sightings()` re-render.
- The two new `TeamManager` methods are one-line delegates with no branching.

**No bypass for any app.**
- The dismissal-does-not-register test proves the UI cannot accidentally side-write registration state.
- The end-to-end test starts from zero registered apps and walks the public Manager API. There is no Vault-specific or Core-specific code path in `web.py`; #122's removal of the Core exception remains intact.

**Refresh is user-triggered.**
- No `setInterval`-style htmx polling on the sightings card. Confirmed by template grep: no `hx-trigger="every"`, no `load`-trigger on the sightings list.

**Hub-Manager boundary holds.**
- `web.py` makes no direct HTTP calls to the Hub; all Hub I/O still goes through `TeamManager` / `SmallSeaClient`.
- The current-state recheck reads only local Manager-owned DBs and does not mutate either Hub state or app/team registration state.
- Local-disposition writes still target only the Manager-local DBs (`device_local.db`, the per-team admission-events sidecar). Confirmed by re-running existing tests rather than adding new ones — the disposition writers are unchanged on this branch.

**Existing behavior is unchanged.**
- `uv run pytest packages/small-sea-manager/tests` passes.
- `uv run pytest packages/small-sea-hub/tests` passes.
- `uv run pytest packages/shared-file-vault/tests` passes — Vault still walks the rejection-and-registration loop the way #111 designed.

This section will be revisited at end-of-branch with concrete file paths, line numbers, and named tests.

## Risks and Open Questions

- **htmx swap target stability.** The card has to re-render in place after every action without disturbing other htmx targets on `index.html`. Risk: an action accidentally swaps the wrong fragment or replaces the whole index. Mitigation: per-route render tests assert the response body matches `app_sightings.html`, not `index.html`.
- **Refresh requires an active NoteToSelf session.** If the user clicks Refresh while the Hub session has expired, `refresh_app_sightings()` will raise. The Phase 1 exception path must catch and render an inline error rather than 500-ing. Test covers this case.
- **Stale Hub observations.** Hub rows are durable observations and remain after a prompt is fixed. If Manager refresh does not re-evaluate them locally, the UI would keep asking the user to fix an already-fixed app. Mitigation: Phase 1 adds current-state filtering below `TeamManager`, with micro tests for `app_unknown → team_berth_missing → resolved`.
- **Ambiguous-friendly-name guidance.** v1 has no in-UI unification flow. Worst-case the user dismisses both rows and never resolves the ambiguity. This is the same risk #111 already accepted; the only new exposure is that the UI now makes the dismissal one-click instead of code-only. Acceptable for v1.
- **Index page weight.** Adding a new top-level card grows `index.html`. If this card and the existing Cloud-storage card start to compete for attention, follow-up UX work belongs in a separate branch, not here.
- **Test file growth.** If `test_manager.py` grows past ~700 lines, split sightings tests into `test_app_sightings_ui.py`. Decision criterion: a reviewer should still be able to find a single test by reading filenames.

## Wrap-up checklist (filled in at branch close)

- [ ] All Phase exit gates met.
- [ ] `branch-plan.md` updated against landed behavior, then archived to `Archive/branch-plan-issue-118-manager-sightings-ui.md` per AGENTS.md.
- [ ] Validation section revised with concrete test names and file:line references.
- [ ] Final test counts pasted into the wrap-up summary.
- [ ] PR description points to this plan and lists each shipped reason mapped to its test.
