# Design record — Issue #90: vault.py → Repo API

Boiled-down record of the non-obvious choices, for a future reader.

## What changed
All 24 direct `gitCmd` call sites in
`packages/shared-file-vault/shared_file_vault/vault.py` were replaced with the
`cod_sync.repo.Repo` facade, and the `from cod_sync.protocol import gitCmd`
import was removed. `import cod_sync.protocol as CS` stays — the 3 `CodSync`
fetch/push sites are remote sync, deliberately untouched. `repo.py` gained no new
methods. Net −39 lines in vault.py (private helpers collapsed to one-liners).

## Choices a future developer might revisit

1. **Error-boundary contract (the interesting one).** vault's public API names its
   *expected, meaningful* failure modes as vault-domain errors
   (`MergeConflictError`, the new `NothingToPublishError`, plus the existing
   `NoCheckoutError`/`StaleCheckoutError`/`DirtyCheckoutError`). *Unexpected*
   low-level git failures surface as `cod_sync.repo.RepoError`. The line is
   expected-condition → vault error vs unexpected-failure → repo-facade error —
   not "every error must be vault-typed." If we later want vault to expose *only*
   vault-domain errors, wrap `RepoError` at the boundary (see FOLLOW-UP.md).

2. **Intentional narrowing of merge failures.** Previously *any* nonzero merge in
   the existing-history path was raised as `MergeConflictError`, even with empty
   conflict paths (a non-conflict failure masquerading as a conflict).
   `Repo.merge()` raises `ConflictError` only on real conflicts; we translate that
   to `MergeConflictError` and let non-conflict `RepoError` propagate. Chosen over
   strict behavior-preservation per maintainer guidance ("improvements trump
   interface stability"; pre-alpha).

3. **No-op publish.** `Repo.commit()` returns `None` when nothing is staged, where
   the old `git commit` raised. Since callers slice publish()'s return as a hash
   (`cli.py`, `web.py`), publish now raises the new `NothingToPublishError` instead
   of returning `None`. This both preserves the "never returns None" invariant and
   upgrades the previously-leaked `GitCmdFailed` to a vault-domain error.

4. **Return-shape adapters kept at the public boundary.** `Repo.status()` returns
   `{"xy","path"}` and `Repo.log()` returns `{"sha","message"}` with a *full* SHA;
   vault's public `status()`/`log()` remap to `{"status","path"}` and
   `{"hash","message"}` to preserve the web-template/CLI contract. The log hash
   widened from abbreviated to full — cosmetic (templates just render `c.hash`).

5. **`_is_checkout_clean` error swallowing.** `Repo.status()` raises on git error;
   the old helper returned `False`. Preserved via `try/except RepoError → False`
   ("can't tell" ⇒ not-clean, the safe direction). The only caller pre-guards
   checkout existence, so the residual risk is narrow and is validated by
   reasoning, not a test.

## Validation summary
- `rg -n gitCmd vault.py` → no matches; only the 3 `CS.CodSync` sites remain.
- `repo.py` unchanged (git diff empty) — no new API surface.
- Full vault suite (66 passed, 3 skipped) and manager suite (94 passed) green.
- New micro test `test_publish_with_no_changes_raises` covers the one edge
  contract the existing suite did not exercise.
