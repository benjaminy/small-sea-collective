# PLAN — Issue #90: Convert `vault.py` gitCmd calls to the Repo API

## Goal

Eliminate the last production leakage of `cod_sync.protocol.gitCmd` from
`shared-file-vault`, completing the #78 abstraction work.
Replace all ~24 direct `gitCmd` call sites in
`packages/shared-file-vault/shared_file_vault/vault.py` with calls to the
concrete `Repo` type in `cod_sync/repo.py`.

Non-goals (explicitly out of scope per the issue):
- The 3 `CodSync` usages (`_cod_push`, `_cod_pull` fetch, `_cod_fetch`) stay.
  Those are remote sync, not local repo ops.
- No test changes (test cleanup follows production cleanup in a later branch).
- No new `Repo` API surface — the existing API covers every operation family.

## Current state (verified)

`vault.py` already concentrates most git access in small private helpers, so the
conversion is well localized.
The 24 `gitCmd` sites cluster into these helpers/functions:

| vault location | git operation | Repo replacement |
|---|---|---|
| `_has_commits` (527) | `rev-parse HEAD` | `Repo(git_dir).has_commits()` |
| `_make_work_tree` (539) | `checkout HEAD -- .` (guarded by has-commits) | `repo.with_work_tree(dest).checkout_head()` |
| `_refresh_work_tree` (555) | `checkout HEAD -- .` | `repo.with_work_tree(dest).checkout_head()` |
| `_is_checkout_clean` (578) | `status --porcelain` | `repo.with_work_tree(co).status()` (see Nuance 5) |
| `_conflict_paths` (590) | `diff --name-only --diff-filter=U` | `repo.with_work_tree(wt).conflict_paths()` |
| `_resolve_ref` (601) | `rev-parse --verify <ref>` | `repo.resolve_ref(ref)` |
| `_is_ancestor` (611) | `merge-base --is-ancestor` | `repo.is_ancestor(a, d)` |
| `_init_git_dir` (636-637) | `init --bare` + `config core.bare false` | `Repo.init(git_dir)` (see Nuance 6) |
| `_cod_pull` (681-697) | `rev-parse --verify HEAD`, then `checkout -B main <r>` or `merge <r>` | `repo.head()`, `wt.checkout_branch(...)` / `wt.merge(...)` (see Nuance 1) |
| `_cod_merge_ref` (720,726) | `merge <ref>` or `checkout -B main <ref>` | `wt.merge(...)` / `wt.checkout_branch(...)` (see Nuance 1) |
| `create_niche` (776,779,780) | `add`, `add`, `commit` | `wt.stage([...])`, `wt.commit(msg)` |
| `publish` (901,903,905,907) | `add`/`add --all`, `commit`, `rev-parse HEAD` | `wt.stage(files)`, `wt.commit(msg)` (see Nuance 4) |
| `status` (916) | `status --porcelain` | `wt.status()` (see Nuance 2) |
| `log` (932) | `log --oneline -n N` | `repo.log(limit)` (see Nuance 3) |

Then remove `from cod_sync.protocol import gitCmd` (line 27).
`import cod_sync.protocol as CS` stays (still used for `CS.CodSync`).
Add `from cod_sync.repo import Repo` (plus its error types — see Nuance 1).

## Behavioral nuances (the risk surface — where a careless 1:1 swap breaks)

These are the reasons this is "careful" rather than "trivial".
Most must be preserved exactly because vault's existing tests and the web UI
depend on the observable contract; two are *intentional* changes (Nuances 1 and
4) flagged explicitly so they are decisions, not accidents.

1. **Error-type translation, with an intentional narrowing.** `Repo.merge()`
   raises `cod_sync.repo.ConflictError` on a real conflict (non-empty conflict
   paths) and `RepoError` on any other failure. Vault's public contract raises
   `MergeConflictError(conflict_paths)`. At `_cod_pull` and `_cod_merge_ref` we
   catch `ConflictError` and re-raise vault's `MergeConflictError` with the paths.
   - **Intentional change:** today vault turns *any* nonzero merge in the
     existing-history path into `MergeConflictError`, even when conflict paths are
     empty (i.e. a non-conflict failure is mislabeled as a conflict). Under the new
     contract a non-conflict `RepoError` will propagate as `RepoError` instead of
     masquerading as a conflict. This is the cleaner contract (AGENTS.md: prefer
     cleanest design; pre-alpha). The existing conflict tests still pass because
     they produce *real* conflicts (verified: `test_merge_conflict_paths_in_user_checkout`
     and the `test_scenarios.py` conflict test both edit the same line divergently).

2. **`status()` return shape.** Repo returns `{"xy", "path"}`; vault's public
   `status()` returns `{"status", "path"}` where `status == line[:2].strip()`.
   Remap inside vault. Covered by `test_vault.py::test_status`,
   `test_selective_publish`, and `status_panel.html`.

3. **`log()` return shape + hash width.** Repo returns `{"sha", "message"}` with a
   *full* SHA (`%H`); vault returns `{"hash", "message"}` with an *abbreviated*
   hash (`--oneline`). Remap `sha`→`hash`. The widening from short to full hash is
   cosmetic (templates `c.hash` just render it); confirm no test asserts hash
   length. Templates: `file_list.html`, `niche_detail.html`, `status_panel.html`.

4. **No-op publish must keep failing — do NOT let `publish` return `None`.**
   (Corrected after committee review; the original plan wrongly assumed publish
   always has staged content.) Public callers *can* call `publish` with no
   changes. Today `git commit` exits nonzero and `gitCmd` (default
   `raise_on_error=True`) raises `GitCmdFailed`. `Repo.commit()` instead returns
   `None` when nothing is staged. Consumers slice the result as a hash —
   `cli.py:379` (`commit_hash[:8]`, unguarded → would `TypeError`) and
   `web.py:320` (wrapped in `except Exception`). So returning `None` is a real
   regression.
   - **Resolution:** in `publish`, if `commit()` returns `None`, raise a new
     vault domain error `NothingToPublishError`. This preserves the invariant
     "publish never returns `None`/always fails on a no-op", and upgrades today's
     leaked low-level `GitCmdFailed` to a clean vault error (AGENTS.md: cleanest
     design). On the success path, return `commit()`'s value (the new HEAD) — no
     separate `rev-parse` needed.
   - `create_niche` always stages a new file, so its `commit()` never returns
     `None`; no special handling there.
   - **New micro test required:** publish with no changes raises
     `NothingToPublishError` (this behavior is otherwise untested — see Validation).

5. **`_is_checkout_clean` swallows errors.** It returns `False` (never raises)
   when the checkout is missing or git errors. `Repo.status()` raises
   `NoWorkTreeError`/`RepoError` instead. Preserve by keeping the existence guard
   and wrapping `status()` in `try/except RepoError -> return False` ("can't tell"
   ⇒ treat as not-clean, the safe direction).
   - **Coverage caveat (committee review):** the only caller,
     `_require_clean_checkout` (vault.py:1017), already pre-guards existence
     (`None`→`NoCheckoutError`, `not exists`→`StaleCheckoutError`), so the
     missing-checkout branch is dead via that path. The live concern is purely the
     `RepoError → False` branch on an *existing* checkout, which is hard to provoke
     without a contrived corrupt-repo fixture. Decision: **preserve the defensive
     swallow and mark this branch manually-validated by reasoning** rather than add
     a contrived test. (Flagging as a judgment call, not test-covered.)

6. **`Repo.init` adds `-b main`.** `Repo.init` runs `init --bare -b main` then
   `config core.bare false` — functionally equivalent to vault's `_init_git_dir`
   and additionally pins the initial branch to `main` (which vault already assumes
   everywhere). This is the canonical usage called out in the issue.

## Implementation steps

Work in clusters, running the vault suite after each so a regression is localized.

1. Swap imports: drop `gitCmd`, add `from cod_sync.repo import Repo, RepoError, ConflictError`.
2. Convert the read-only/private helpers (`_has_commits`, `_resolve_ref`,
   `_is_ancestor`, `_conflict_paths`, `_make_work_tree`, `_refresh_work_tree`,
   `_is_checkout_clean`, `_init_git_dir`). Run vault suite.
3. Convert the merge/checkout sites (`_cod_pull`, `_cod_merge_ref`) with the
   `ConflictError -> MergeConflictError` translation. Run vault suite.
4. Convert the write/introspection public functions (`create_niche`, `publish`,
   `status`, `log`) with shape preservation. Add `NothingToPublishError` and the
   no-op-publish guard (Nuance 4). Run vault suite.
5. Add the new no-op-publish micro test (Nuance 4). Run vault suite.
6. Confirm `gitCmd` count is 0 and the import is gone.

**Test-scope note:** the issue says "do NOT touch vault tests." Read as: do not
modify existing tests or fixtures. *Adding* a new micro test for the
no-op-publish contract is in-bounds and is the validation rigor AGENTS.md
demands. No existing test is edited.

## Validation (convince a skeptic)

**Goal accomplished:**
- `rg -c 'gitCmd' packages/shared-file-vault/shared_file_vault/vault.py` → `0`.
- No `from cod_sync.protocol import gitCmd` remains; `grep gitCmd` over the whole
  `shared-file-vault` package shows only test files (untouched) if any.
- `uv run pytest packages/shared-file-vault/tests -q` → all pass (this is the
  primary safety net: behavior is pinned by the existing suite, and we changed no
  tests, so green means observable behavior is preserved).
- `uv run pytest packages/small-sea-manager/tests -q` → no regressions
  (manager imports vault paths indirectly).

**Each nuance is individually covered (skeptic checklist):**
- Conflict translation (real conflict) → `test_scenarios.py` conflict test +
  `test_vault.py::test_merge_conflict_paths_in_user_checkout` (both assert
  `MergeConflictError`; both produce non-empty conflict paths — verified).
- Non-conflict merge narrowing (Nuance 1) → no existing test feeds a non-conflict
  merge failure; the change only affects that untested path, and the narrowing is
  the intended contract. Manually validated by reasoning.
- `status` shape → `test_vault.py::test_status`, `test_selective_publish`
  (assert `e["path"]`, clean ⇒ `len 0`).
- `log` shape → render path exercised by `test_web_sync.py` / `test_hub_sync.py`;
  manual check that no test asserts hash length.
- No-op publish (Nuance 4) → **new** micro test asserts `NothingToPublishError`.
  NB: this is *not* covered by the existing suite — most `publish(...)` calls
  ignore the return, and `test_publish_and_log` only checks the happy-path hash
  with real changes. The new test is the proof; do not cite the existing suite
  here. (Correction from committee review.)
- `_is_checkout_clean` error path → existence is pre-guarded by
  `_require_clean_checkout`; the residual `RepoError → False` branch is manually
  validated by reasoning, not test-covered (see Nuance 5).

**Repo integrity maintained or improved:**
- Coupling strictly *reduced*: vault no longer reaches into `cod_sync.protocol`'s
  low-level `gitCmd`; it depends only on the intended `cod_sync.repo.Repo` facade.
  Confirm with: `vault.py` imports from `cod_sync` are `CS` (CodSync) + `Repo` only.
- No new public surface added to `Repo` (the issue's invariant) — verify
  `git diff` touches `repo.py` zero times.
- `CodSync` fetch/push paths untouched — verify the 3 `CS.CodSync(...)` sites are
  byte-identical in the diff.
- Net line change should be roughly flat-to-negative (private helpers shrink to
  one-liners); a large positive delta would signal scope creep.

## Out of scope / follow-ups
- Test-suite cleanup that itself uses `gitCmd` or raw `git` (separate branch, per #78 staging).
- Optional: align the manager's `Repo.init(path/'.git').with_work_tree(path)` calls
  to the canonical `Repo.init(git_dir)` pattern (noted in the issue) — record in
  FOLLOW-UP.md if it looks worthwhile, do not do it here.
