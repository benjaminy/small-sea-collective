# Branch Plan: gitCmd Leakage — Catalog + Starter Refactor (Issue #78)

**Branch:** `codex-issue-78-gitcmd-leakage-catalog`
**Base:** `main`
**Primary issue:** #78 "Fix gitCmd sprawl"
**Date refreshed:** 2026-04-15
**Related packages:** `packages/cod-sync`, `packages/shared-file-vault`,
`packages/small-sea-manager`, `packages/small-sea-hub`

## Goal

Reduce the cross-package coupling on `cod_sync.protocol.gitCmd`. Concretely:

1. A **refreshed leakage catalog** (done — see appendix).
2. A **concrete `Repo` type** in cod-sync that wraps the `(git_dir, work_tree)`
   pair and exposes generic DVCS methods. `gitCmd` becomes a private
   implementation detail.
3. A **starter refactor** that converts a real call-site cluster to the new API,
   proving the design works before a broader rollout.

## Context

After #80 (one checkout per niche), #81 (three residency modes), #82 (transit
removal), and #87 (drop .git pointer file), the codebase is in a clean state
for this work. In particular, #87 established the explicit `(--git-dir,
--work-tree)` pattern in vault, which maps directly onto the `Repo` constructor.

## Design: the `Repo` type

A value type in `cod_sync.protocol` (or a new `cod_sync.repo` module):

```python
class Repo:
    """A local git repository identified by its git_dir and optional work_tree.

    work_tree=None means CACHED mode (bare-style, no checkout files).
    """

    def __init__(self, git_dir, work_tree=None):
        self.git_dir = pathlib.Path(git_dir)
        self.work_tree = pathlib.Path(work_tree) if work_tree else None
```

### Methods — read-only introspection

These are safe in both CACHED and CHECKED_OUT modes:

- `head() -> str | None` — `rev-parse HEAD`, None if unborn
- `has_commits() -> bool` — whether HEAD resolves
- `resolve_ref(ref_name) -> str | None` — `rev-parse --verify <ref>`
- `is_ancestor(maybe_ancestor, descendant="HEAD") -> bool`
- `log(limit=10) -> list[dict]` — `git log --oneline`

### Methods — work-tree operations

These require `work_tree` to be set. When called on a Repo with
`work_tree=None`, they raise `NoWorkTreeError` (see Exception Model below)
instead of letting the underlying git command fail with a cryptic message.

- `status() -> list[dict]` — `git status --porcelain`
- `stage(files=None)` — `git add <files>` or `git add --all`
- `commit(message) -> str | None` — SHA on success, None if nothing staged
- `checkout_head()` — `git checkout HEAD -- .` (refresh work tree)
- `checkout_branch(branch, start_point=None)` — `git checkout -B ...`
- `merge(ref)` — `git merge <ref>`; raises `ConflictError` on conflict
- `conflict_paths() -> list[str]` — `git diff --name-only --diff-filter=U`

### Methods — repo setup

- `@staticmethod init(git_dir, initial_branch="main") -> Repo` —
  `git init --bare -b <initial_branch>` + `core.bare = false`

### Exception Model

All Repo errors derive from `RepoError` so callers can handle repo-level
failures without knowing git is the implementation. The hierarchy:

- `RepoError` — base class for all Repo failures. Wraps the underlying
  `GitCmdFailed` so the raw git info is preserved for debugging but is not
  part of the advertised API.
- `NoWorkTreeError(RepoError)` — raised when a work-tree-requiring method
  is called on a Repo with `work_tree=None`. Message names the method and
  the repo's `git_dir`.
- `ConflictError(RepoError)` — raised by `merge()` when the merge leaves
  unresolved conflicts. Carries `conflict_paths: list[str]` so callers
  don't need to call `conflict_paths()` separately.

This is the principal step toward hiding "cod-sync uses git." It does not
fully get us there — `gitCmd` and `GitCmdFailed` still exist internally,
and `CodSync` still exposes `gitCmd` publicly — but every new `Repo` call
site no longer sees git-specific exception types.

### What stays on CodSync

The existing sync workflows stay as-is on `CodSync`:

- `push_to_remote`, `fetch_from_remote`, `clone_from_remote`,
  `merge_from_remote`, `add_remote`

`CodSync` should eventually accept a `Repo` instead of a `repo_dir`, but that
can happen incrementally and is not required for the starter refactor.

## Starter Refactor Target

The manager's recurring "stage + check + commit" pattern. This appears 10+
times across `provisioning.py` and `manager.py`:

```python
cod.gitCmd(["add", "core.db"])
r = cod.gitCmd(["diff", "--cached", "--quiet"], raise_on_error=False)
if r.returncode != 0:
    cod.gitCmd(["commit", "-m", "..."])
```

Converting these sites proves: `Repo.stage()`, `Repo.commit()`, and
`Repo.head()` (replacing `_git_head`).

Also convert the one raw `subprocess.run(["git", ...])` helper — `_git_head`
in `manager.py` — which becomes `Repo.head()`.

### Why this target

- High repetition (10+ sites) — maximum deduplication payoff.
- Low risk — the commit pattern is purely local, no sync or conflict behavior.
- Doesn't touch vault, which has its own more complex `(git_dir, work_tree)`
  usage patterns worth handling in a separate pass.

## Steps

### 1. Add the `Repo` class to cod-sync

Create the type with the read-only introspection methods and the work-tree
methods listed above. Every method forwards to `gitCmd` internally, using
`--git-dir` and (where needed) `--work-tree`.

Put it in a new `cod_sync/repo.py` module. `cod_sync/protocol.py` stays
as-is; the two modules can grow independently.

Keep `gitCmd` as a module-level private helper in `protocol.py`. It stays
importable for now (removing the export is a follow-up), but `Repo` is the
intended public API for local repo operations.

Add micro tests for `Repo` covering at least: `init`, `has_commits`, `head`,
`stage`, `commit`, `commit` returning `None` when nothing staged, `status`,
`checkout_head`, `merge` raising `ConflictError` with paths populated, and
a work-tree method raising `NoWorkTreeError` on a CACHED Repo.

### 2. Convert manager's stage/check/commit sites

Replace the `add core.db` + `diff --cached --quiet` + `commit` pattern in
`provisioning.py` and `manager.py` with `Repo.stage()` + `Repo.commit()`.

Replace `_git_head()` in `manager.py` with `Repo.head()`.

`Repo.commit()` returns `str | None` — the commit SHA on success, `None` if
nothing was staged. This absorbs the recurring `diff --cached --quiet` check
into a one-liner while keeping the signal available to callers that need to
assert something was actually committed.

### 3. Verify the leakage count improved

Re-run the catalog searches and update the appendix. Expected: `provisioning.py`
drops from 29 to ~19 `CodSync.gitCmd` sites, `manager.py` drops from 5 to ~1.

### 4. Do NOT convert vault in this branch

Vault's gitCmd usage is architecturally different (work-tree coordination,
conflict handling, registry ops). It deserves its own branch with its own plan,
built on the `Repo` type this branch introduces.

## Design Constraints

- `Repo` is a thin wrapper, not an ORM or transaction manager. It should not
  accumulate state beyond `git_dir` and `work_tree`.
- Do not break `CodSync`'s existing public API. `Repo` is additive.
- Do not introduce a backward-compatibility shim for `gitCmd` exports — the
  repo is pre-alpha.
- Manager's CodSync instances for push/fetch stay as-is. Only the local repo
  maintenance calls migrate to `Repo`.

## Validation

### Goal: leakage reduced

- `rg -c 'CodSync\.gitCmd|CodSyncProtocol\.gitCmd' packages/small-sea-manager/`
  should show a meaningful drop (target: ~20 fewer sites across the two files).
- `rg -n 'subprocess\.run\(\["git"' packages/small-sea-manager/` should return
  zero hits (raw subprocess replaced by `Repo.head()`).

### Goal: behavior preserved

- `uv run pytest packages/cod-sync/tests -q` — new Repo tests pass.
- `uv run pytest packages/small-sea-manager/tests -q` — existing manager tests
  still pass.
- `uv run pytest packages/shared-file-vault/tests -q` — vault unaffected.

### Goal: API is usable

- At least one reviewer (human or future-branch author) can look at the
  converted manager code and understand the `Repo` API without reading the
  implementation.

---

## Appendix: Leakage Catalog (refreshed 2026-04-15, post-#87)

### Packages with no production git leakage

- `packages/small-sea-hub` — clean (only unrelated `osascript` subprocess usage)
- `packages/cuttlefish` — clean
- `packages/small-sea-client` — clean
- `packages/small-sea-note-to-self` — clean
- `packages/splice-merge` — clean
- `packages/wrasse-trust` — clean

### `packages/shared-file-vault/shared_file_vault/vault.py`

- `from cod_sync.protocol import gitCmd` — 1 import + 23 call sites
- 3 `CodSync` usages, all `repo_dir=git_dir` (fetch/push paths only — #87
  eliminated the `repo_dir=checkout` usages)

Operation families:
- repo init/config: `init --bare`, `config core.bare false`
- ref introspection: `rev-parse HEAD`, `rev-parse --verify`, `merge-base --is-ancestor`
- work-tree ops: `checkout HEAD -- .`, `checkout -B main <ref>`, `merge <ref>`
- content publication: `add`, `commit`
- user-facing introspection: `status --porcelain`, `log --oneline`, `diff --name-only --diff-filter=U`

### `packages/small-sea-manager/small_sea_manager/provisioning.py`

- 29 `CodSync.gitCmd(...)` call sites
- Dominated by the recurring `add core.db` / `diff --cached --quiet` / `commit`
  pattern (the starter refactor target)
- Also: `init -b main`, `checkout main`, `add .gitattributes`

### `packages/small-sea-manager/small_sea_manager/manager.py`

- 4 `CodSyncProtocol.gitCmd(...)` sites: `checkout main`, `add core.db`,
  `diff --cached --quiet`, conditional `commit`
- 1 raw `subprocess.run(["git", ...])` helper: `_git_head()` doing
  `rev-parse HEAD`

### Test-only usage

- cod-sync tests: `CS.gitCmd(...)` for fixture setup — expected, not targeted
- vault tests: `gitCmd` for scenario setup — expected
- manager tests: `CodSync.gitCmd(...)` for repo state construction — expected
- Test cleanup follows production API cleanup; not in scope here

### Leakage summary table

| Package | File | Mechanism | Sites | Character |
|---------|------|-----------|-------|-----------|
| shared-file-vault | vault.py | direct `gitCmd` import | 23+1 | full local git plumbing (NOT targeted this branch) |
| small-sea-manager | provisioning.py | `CodSync.gitCmd(...)` | 29 | repo init + recurring stage/commit (**starter refactor target**) |
| small-sea-manager | manager.py | `CodSyncProtocol.gitCmd(...)` + raw subprocess | 4+1 | NoteToSelf stage/commit + HEAD introspection (**starter refactor target**) |
