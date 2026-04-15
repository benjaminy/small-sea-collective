# Branch Plan: Drop `.git` Pointer File (Issue #87)

Branch: `codex-issue-87-drop-git-pointer-file`

## Goal

Remove the `.git` pointer file that `_make_work_tree` writes into user checkout
directories. Switch every Shared File Vault git operation that touches checkout
files to the explicit `(--git-dir, --work-tree)` pair. The checkout directory
becomes 100% user content, with no vault/git bookkeeping files written into it.

## Background / Key Findings

The `.git` pointer file is written in one place:

```python
# vault.py:365
(dest / ".git").write_text(f"gitdir: {pathlib.Path(git_dir).resolve()}\n")
```

Most of `vault.py` already passes `--git-dir`/`--work-tree` explicitly and does
**not** rely on the pointer file. The two exceptions — the only callers that
still depend on it — both go through `CodSync(repo_dir=checkout)`, which wraps
every git call as `git -C <checkout> ...`, relying on git's implicit `.git`
discovery:

1. **`_cod_merge_ref`** — creates `CodSync(repo_dir=checkout)` and calls
   `merge_from_ref(ref_name)`, which runs `git -C <checkout> merge <ref_name>`.
   Its no-local-history path already uses explicit flags and must be preserved.

2. **`_cod_pull`** — creates a second `CodSync(repo_dir=checkout)` (`cod_merge`)
   and calls `merge_from_remote(["main"])`, which runs
   `git -C <checkout> rev-parse HEAD` then either
   `git -C <checkout> checkout -B main cloud-codsync-bundle-tmp/main` (fresh repo)
   or `git -C <checkout> merge cloud-codsync-bundle-tmp/main`.

Both can be rewritten as direct `gitCmd` calls with `--git-dir`/`--work-tree`, which
eliminates the need for CodSync at these sites entirely — a net simplification.

One micro test asserts the pointer file exists (`test_vault.py`); it needs
updating. The `_make_work_tree`, `_cod_pull`, and `_cod_merge_ref` docstrings or
comments should also stop saying merge works because of a checkout `.git`
pointer.

## Design Constraints / Invariants

- `CodSync(repo_dir=git_dir)` remains acceptable for fetch and push paths because
  those operations do not write into the user checkout.
- `CodSync(repo_dir=checkout)` should disappear from `shared_file_vault/vault.py`.
  Checkout-writing git operations should use `gitCmd` with explicit
  `--git-dir` and `--work-tree` flags.
- Preserve the current conflict behavior: merge conflicts still raise
  `MergeConflictError(_conflict_paths(git_dir, checkout))`.
- Preserve the current empty-local-history behavior: first merge/pull into an
  unborn local repo checks out the fetched or parked `main` history instead of
  trying to merge into non-existent `HEAD`.
- Preserve the current CWD behavior: no operation should rely on `os.chdir`, and
  the existing CWD preservation micro test should still pass.
- Do not change registry/checkouts schema, residency modes, or remote protocol
  behavior.

## Steps

### 1. Rewrite `_cod_merge_ref` to use explicit flags

Remove the `CodSync(repo_dir=checkout)` object. Keep the current branching
behavior:

```python
def _cod_merge_ref(git_dir, checkout, ref_name):
    """Merge a parked peer ref into the user checkout using explicit git-dir/work-tree."""
    git_prefix = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]
    if _has_commits(git_dir):
        result = gitCmd(git_prefix + ["merge", ref_name], raise_on_error=False)
        if result.returncode != 0:
            raise MergeConflictError(_conflict_paths(git_dir, checkout))
    else:
        gitCmd(git_prefix + ["checkout", "-B", "main", ref_name])
```

This keeps the existing public behavior of `_cod_merge_ref`: it raises on
conflict internally and returns `None` on success.

### 2. Rewrite the `cod_merge` half of `_cod_pull`

The fetch half (`cod_fetch = CodSync(repo_dir=git_dir)`) does not use the pointer file
and can stay as-is. Only the merge half needs to change.

Replace:
```python
cod_merge = CS.CodSync("cloud", bundle_tmp_dir=btd, repo_dir=checkout)
exit_code = cod_merge.merge_from_remote(["main"])
```

With inline logic that mirrors `merge_from_remote` but uses explicit flags:

```python
tmp_remote = "cloud-codsync-bundle-tmp"
git_prefix = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]
head_result = gitCmd(["--git-dir", str(git_dir), "rev-parse", "--verify", "HEAD"],
                     raise_on_error=False)
if head_result.returncode != 0:
    # Unborn branch — adopt fetched branch as initial local branch.
    result = gitCmd(git_prefix + ["checkout", "-B", "main", f"{tmp_remote}/main"],
                    raise_on_error=False)
    exit_code = result.returncode
else:
    result = gitCmd(git_prefix + ["merge", f"{tmp_remote}/main"], raise_on_error=False)
    exit_code = result.returncode
```

Use the same temp remote name that `CodSync.bundle_tmp()` uses for this
`bundle_tmp_dir`; do not introduce a second temp remote convention.

### 3. Remove the `.git` pointer write in `_make_work_tree`

Delete the line:
```python
(dest / ".git").write_text(f"gitdir: {pathlib.Path(git_dir).resolve()}\n")
```

Update the docstring to make the new behavior explicit:

> Create `dest` and populate it from `git_dir` if the repo has commits. The
> checkout receives only user files; git metadata stays in `git_dir`.

### 4. Update comments/docstrings that describe pointer-file behavior

vault.py:527-529 currently reads:
> The checkout's .git pointer resolves git_dir automatically, so remotes
> set up on git_dir (e.g. from a preceding fetch) are visible here.

Replace with something like:
> Explicit --git-dir/--work-tree flags locate the repo; remotes set up on
> git_dir during fetch are visible here.

### 5. Update and add micro test coverage

Remove the assertion at lines 63-64:
```python
# .git pointer file exists in the checkout
assert (dest / ".git").exists()
```

Optionally replace with a negative assertion that confirms the checkout is clean:
```python
# checkout contains no vault/git bookkeeping files
assert not (dest / ".git").exists()
```

Also make sure at least one micro test proves each of these paths still works
without a checkout `.git` file:

- fresh checkout creation via `add_checkout`
- initial pull/merge into an unborn repo
- merge from a parked peer ref (`merge_niche` or `merge_registry`)
- merge-conflict reporting still raises `MergeConflictError` and reports
  conflict paths

## Behavior change to call out

A user who `cd`s into their checkout and runs `git status` will see
"not a git repository." This is intentional per the issue — the product stance is
that users should not need to think about git.

## Validation Plan

The validation should convince a skeptical reviewer in two ways: the branch goal
is achieved, and repo integrity did not regress.

### Goal validation

- Static search:
  - `rg -n "CodSync\\([^\\n]*repo_dir=checkout|gitdir:|\\.git pointer" packages/shared-file-vault/shared_file_vault packages/shared-file-vault/tests`
  - Expected: no production `CodSync(repo_dir=checkout)`, no pointer-file write,
    and no stale pointer-file comments in source or micro tests.
- Focused micro tests:
  - `uv run pytest packages/shared-file-vault/tests/test_vault.py -q`
  - Expected: `test_add_checkout` asserts `(dest / ".git")` does not exist, and
    existing pull/merge/conflict/CWD scenarios still pass.
- Manual git behavior check, if needed while debugging:
  - after a checkout is created, `git -C <checkout> status` should fail with
    "not a git repository";
  - `git --git-dir <vault git dir> --work-tree <checkout> status --porcelain`
    should still succeed.

### Integrity validation

- Shared File Vault package sweep:
  - `uv run pytest packages/shared-file-vault/tests -q`
  - Expected: broader vault/web/hub sync behavior stays green with local mocks.
- Cod Sync regression check:
  - `uv run pytest packages/cod-sync/tests -q`
  - Expected: no unintended change to `CodSync` protocol semantics.
- Coupling review:
  - The implementation should reduce Shared File Vault's dependence on
    `CodSync` checkout semantics while leaving Cod Sync's public API unchanged.
  - No new compatibility shim is needed because the project is pre-alpha.
  - No network-dependent validation is required for this change; keep tests
    local-only.

## Out of scope

- Cross-package `gitCmd` cleanup (#78)
- Giving `CodSync` a `(git_dir, work_tree)` constructor mode — that is the future
  shape anticipated by #78 and #87's architecture notes, but vault can use plain
  `gitCmd` calls in the meantime.
- Any change to residency modes or the checkout registry schema.
- Making user checkouts behave like ordinary command-line git repos.
