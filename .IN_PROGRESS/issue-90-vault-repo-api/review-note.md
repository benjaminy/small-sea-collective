# Review note — Issue #90: convert vault.py gitCmd calls to the Repo API

Closes the last production `gitCmd` leakage from `shared-file-vault`, completing
the #78 abstraction work. All 24 `gitCmd` sites in `vault.py` now go through the
`cod_sync.repo.Repo` facade; the `gitCmd` import is gone. `repo.py` is unchanged
(no new API surface) and the 3 `CodSync` fetch/push sites are deliberately left
as-is. Net −39 lines.

## Where to look
- `vault.py`: private git helpers (now one-liners), the two merge sites
  (`_cod_pull`, `_cod_merge_ref`), and the public `create_niche`/`publish`/
  `status`/`log`.
- New error type `NothingToPublishError`; new test
  `test_publish_with_no_changes_raises`.

## Three intentional behavior changes (not accidents)
1. **No-op publish** now raises `NothingToPublishError` instead of the old leaked
   `GitCmdFailed` (callers slice the result as a hash, so returning `None` was not
   an option). New micro test covers it.
2. **Non-conflict merge failures** are no longer mislabeled as
   `MergeConflictError`; only real conflicts (non-empty conflict paths) map to it.
   Other failures surface as `RepoError`.
3. **`log()` commit hash** widened from abbreviated to full SHA (cosmetic; only
   rendered in templates).

## Error boundary
vault public API exposes vault-domain errors for *expected* conditions; *unexpected*
git failures surface as `cod_sync.repo.RepoError`. A full "wrap every RepoError"
boundary pass is intentionally out of scope — see FOLLOW-UP.md.

## Validation
Vault suite 66 passed / 3 skipped; manager suite 94 passed. Existing tests
unmodified; exactly one new micro test added.
