# Branch Plan: Simplify Vault (Issue #80)

## Goal

Remove DVCS features from Shared File Vault whose complexity cost exceeds their value:
1. **At most one local checkout per niche** — a niche is either not materialized on a device or has exactly one checkout location.
2. **Require clean checkout before pulling** — the user must commit or drop local changes before accepting changes from elsewhere.
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

### 2. Require clean checkout before pull/merge in `vault.py`

- Add `_is_checkout_clean(git_dir, checkout_path) -> bool` that checks `git status --porcelain` for the checkout's work tree.
- In `pull_niche()` (and `merge_niche()`): before doing any network/merge work, call this check. Raise a new `DirtyCheckoutError` (with the list of modified paths) if unclean.
- Do the same in `pull_registry()` / `merge_registry()`.
- Mirror this guard in `sync.py`'s `pull_via_hub()` and `merge_via_hub()`.
- Do not add a partial "discard tracked files only" path in this branch. For now, pulling requires an actually clean checkout; any future destructive cleanup action should be an explicit separate feature.

### 3. Update `web.py` and templates

- Remove the "add another checkout" form from `checkouts.html` / `niche_detail.html` — replace it with a single checkout path display, a remove action, and an attach form that is only available when no checkout exists.
- Keep the `POST .../checkouts` route as an explicit attach action, not an implicit replace. If a checkout already exists, return an error that tells the user to remove it first.
- Simplify niche detail view: instead of iterating over checkouts, show the single checkout path (or a prompt to attach one).
- Surface `DirtyCheckoutError` in the UI when a pull is rejected — show which files are dirty and direct the user to publish, manually clean the work tree, or remove the checkout. Do not add an in-branch "move checkout" or destructive auto-clean flow.

### 4. Update `cli.py`

- Audit all CLI commands that mention checkouts; update help text and behavior to match single-checkout semantics.
- `checkout` command should fail clearly if the niche already has a checkout and explain that the user must remove it first.
- `pull` command: surface `DirtyCheckoutError` with a clear message about what the user needs to do.

### 5. Update `spec.md`

- Remove or mark deprecated the multi-checkout description.
- Document the new constraint: "at most one checkout per niche per device."
- Document the new pull pre-condition: "checkout must be clean."

### 6. Update tests

- Remove `test_add_multiple_checkouts`, `test_publish_refreshes_sibling_checkouts`, `test_multiple_checkouts_same_niche` (or convert to tests that verify the new error is raised).
- Add micro tests:
  - `test_add_checkout_twice_raises()` — second `add_checkout` on same niche errors.
  - `test_pull_dirty_checkout_raises()` — pull with uncommitted changes raises `DirtyCheckoutError`.
  - `test_pull_clean_checkout_succeeds()` — baseline pull still works.
  - `test_merge_dirty_checkout_raises()` — same guard for merge.
- Update `test_aspirational.py` scenarios to reflect single-checkout reality.

---

## Resolved Decisions

1. **Second `add_checkout` errors; it does not replace.** Under the hood the one-checkout invariant should be enforced by a real error, not a silent move. At the UX level, forcing the user to remove/detach the existing checkout before attaching a new one is acceptable for now. A dedicated "move checkout" workflow can come later if we still want it.

2. **No special discard workflow in this branch.** The simplest honest rule is that pull/merge requires a clean checkout. We should not spend branch scope on a half-safe cleanup path. If we later add a destructive cleanup action, it should be explicit and should blow away tracked and untracked local changes together, with strong warnings.

3. **No migration work; recreate local checkout metadata.** `checkouts.db` is device-local and reconstructable. For this pre-alpha branch we should add/keep schema version markers, but on mismatch simply recreate the local DB instead of writing compatibility migrations.

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- a niche can have at most one local checkout on a device, enforced both in Python code and in the SQLite schema
- attempts to attach a second checkout fail explicitly instead of silently replacing or partially mutating local state
- pull and merge operations refuse to run when the relevant checkout is dirty, and they report which paths are blocking progress
- the branch does not add hidden destructive behavior such as partial discard, silent checkout moves, or schema-migration complexity
- the `.git`-separate-from-checkout-directory design remains intact
- Shared File Vault behavior becomes simpler rather than more coupled: single-checkout helpers replace list-oriented logic where possible, and UI/CLI language matches runtime reality
- updated micro tests cover both happy paths and refusal paths, especially the ones most likely to regress the new invariants

## Validation Evidence To Gather In This Branch

- micro tests showing `add_checkout()` succeeds once and then fails on a second checkout for the same niche
- micro tests showing the DB schema rejects duplicate `(team_name, niche_name)` checkout rows
- micro tests showing `pull_niche()` and `merge_niche()` reject dirty tracked changes
- micro tests showing dirty untracked files also block pull/merge, so we do not hide collision cases behind partial cleanup
- micro tests showing clean pull/merge still refresh the single checkout correctly
- web and CLI tests showing the user sees a clear "remove existing checkout first" message instead of implicit replacement
- web and CLI tests showing `DirtyCheckoutError` is surfaced with actionable guidance
- spec and aspirational test updates showing the product model now says "one checkout per niche per device" rather than preserving the old multi-checkout story

---

## Order of Work

1. `vault.py` — local schema/version handling, one-checkout uniqueness enforcement, and `get_checkout` helper
2. `vault.py` — `DirtyCheckoutError` + `_is_checkout_clean` + guards in pull/merge
3. `sync.py` — propagate dirty-checkout guard
4. Micro tests for the new invariants and refusal paths
5. `web.py` + templates — single-checkout UI, explicit remove-then-attach semantics, and dirty-checkout error surfacing
6. `cli.py` — audit and update
7. `spec.md` — update documentation and remove multi-checkout claims
8. Aspirational tests — update/remove multi-checkout scenarios
