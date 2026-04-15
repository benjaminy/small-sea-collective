# Branch Plan: Drop `.git` Pointer File (Issue #87)

Branch: `codex-issue-87-drop-git-pointer-file`

## Goal

Remove the `.git` pointer file that `_make_work_tree` writes into user checkout
directories. Switch every vault git operation that touches files to the explicit
`(--git-dir, --work-tree)` pair. The checkout directory becomes 100% user content.

## Background / Key Findings

The `.git` pointer file is written in one place:

```python
# vault.py:365
(dest / ".git").write_text(f"gitdir: {pathlib.Path(git_dir).resolve()}\n")
```

Most of vault.py already passes `--git-dir`/`--work-tree` explicitly and does **not**
rely on the pointer file. The two exceptions — the only callers that still depend on it
— both go through `CodSync(repo_dir=checkout)`, which wraps every git call as
`git -C <checkout> ...`, relying on git's implicit `.git` discovery:

1. **`_cod_merge_ref`** (vault.py:524) — creates `CodSync(repo_dir=checkout)` and calls
   `merge_from_ref(ref_name)`, which runs `git -C <checkout> merge <ref_name>`.

2. **`_fetch_from_vault_direct`** (vault.py ~507) — creates a second
   `CodSync(repo_dir=checkout)` (`cod_merge`) and calls `merge_from_remote(["main"])`,
   which runs `git -C <checkout> rev-parse HEAD` then either
   `git -C <checkout> checkout -B main cloud-codsync-bundle-tmp/main` (fresh repo) or
   `git -C <checkout> merge cloud-codsync-bundle-tmp/main`.

Both can be rewritten as direct `gitCmd` calls with `--git-dir`/`--work-tree`, which
eliminates the need for CodSync at these sites entirely — a net simplification.

One test asserts the pointer file exists (test_vault.py:63-64); it needs updating.
One docstring references it (vault.py:527-529); it needs updating.

## Steps

### 1. Rewrite `_cod_merge_ref` to use explicit flags

Remove the `CodSync` object. Replace with a direct `gitCmd` call:

```python
def _cod_merge_ref(git_dir, checkout, ref_name):
    """Merge a parked peer ref into the user checkout using explicit git-dir/work-tree."""
    git_prefix = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]
    result = gitCmd(git_prefix + ["merge", ref_name], raise_on_error=False)
    return result.returncode
```

Callers already check the return code and raise `MergeConflictError` on non-zero.

### 2. Rewrite the `cod_merge` half of `_fetch_from_vault_direct`

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

### 3. Remove the `.git` pointer write in `_make_work_tree`

Delete the line:
```python
(dest / ".git").write_text(f"gitdir: {pathlib.Path(git_dir).resolve()}\n")
```

Update the docstring to drop the "Writes a .git pointer file" sentence.

### 4. Update the comment in `_fetch_from_vault_direct` / old `_cod_merge_ref` docstring

vault.py:527-529 currently reads:
> The checkout's .git pointer resolves git_dir automatically, so remotes
> set up on git_dir (e.g. from a preceding fetch) are visible here.

Replace with something like:
> Explicit --git-dir/--work-tree flags locate the repo; remotes set up on
> git_dir during fetch are visible here.

### 5. Update `test_vault.py`

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

## Behavior change to call out

A user who `cd`s into their checkout and runs `git status` will see
"not a git repository." This is intentional per the issue — the product stance is
that users should not need to think about git.

## Out of scope

- Cross-package `gitCmd` cleanup (#78)
- Giving `CodSync` a `(git_dir, work_tree)` constructor mode — that is the future
  shape anticipated by #78 and #87's architecture notes, but vault can use plain
  `gitCmd` calls in the meantime.
- Any change to residency modes or the checkout registry schema.
