# Branch Plan: Simplify Vault (Issue #80)

## Goal

Remove DVCS features from Shared File Vault whose complexity cost exceeds their value:
1. **At most one local checkout per niche** — a niche is either not materialized on a device or has exactly one checkout location.
2. **Require clean checkout before merge-capable sync operations** — the user must commit or drop local changes before integrating fetched changes from elsewhere.
3. **Keep the `.git`-separate-from-checkout-directory design.**

---

## Current State Summary

- `checkouts.db` has a `checkout` table with no uniqueness constraint on `(team_name, niche_name)` — multiple checkouts are explicitly supported.
- `pull_niche()` and `merge_niche()` refresh **all** registered checkouts after a successful merge. There is no "is checkout clean?" guard.
- The web UI (`checkouts.html`, `niche_detail.html`) shows a list of checkouts, an "add checkout" form, and per-checkout "remove" buttons — all designed for the multi-checkout case.
- Tests: `test_add_multiple_checkouts`, `test_publish_refreshes_sibling_checkouts`, `test_multiple_checkouts_same_niche` explicitly exercise multiple-checkout behavior.

---

## Planned Changes

### 1. Enforce one-checkout-per-niche in `vault.py`

- In `add_checkout()`: if a checkout already exists for `(team_name, niche_name)`, raise an error. The user must explicitly remove the old checkout before attaching a new one.
- In the `checkout` table, add a UNIQUE constraint on `(team_name, niche_name)`.
- Add an explicit local schema/version marker for `checkouts.db`, and on mismatch recreate the local DB from scratch instead of doing migration work.
- Remove (or simplify) the multi-checkout refresh loop in `publish()`, `pull_niche()`, and `merge_niche()` — with at most one checkout, this reduces to a direct single-checkout refresh.
- Add a helper `get_checkout(vault_root, participant_hex, team_name, niche_name) -> str | None` that returns the single checkout path (or None).

### 2. Fetch/merge semantics after simplification

Post-simplification model:
- **fetch**: may happen automatically in the background and may also be triggered explicitly by the user. It gathers peer updates without changing the user's checkout contents.
- **merge**: remains a separate explicit action. Merge is the moment when fetched peer updates are applied to local visible state.
- **pull**: if retained in the CLI, it is only a convenience wrapper for `fetch + merge`, not a distinct model.

Edge case: **niche not materialized locally.** If a peer pushes while no checkout is attached, fetch is still allowed and should park the fetched refs locally. Later attaching a checkout does **not** auto-merge those parked refs. The user must still perform an explicit merge after attach. This is the least surprising first implementation and keeps "materialize files" separate from "integrate fetched changes."

This branch intentionally does **not** require architectural garbage collection of the transit work tree or adjacent Cod Sync helper complexity. That cleanup remains desirable, but it will be tracked as a follow-up issue instead of broadening this branch.

### 3. Require clean checkout before merge in `vault.py`

- Add `_is_checkout_clean(checkout_path, git_dir) -> bool` that runs `git status --porcelain` scoped to the user's checkout work tree. **Important:** always pass the user's checkout path explicitly — do not let this check accidentally target the transit or any other work tree.
- `--porcelain` output includes untracked files. Untracked files block merge-time operations just like tracked changes do. The primary motivation is UX simplicity: non-git users have no mental model for the tracked/untracked distinction, so "your folder must be clean" is one rule rather than a leaky git abstraction. Path-collision safety is a secondary benefit. This diverges from git's default merge behavior and should be noted in the function doc. Future relaxation (e.g. ignoring certain noise files) can be motivated by specific cases.
- In `merge_niche()` (and any combined `pull` wrapper): before doing merge work, call this check. Raise a new `DirtyCheckoutError` (with the list of modified paths) if unclean.
- Do the same for registry merge-time paths.
- Mirror this guard in `sync.py`'s combined/wrapper operations and `merge_via_hub()`.
- `fetch_niche()` does not need the clean-checkout guard because fetch alone does not change visible checkout state. The guard lives in merge-time paths. The UI model (background-or-manual fetch, then deliberate merge) gives the user a natural window to clean up before integrating changes.
- Do not add a partial "discard tracked files only" path in this branch. Merge-capable flows require a genuinely clean checkout; any destructive cleanup action should be a separate explicit feature.

### 4. Update `web.py` and templates

- Remove the "add another checkout" form from `checkouts.html` / `niche_detail.html` — replace it with a single checkout path display, a remove action, and an attach form that is only available when no checkout exists.
- Keep the `POST .../checkouts` route as an explicit attach action, not an implicit replace. If a checkout already exists, return an error that tells the user to remove it first.
- Simplify niche detail view: instead of iterating over checkouts, show the single checkout path (or a prompt to attach one).
- The fetch→merge flow should not present as a single button that immediately leads to merge. Instead: background fetch and explicit "check now" fetch both surface a "Changes from teammates available" banner or indicator; merge is a separate deliberate action the user takes when ready. This gives the user a natural window to clean up their work tree before merging, making the dirty-checkout error less surprising when it fires.
- Surface `DirtyCheckoutError` when merge is rejected — show which files are dirty and direct the user to publish or manually clean the work tree. Do not add an in-branch "move checkout" or destructive auto-clean flow.

### 5. Update `cli.py`

- Audit all CLI commands that mention checkouts; update help text and behavior to match single-checkout semantics.
- `checkout` command should fail clearly if the niche already has a checkout and explain that the user must remove it first.
- Keep separate `fetch` and `merge` semantics conceptually clear. If the CLI keeps a `pull` command, document it as a convenience wrapper for `fetch + merge`.
- Any merge-capable CLI path should surface `DirtyCheckoutError` with a clear message about what the user needs to do.

### 6. Update `spec.md`

- Remove or mark deprecated the multi-checkout description.
- Document the new constraint: "at most one checkout per niche per device."
- Document the new pull pre-condition: "checkout must be clean."

### 7. Update tests

- Remove `test_add_multiple_checkouts`, `test_publish_refreshes_sibling_checkouts`, `test_multiple_checkouts_same_niche` (or convert to tests that verify the new error is raised).
- Add micro tests:
  - `test_add_checkout_twice_raises()` — second `add_checkout` on same niche errors.
  - `test_merge_dirty_checkout_raises()` — merge with uncommitted changes raises `DirtyCheckoutError`.
  - `test_fetch_then_merge_clean_checkout_succeeds()` — baseline fetch+merge still works.
  - `test_merge_without_checkout_raises()` — parked updates cannot be merged into visible state until a checkout exists.
- Update `test_aspirational.py` scenarios to reflect single-checkout reality.

---

## Resolved Decisions

1. **Second `add_checkout` errors; it does not replace.** Under the hood the one-checkout invariant should be enforced by a real error, not a silent move. At the UX level, forcing the user to remove/detach the existing checkout before attaching a new one is acceptable for now. A dedicated "move checkout" workflow can come later if we still want it.

2. **No special discard workflow in this branch.** The simplest honest rule is that pull/merge requires a clean checkout. We should not spend branch scope on a half-safe cleanup path. If we later add a destructive cleanup action, it should be explicit and should blow away tracked and untracked local changes together, with strong warnings.

3. **No migration work; recreate local checkout metadata.** `checkouts.db` is device-local and reconstructable. For this pre-alpha branch we should add/keep schema version markers, but on mismatch simply recreate the local DB instead of writing compatibility migrations.

4. **Transit/code-complexity cleanup is deferred.** This branch should simplify the user-visible model and the branch-local invariants first. Cleaning up transit work trees and adjacent Cod Sync helper complexity is valuable, but it should be tracked as a separate follow-up issue rather than being a prerequisite for this branch.

5. **Automatic fetch is desired; merge stays explicit.** The product model should allow the app to fetch under the hood and also let the user ask to check right now. Either way, fetched changes do not become visible local state until the user explicitly merges.

6. **Follow-up work should be explicit GitHub issues.** This branch should create three follow-up issues:
   1. three-mode niche residency (`checked out`, `cached but not checked out`, `remote only`)
   2. garbage-collect transit work tree / git-tree complexity and simplify related sync code
   3. research and implement mitigations for incidental conflicts / benign noise

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- a niche can have at most one local checkout on a device, enforced both in Python code and in the SQLite schema
- attempts to attach a second checkout fail explicitly instead of silently replacing or partially mutating local state
- merge-time operations refuse to run when the relevant checkout is dirty, and they report which paths are blocking progress
- the branch does not add hidden destructive behavior such as partial discard, silent checkout moves, or schema-migration complexity
- the `.git`-separate-from-checkout-directory design remains intact
- fetched updates may be parked locally without a checkout, and attaching a checkout later still requires an explicit merge before those parked updates become visible files
- Shared File Vault behavior becomes simpler rather than more coupled: single-checkout helpers replace list-oriented logic where possible, and UI/CLI language matches runtime reality
- updated micro tests cover both happy paths and refusal paths, especially the ones most likely to regress the new invariants

## Validation Evidence To Gather In This Branch

- micro tests showing `add_checkout()` succeeds once and then fails on a second checkout for the same niche
- micro tests showing the DB schema rejects duplicate `(team_name, niche_name)` checkout rows
- micro tests showing fetch can park updates without an attached checkout
- micro tests showing attaching a checkout after fetch does not auto-merge parked updates
- micro tests showing merge-time paths reject dirty tracked changes — critically, these tests must put dirty files in the *user's checkout directory*, not in transit, and must verify the guard fires before any transit operations run (transit always resets itself to HEAD and would silently pass the check if tested there)
- micro tests showing dirty untracked files also block merge, so we do not hide collision cases behind partial cleanup
- micro tests showing clean fetch/merge still refresh the single checkout correctly
- web and CLI tests showing the user sees a clear "remove existing checkout first" message instead of implicit replacement
- web and CLI tests showing `DirtyCheckoutError` is surfaced with actionable guidance
- spec and aspirational test updates showing the product model now says "one checkout per niche per device" rather than preserving the old multi-checkout story
- wrap-up evidence includes filing the three intended follow-up GitHub issues so scope boundaries remain explicit

---

## Order of Work

1. `vault.py` — local schema/version handling, one-checkout uniqueness enforcement, and `get_checkout` helper
2. `vault.py` / `sync.py` — preserve explicit fetch-vs-merge semantics, including parked updates without checkout and explicit merge after attach
3. `vault.py` — `DirtyCheckoutError` + `_is_checkout_clean` + guards in merge-time paths
4. Micro tests for the new invariants and refusal paths
5. `web.py` + templates — single-checkout UI, automatic-plus-manual fetch, explicit merge, dirty-checkout error surfacing
6. `cli.py` — audit and update, keeping any `pull` command as a documented convenience wrapper only
7. `spec.md` — update documentation, remove multi-checkout claims, and document parked-fetch/explicit-merge behavior
8. Aspirational tests — update/remove multi-checkout scenarios
9. File follow-up GitHub issues for niche residency modes, transit/code-complexity cleanup, and incidental-conflict mitigation
