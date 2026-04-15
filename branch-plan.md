# Branch Plan: gitCmd Leakage Catalog (Issue #78, Phase 1)

**Branch:** `codex-issue-78-gitcmd-leakage-catalog`  
**Base:** `main`  
**Primary issue:** #78 "Fix gitCmd sprawl"  
**Date refreshed:** 2026-04-14  
**Related packages:** `packages/cod-sync`, `packages/shared-file-vault`,
`packages/small-sea-manager`, `packages/small-sea-hub`

## In-Progress Notes (2026-04-15) — paused pending #87

This branch is paused while issue #87 ("Vault: drop .git pointer file; pass
explicit --git-dir and --work-tree") is worked on. The existing plan text below
is background context, not the working plan.

### Where planning left off

After issues #80, #81, and #82 landed, we agreed this branch should be a
**catalog + starter refactor**, not catalog-only. The target direction for
cod-sync is a **pared-down generic DVCS API plus automation around specific
sync workflows** — callers should name operations in DVCS terms, not git-CLI
terms, and `gitCmd` becomes a private implementation detail.

Sketch of the API shape we discussed:

- A `Repo(git_dir, work_tree=None)` value type (work_tree=None = CACHED mode).
- Generic DVCS methods: `init`, `head`, `has_commits`, `stage`, `commit`,
  `status`, `log`, `resolve_ref`, `is_ancestor`, `checkout_branch`,
  `conflict_paths`.
- Existing sync workflows (`push_to_remote`, `fetch_from_remote`,
  `clone_from_remote`, `merge_from_remote`, `add_remote`) stay as-is.

Candidate starter refactor target: the manager's recurring
"add core.db + diff --cached --quiet + commit" pattern (10+ sites), plus the
one raw `subprocess.run(["git", ...])` helper `_git_head` in `manager.py`.
Converting those proves the read-only introspection primitive and the
commit-staged primitive without touching vault or the full provisioning
surface.

### Why paused on #87

The `Repo(git_dir, work_tree=None)` shape is cleanest if vault adopts
explicit `(--git-dir, --work-tree)` pairs and drops the `.git` pointer file
(#87). Doing #87 first means the cod-sync API can take that shape as a
given rather than having to accommodate the pointer-file mechanism.

### When we resume

- Refresh the catalog section below against post-#87 code.
- Draft the cod-sync DVCS API sketch concretely (method signatures).
- Pick the starter refactor site and convert it.
- Measure the change in leakage count as validation.

## Why This Branch Exists

`packages/cod-sync/cod_sync/protocol.py` defines a low-level helper,
`gitCmd(...)`, that shells out to the git CLI. That helper was meant to support
Cod Sync internals, but production code in other packages now depends on it
directly or indirectly.

That coupling matters because:

- it makes git subprocess behavior part of cod-sync's de facto public API
- it spreads git-specific operational knowledge across unrelated packages
- it makes future cleanup harder because the real dependency surface is unclear
- it blurs the boundary between "Cod Sync protocol behavior" and "generic local
  repo maintenance"

This branch is the discovery phase for issue #78. Its job is to produce a
trustworthy catalog of the leakage and a credible refactor target for the next
branch, without changing production behavior yet.

## Branch Goals

This branch should leave us with:

1. A corrected catalog of all current production git CLI leakage outside
   cod-sync internals.
2. A clear classification of what kinds of git operations are leaking.
3. A shallow abstraction sketch for Phase 2 that reduces coupling without
   smuggling broad "git wrapper" APIs across the repo.
4. Validation strong enough to convince a skeptical reviewer that the catalog is
   complete enough to guide the real refactor.

## Scope

In scope for this branch:

- catalog current production call sites that depend on `gitCmd` or direct git
  subprocess usage outside cod-sync internals
- note test-only usage where it helps define the boundary of the cleanup
- describe the kinds of operations being performed at each leakage site
- document recommended Phase 2 seams
- optionally add a short "internal helper" note near `gitCmd` in
  `cod_sync/protocol.py` if that can be done without implying the refactor is
  complete

Out of scope for this branch:

- changing production code to remove leakage
- changing test helpers just for stylistic consistency
- adding backward-compatibility shims
- redesigning Cod Sync transport behavior

## Evidence-Grounded Leakage Catalog

The following findings are based on direct inspection of the repository on
2026-04-14.

### 0. `packages/small-sea-hub`

`small-sea-hub` was included in the search scope and came back clean for this
issue.

- no production `gitCmd` usage found
- no production raw `subprocess.run(["git", ...])` usage found
- the notable subprocess usage in this package is unrelated `osascript`
  notification handling, which is out of scope for issue #78

### 0b. Other searched packages with no production git leakage

The remaining first-party packages were also searched and came back clean for
this issue:

- `packages/cuttlefish`
- `packages/small-sea-client`
- `packages/small-sea-note-to-self`
- `packages/splice-merge`
- `packages/wrasse-trust`

For these packages, I found no production `gitCmd` usage and no production raw
`subprocess.run(["git", ...])` usage.

### 1. `packages/shared-file-vault/shared_file_vault/vault.py`

`vault.py` imports `gitCmd` directly:

- `from cod_sync.protocol import gitCmd` at line 27

There are 22 `gitCmd` references in this file total, including the import.
Operationally, this file is performing its own git plumbing for both registry
repos and niche repos.

Observed operation families:

- repo existence/state checks:
  `rev-parse HEAD`, `rev-parse --verify`, `merge-base --is-ancestor`
- work tree population and reset:
  `checkout HEAD -- .`, `checkout main`, `checkout -B main <ref>`
- repo initialization/configuration:
  `git init --bare`, `git config core.bare false`
- content publication:
  `git add`, `git commit`
- introspection:
  `git status --porcelain`, `git log --oneline`, `git rev-parse HEAD`

Interpretation:

- This is not a small accidental leak.
- `shared-file-vault` currently owns substantial git workflow logic itself and
  uses cod-sync only as a subprocess convenience layer plus cloud push/pull.
- Phase 2 should assume this file needs a deliberate seam, not a simple import
  rename.

### 2. `packages/small-sea-manager/small_sea_manager/provisioning.py`

`provisioning.py` contains 29 `CodSync.gitCmd(...)` call sites.

These are spread across identity bootstrap, NoteToSelf maintenance, team
creation, invitation flows, device linking, and commit-after-db-mutation paths.

Observed operation families:

- repo initialization:
  `git init -b main`
- branch/work tree movement:
  `git checkout main`
- persistence of DB-backed mutations:
  `git add core.db`, `git add core.db .gitattributes`, `git commit -m ...`

Interpretation:

- Most of this leakage is "local repo maintenance after sqlite/file writes," not
  protocol sync behavior.
- The manager package is using cod-sync's git helper as a convenience utility
  for lifecycle management of local repos.
- That suggests a future abstraction closer to "managed local repo for Small Sea
  state" than a generic exposed `gitCmd`.

### 3. `packages/small-sea-manager/small_sea_manager/manager.py`

`manager.py` contains two distinct kinds of production git coupling:

- 4 `CodSyncProtocol.gitCmd(...)` call sites:
  `checkout main`, `add core.db`, `diff --cached --quiet`, and conditional
  `commit -m "Update NoteToSelf"`
- 1 raw `subprocess.run(["git", ...])` helper:
  `_git_head(...)`, which reads `rev-parse HEAD`

Interpretation:

- `manager.py` is also participating in repo maintenance, not just
  `provisioning.py`.
- The NoteToSelf staging/commit behavior is important because it shows the leak
  is not isolated to one bootstrap module; it is present in ongoing application
  logic too.
- The `_git_head(...)` helper shows that issue #78 is mostly about leaked
  `gitCmd`, but not exclusively; there is at least one direct raw-git
  subprocess usage in production manager code.

### 4. Direct raw git subprocess leakage

For this branch's target area, the dominant production leakage is the
cross-package dependency on `gitCmd`, not a broad field of independent raw
`subprocess.run(["git", ...])` calls.

I did find one production raw-git subprocess helper in the targeted packages:
`small_sea_manager/manager.py:_git_head(...)`, which runs
`git -C <repo> rev-parse HEAD`.

Implication:

- Issue #78 is primarily about cod-sync's helper becoming an accidental shared
  utility.
- Phase 2 should still decide whether `_git_head(...)` gets folded into the same
  cleanup or intentionally left as a separate local helper.

### 5. Test-only usage worth tracking

Test usage is not the main deliverable, but it matters as a boundary check.

Observed categories:

- cod-sync tests use `CS.gitCmd(...)` for fixture setup and protocol assertions
- top-level integration-style tests also use `CS.gitCmd(...)`
- small-sea-manager tests use `CodSync.gitCmd(...)` / `provisioning.CodSync.gitCmd(...)`
  when constructing repo state for scenarios

Interpretation:

- Test usage is widespread and expected.
- Phase 2 should avoid over-optimizing for "zero test references" if that would
  distort production design.
- The production seam should be cleaned first; test cleanup can follow only if
  it remains simple and improves readability.

## Leakage Summary Table

| Package | File | Mechanism | Production call sites | Operational character |
|---------|------|-----------|-----------------------|-----------------------|
| `shared-file-vault` | `shared_file_vault/vault.py` | direct `from cod_sync.protocol import gitCmd` | 21 calls + 1 import | full local git plumbing for registry/niche repos |
| `small-sea-manager` | `small_sea_manager/provisioning.py` | `CodSync.gitCmd(...)` | 29 | repo init, checkout, add, commit around db/file mutations |
| `small-sea-manager` | `small_sea_manager/manager.py` | 4 `CodSyncProtocol.gitCmd(...)` sites plus 1 raw `subprocess.run(["git", ...])` helper | 5 total | bootstrap checkout, NoteToSelf staging/commit logic, and HEAD introspection |

## What The Leakage Seems To Mean Architecturally

The leakage is not one thing. It clusters into two different problem shapes.

### A. Local repo lifecycle operations

Examples:

- initialize repo
- switch/create main working branch
- stage generated files
- create commits after controlled local mutations

This is the dominant pattern in `small-sea-manager` and part of
`shared-file-vault`.

This suggests a future abstraction like:

- `ManagedLocalRepo`
- `StateRepo`
- package-specific helpers built on cod-sync internals rather than a globally
  exposed subprocess wrapper

Important design constraint:

- We should not replace `gitCmd` sprawl with a giant generic "GitService"
  surface that merely moves the same coupling into a new namespace.

### B. Repo state inspection and work-tree coordination

Examples:

- read head sha
- verify refs
- inspect conflict paths
- query status/log for user-facing APIs
- perform work-tree refresh operations tied to attached git dirs

This is especially visible in `shared-file-vault`.

This suggests either:

- a vault-local abstraction that owns its registry/niche repo mechanics, or
- a very small cod-sync helper surface for the repo operations that genuinely
  belong near Cod Sync

Important design constraint:

- If an operation is specific to vault's multi-work-tree model, pushing it down
  into cod-sync may increase coupling instead of reducing it.

## Recommended Direction For Phase 2

Phase 2 should begin with a bias toward narrow abstractions and package-local
ownership.

Recommended sequence:

1. Define the minimal production call sites that must stop touching
   `cod_sync.protocol.gitCmd` directly.
2. Split the work by behavior, not by package name:
   repo init/checkout, staged persistence commits, and repo-state inspection.
3. Decide case-by-case whether a behavior belongs:
   in cod-sync, in small-sea-manager, or in shared-file-vault.
4. Refactor production call sites first.
5. Revisit tests only after the production API boundary is cleaner.

Provisional design guidance:

- `small-sea-manager` likely wants a narrow helper for "persist local repo state
  after domain mutation" rather than direct git verbs at call sites.
- `shared-file-vault` likely needs an internal repo-management layer because it
  owns specialized registry/niche behavior that is broader than Cod Sync's
  transport concerns.
- cod-sync should probably keep owning protocol-facing repo operations, but
  should stop accidentally advertising `gitCmd` as a shared cross-package tool.

## Deliverables For This Branch

Required deliverable:

- this `branch-plan.md`, corrected and strong enough to guide implementation

Optional deliverable:

- a small documentation note near `gitCmd` clarifying that it is an internal
  helper and not a supported cross-package API

No production refactor is required in this branch.

## Validation

This branch is documentation-heavy, so validation has to prove completeness and
soundness rather than runtime behavior.

### Evidence that the branch goal is accomplished

- Every production reference in the repo's first-party packages to `gitCmd` or
  direct git subprocess usage has been searched for and reviewed.
- The plan distinguishes production leakage from test-only usage.
- The catalog reflects the actual code currently on this branch.
- The plan names concrete future refactor seams rather than vague "clean this
  up later" intentions.

### Evidence that repo integrity is maintained or improved

- The plan does not propose a broad abstraction that would increase coupling.
- The proposed Phase 2 direction respects existing architecture:
  cod-sync for sync concerns, package-local ownership for package-specific repo
  behavior, no new network bypasses around the Hub.
- The plan avoids backward-compatibility baggage since the repo is pre-alpha.
- The branch adds clarity without changing behavior.

### Concrete verification steps used for this plan

- search for leakage:
  `rg -n 'subprocess\.run\(\["git"|gitCmd|CodSync\.gitCmd|CodSyncProtocol\.gitCmd' packages tests`
- count `vault.py` references:
  `rg -n "gitCmd" packages/shared-file-vault/shared_file_vault/vault.py | wc -l`
- count `provisioning.py` references:
  `rg -n "CodSync\\.gitCmd" packages/small-sea-manager/small_sea_manager/provisioning.py | wc -l`
- count `manager.py` references:
  `rg -n 'CodSyncProtocol\.gitCmd|subprocess\.run\(\["git"' packages/small-sea-manager/small_sea_manager/manager.py | wc -l`
- inspect surrounding code manually in:
  `packages/cod-sync/cod_sync/protocol.py`
  `packages/shared-file-vault/shared_file_vault/vault.py`
  `packages/small-sea-manager/small_sea_manager/provisioning.py`
  `packages/small-sea-manager/small_sea_manager/manager.py`

## Risks And Open Questions For Phase 2

- `shared-file-vault` may legitimately need repo-management concepts that do not
  belong in cod-sync.
- `small-sea-manager` may need a helper that is manager-local rather than
  cod-sync-owned, even if it still shells out to git internally.
- Some test helpers may currently encode assumptions about `gitCmd` remaining
  easy to call directly.
- If we over-centralize the replacement API, we may reduce visible duplication
  while increasing hidden coupling.

## Branch Exit Criteria

This branch is done when:

1. The catalog is factually correct for current production code.
2. The validation section gives a skeptical reviewer a concrete way to audit the
   claims.
3. The plan gives Phase 2 a cleaner starting point than "search and hope."
