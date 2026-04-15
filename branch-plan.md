# Branch Plan: Issue 82 — Remove transit work tree and simplify Cod Sync helper layer

## Background

The vault keeps an internal `transit` work tree (a linked checkout at
`niches/<name>/transit/`) that `_cod_push`, `_cod_pull`, `_cod_fetch`, and
`_cod_merge_ref` use as a stable, always-clean staging area for git bundle and
merge operations.

It existed for two reasons that issue #80 eliminated:

1. **Multiple checkouts** — you couldn't safely merge into any one user
   checkout.  Now there is exactly one checkout per niche.
2. **Dirty checkout at merge time** — a dirty work tree would block the merge.
   Now `_require_clean_checkout` enforces cleanliness before any sync step.

This branch removes transit entirely and makes the `_cod_*` helpers operate
directly on the bare git dir (for network/bundle operations that need no work
tree) and the single user checkout (for merge operations that do).

Issue 78 is related follow-up work: it catalogs `gitCmd` leakage outside
`cod_sync`. Issue 82 is a good opportunity to tighten the boundary in
`shared_file_vault/vault.py` but leaves the broader refactor to issue 78.

---

## Key insight enabling simplification

`CodSync` already accepts a `repo_dir` parameter.  When provided it runs every
git command as `git -C repo_dir` rather than relying on the process cwd.

The two roles that transit played can now be handled by passing the right
`repo_dir`:

| Operation | `repo_dir` | Work tree needed? |
|---|---|---|
| fetch / bundle / update-ref / push | `git_dir` | No — pure object/ref work |
| merge | `checkout` | Yes — git writes files into it |

Because `checkout` is a linked work tree of `git_dir` (has a `.git` pointer
file), git discovers `git_dir` automatically from it.  Remotes added via
`git -C git_dir remote add ...` are stored in `git_dir/config`, which the
linked checkout shares — so `git -C checkout merge bundle_remote/main` resolves
the remote set up during the fetch step.

---

## Changes

### `shared_file_vault/vault.py`

**Remove:**
- `_niche_transit_dir(vault_root, participant_hex, team_name, niche_name)` — path
  helper, no longer needed.

**Drop `work_tree` from pure-ref helpers** (these never touched the work tree):
- `_resolve_ref(git_dir, ref_name)` — drop `work_tree` param; use
  `--git-dir git_dir` only.
- `_is_ancestor(git_dir, maybe_ancestor, descendant)` — same.

**Simplify `_cod_*` helpers** (remove `transit` param, remove `os.chdir`
pattern):

- `_cod_push(git_dir, remote)`:
  - Create `CodSync("cloud", bundle_tmp_dir=..., repo_dir=git_dir)`.
  - No work tree involvement; `git bundle create` operates on refs/objects only.

- `_cod_fetch(git_dir, remote, pin_to_ref)`:
  - Create `CodSync("cloud", bundle_tmp_dir=..., repo_dir=git_dir)`.
  - Fetch/bundle/update-ref don't touch the work tree.

- `_cod_pull(git_dir, checkout, remote)`:
  - Fetch with `repo_dir=git_dir`; merge with `repo_dir=checkout`.
  - Drop the "reset work tree to HEAD before merging" step — it was only needed
    because transit could drift from HEAD when the user committed from their own
    checkout.  With checkout required to be clean, this is unnecessary.
  - On the no-commits path (initial pull), use explicit `--git-dir`/`--work-tree`
    flags for `git checkout main`.

- `_cod_merge_ref(git_dir, checkout, ref_name)`:
  - Create `CodSync("cloud", bundle_tmp_dir=..., repo_dir=checkout)`.
  - Drop the "reset work tree to HEAD" step for the same reason as above.
  - On the no-commits path, use explicit flags.

**Update callers** to drop transit creation and pass the right arguments:

- `create_niche`: remove the `_make_work_tree(git_dir, transit)` block.
- `push_niche`: drop transit lookup, call `_cod_push(git_dir, remote)`.
- `pull_niche`: remove transit creation block; pass `checkout` to `_cod_pull`.
- `fetch_niche`: remove transit creation block; call
  `_cod_fetch(git_dir, remote, ref_name)`.
- `merge_niche`: drop transit lookup; pass `checkout` to `_cod_merge_ref`,
  `_resolve_ref`, and `_is_ancestor`.
- `niche_conflict_paths`: drop transit lookup; look up the registered checkout
  via `get_checkout` and call `_conflict_paths(git_dir, checkout)`.  Return `[]`
  if no checkout is registered.
- `peer_update_status`: for the niche case, derive `work_tree` from the user
  checkout instead of the transit dir.

### Tests: `tests/test_vault.py`

- Remove docstring comments that reference "transit work tree" — replace with
  simpler language that describes the actual invariant being tested (clean
  checkout guard fires on the user checkout, not some internal staging area).
- Remove any assertions that check for the existence of a `transit/` directory.

### No changes needed in:
- `cod_sync/protocol.py` — `CodSync` already has `repo_dir`; no API changes.
- `sync.py`, `web.py`, `cli.py` — these call the public vault API, not internals.
- Other packages — transit was always internal to `shared_file_vault`.

---

## Test plan

Run the existing test suite; no new tests are expected because this is pure
internal cleanup with no behaviour change.

```
cd packages/shared-file-vault && python -m pytest tests/ -x -q
```

All existing tests that exercise push/pull/fetch/merge (including
`test_merge_clean_checkout_succeeds`, `test_merge_dirty_tracked_file_raises`,
scenario tests) must pass unchanged.

---

## Out of scope (issue 78 follow-up)

- `gitCmd` is imported directly from `cod_sync.protocol` into `vault.py`.  This
  is the leakage that issue 78 will address.  Do not attempt to hide that import
  in this branch.
- No new abstraction layer over git operations — issue 78 covers that.
