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
   Now `_require_clean_checkout` enforces cleanliness before any **merge or
   apply step that writes into the user checkout**.  Fetch and push do not
   require a clean checkout and should not gain that guard in this branch.

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

**Why `git -C git_dir` works**: the niche git dirs are initialized with
`git init --bare` then immediately set `core.bare = false` (see
`_init_git_dir`).  Git identifies the directory as a git dir by the presence
of `HEAD`, `objects/`, `refs/`, etc., and allows linked-work-tree operations
because `core.bare` is false.  It does not treat `git_dir` itself as a work
tree when invoked from a linked checkout — the linked checkout's `.git` pointer
file is what tells git which directory to update when merging.

Because `checkout` is a linked work tree of `git_dir` (the `.git` file points
at `git_dir`), remotes added via `git -C git_dir remote add ...` are stored in
`git_dir/config`, which the linked checkout shares — so
`git -C checkout merge bundle_remote/main` resolves the remote set up during
the preceding fetch step.

---

## Changes

### `shared_file_vault/vault.py`

**Remove:**
- `_niche_transit_dir(...)` — path helper, no longer needed.

**Drop `work_tree` from pure-ref helpers** (these never touched the work tree):
- `_resolve_ref(git_dir, ref_name)` — drop `work_tree`; use `--git-dir git_dir`
  only.  All callers updated accordingly.
- `_is_ancestor(git_dir, maybe_ancestor, descendant)` — same.

**Simplify `_cod_*` helpers** (remove `transit` param, remove `os.chdir`):

- `_cod_push(git_dir, remote)`:
  - `CodSync("cloud", bundle_tmp_dir=..., repo_dir=git_dir)`.
  - Bundle creation, tag, and update-ref touch only objects/refs; no work tree
    needed.

- `_cod_fetch(git_dir, remote, pin_to_ref)`:
  - `CodSync("cloud", bundle_tmp_dir=..., repo_dir=git_dir)`.
  - Fetch, bundle-verify, and update-ref touch only objects/refs.
  - `fetch_niche` requires no checkout and must continue to work from the CACHED
    state.  Using `repo_dir=git_dir` preserves that.

- `_cod_pull(git_dir, checkout, remote)`:
  - **Fetch step**: `CodSync(repo_dir=git_dir)` → `cod.fetch_from_remote(["main"])`.
  - **Merge step**: `CodSync(repo_dir=checkout)` → `cod.merge_from_remote(["main"])`.
  - `CodSync.merge_from_remote` already handles both branches internally:
    - With commits: `git -C checkout merge bundle_remote/main`
    - Without commits (initial pull): `git -C checkout checkout -B main bundle_remote/main`
  - Drop the `has_commits` branch split and the "reset work tree to HEAD"
    step — both were transit artefacts.
  - Remove the `_refresh_work_tree` call after `_cod_pull`; the merge already
    wrote files directly into `checkout`.

- `_cod_merge_ref(git_dir, checkout, ref_name)`:
  - `CodSync("cloud", bundle_tmp_dir=..., repo_dir=checkout)`.
  - With commits: `cod.merge_from_ref(ref_name)` →
    `git -C checkout merge ref_name`.
  - Without commits (initial merge): explicitly
    `gitCmd(["--git-dir", git_dir, "--work-tree", checkout, "checkout", "-B", "main", ref_name])`.
    Here `ref_name` is the parked peer ref (e.g. `refs/peers/<hex>/main`), which
    names an exact, already-present ref — no ambiguity.
  - Remove the `_refresh_work_tree` call after `_cod_merge_ref`; merge already
    wrote files into `checkout`.
  - Drop the `has_commits` reset-to-HEAD step for the same reason as `_cod_pull`.

**Update callers:**

- `create_niche`: remove the `_make_work_tree(git_dir, transit)` block.
- `push_niche`: drop transit lookup; call `_cod_push(git_dir, remote)`.
- `pull_niche`: remove transit creation; pass `checkout` to `_cod_pull`; remove
  `_refresh_work_tree` call (redundant — see above).
- `fetch_niche`: remove transit creation; call `_cod_fetch(git_dir, remote, ref_name)`.
- `merge_niche`: drop transit lookup; pass `checkout` to `_cod_merge_ref`;
  update `_resolve_ref` and `_is_ancestor` call sites (no work_tree arg);
  remove `_refresh_work_tree` call.

**`niche_conflict_paths`** — conflict semantics by residency:
- CHECKED_OUT: look up the registered checkout via `get_checkout`; call
  `_conflict_paths(git_dir, checkout)`.
- CACHED or REMOTE_ONLY (no checkout registered): return `[]` — there is no
  active merge in progress without a checkout.
- Stale registered checkout (path in DB but directory deleted): return `[]` for
  the same reason; a deleted directory cannot hold an in-progress merge.
- Update the docstring to say "user checkout" not "transit work tree".

**`peer_update_status`** — niche CACHED state:
- Drop the `work_tree = _niche_transit_dir(...)` line.
- Change the early-return guard from
  `if not git_dir.exists() or not work_tree.exists()` to
  `if not git_dir.exists()`.
- `_resolve_ref` and `_is_ancestor` no longer need a work tree, so CACHED
  niches (git_dir present, no checkout) continue to report parked SHA,
  `ready_to_merge`, and `already_merged` correctly.  This is important: the
  UI reads `ready_to_merge` from this function to tell the user there is new
  peer content to merge, even before they have a checkout.

### Tests: `tests/test_vault.py`

- Update docstrings that reference "transit work tree" to describe the actual
  invariant: the clean-checkout guard fires on the user checkout before any
  merge step.
- Remove any assertions that check for the existence of a `transit/` directory.

### Callers in `sync.py`, `web.py`, `cli.py`

No public API signature changes are expected.  However, verify that:
- `niche_conflict_paths` semantic change (checkout vs. transit) does not break
  any caller that expects a non-empty list for CACHED niches.
- `peer_update_status` correctly propagates `ready_to_merge` for CACHED niches
  through whatever surfaces it to the user.

---

## Micro tests

These are in addition to the existing suite, not a replacement.  Add them to
`tests/test_vault.py`.

1. **No transit dir created**: after `create_niche`, assert that no
   `transit/` subdirectory exists under the niche dir.

2. **Fetch without checkout pins the ref**: call `fetch_niche` on a CACHED niche
   (git_dir exists, no checkout registered), then assert that the parked peer
   ref resolves to the expected SHA.

3. **`peer_update_status` for CACHED niche**: after the fetch above, call
   `peer_update_status` and assert `parked_sha` is not None and
   `ready_to_merge` is True — without ever creating a checkout.

4. **Initial-history pull**: call `pull_niche` on a fresh niche git dir (no
   prior commits), assert the checkout is populated with the expected files
   afterward.

5. **Initial-history merge**: call `fetch_niche` + `add_checkout` +
   `merge_niche` on a niche with no prior commits, assert the checkout is
   populated.

6. **Merge conflict paths land in user checkout**: induce a merge conflict via
   `merge_niche`, then assert `niche_conflict_paths` returns the conflicted
   filenames (not an empty list).

7. **CWD preservation**: assert that `os.getcwd()` is identical before and
   after any vault operation that touches git (push, pull, fetch, merge).

---

## Out of scope (issue 78 follow-up)

- `gitCmd` is imported directly from `cod_sync.protocol` into `vault.py`.  This
  is the leakage that issue 78 will address.  Do not attempt to hide that import
  in this branch.
- No new abstraction layer over git operations — issue 78 covers that.
