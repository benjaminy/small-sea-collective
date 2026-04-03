# Git History Pruning for Cod Sync

Explore the feasibility of git-history pruning for Cod Sync and implement a first, branch-scoped experiment for [Issues/0019](Issues/0019-task-git-history-pruning.md).

## Branch Goal

This branch is about proving or disproving a specific technical claim:

> A git repo can be converted into a "blob-pruned" form that preserves the full commit DAG and commit SHAs, while keeping blob data only for a recent boundary-to-HEAD window that is sufficient for normal recent operations.

If that claim holds, this branch should leave us with:

- A reproducible experiment script
- Clear evidence about what works, what fails, and why
- A recommendation for the best rehydration strategy
- A sharpened list of protocol changes for later branches

This branch should **not** change the Cod Sync wire protocol or production behavior yet.

## Scope of This Branch

### In scope

- Build local-only experimental repos
- Exercise git partial-clone / promisor-object behavior against local file-backed repos
- Measure correctness and space savings
- Measure several rehydration strategies
- Document exact failure modes outside the kept window
- Produce a recommendation for whether to proceed with protocol work

### Out of scope

- Changing Cod Sync clone/fetch/push behavior
- Introducing `window-snapshot` support in the protocol
- Automatically determining a safe pruning boundary across teammates
- Wiring anything into Small Sea Manager or Hub
- Any production internet access

Keeping this branch narrow is important because the current `packages/cod-sync` implementation still assumes `initial-snapshot` semantics for compaction and clone flows.

## Definitions

- **Boundary**: A commit SHA chosen by the caller. Blob data must remain available for commits reachable from `HEAD` back through this boundary, inclusive.
- **Window**: The set of commits whose blobs are intentionally kept. In this branch, "window" means "boundary-to-HEAD closure needed for normal recent operations," not "last N commits" unless a test repo chooses that shape.
- **Blob-pruned repo**: A repo with the original commit/tree DAG intact, but with blobs outside the kept window absent after removing the promisor remote and garbage-collecting.
- **Success for this branch**: We can demonstrate the technique locally, characterize its limits, and decide whether a future Cod Sync protocol change is justified.

## Why This Matters

Cod Sync is meant to support durable, local-first team data. Unbounded git history is a real storage cost, but Small Sea also depends on robust 3-way merge and reproducible sync behavior. The value of this branch is not "make repos smaller at any cost"; it is "find out whether bounded local blob retention can preserve the operational properties Small Sea actually needs."

That means the plan must convince a skeptical reader of two things:

1. The proposed technique really can preserve the important git/Cod Sync behaviors inside a recent working window.
2. Adopting it later would not quietly undermine Small Sea's integrity, sync guarantees, or architectural rules.

## Key Assumptions to Validate

These are assumptions right now, not facts:

1. A blobless local clone from `file://` is a faithful enough stand-in for later Cod Sync local pruning work.
2. Removing the promisor remote and repacking actually leaves old blobs unavailable rather than silently retained.
3. 3-way merge works as long as all blobs needed for the merge base and both sides are still inside the kept boundary.
4. Incremental bundle creation still works when both prerequisite and tip are inside the kept boundary.
5. Current Cod Sync compatibility should be judged only for local bundle mechanics in this branch, not for remote chain compaction semantics.

If any of these fail, the branch should say so plainly and recommend stopping or narrowing the idea.

## Questions This Branch Must Answer

### Core feasibility

1. Can we create a blob-pruned repo without changing any existing commit SHA?
2. Can we reliably keep all blobs needed for a chosen boundary-to-HEAD window?
3. After pruning, is the repo materially smaller?
4. Are git operations inside the kept window still normal enough to support Cod Sync's near-term needs?

### Operational limits

5. What exact git commands fail outside the kept window, and with what errors?
6. What exact merge scenarios fail when the merge base falls outside the kept window?
7. What exact bundle scenarios fail when required historical blobs are absent?

### Decision support

8. Which rehydration strategy gives the best simplicity/performance/correctness tradeoff?
9. Is the result strong enough to justify follow-up protocol work, or is the idea too brittle?

## Deliverables

### 1. Experiment directory

Create a dedicated experiment directory at `Experiments/git_history_pruning/` containing:

- `README.md` with the experiment description, how to run it, what was tested, what passed, what failed, and the recommended next step
- One or more scripts that build the test repos, run the pruning flow, validate results, and benchmark rehydration strategies
- Any small supporting files needed to keep the experiment reproducible and understandable

The experiment implementation should:

- Build the test repos
- Run the pruning flow
- Run validations
- Benchmark rehydration strategies
- Print or write a structured summary of results

### 2. Experimental write-up

Use `Experiments/git_history_pruning/README.md` as the companion write-up summarizing:

- What was tested
- What passed
- What failed
- Recommended next step

The experiment directory should be understandable without reading the script source line-by-line.

### 3. Micro test coverage where appropriate

If any helper code is factored into reusable Python functions inside the repo, add small local micro tests for the tricky logic. The main experiment itself can remain an executable script.

## Experimental Design

## Phase 0: Make the success criteria explicit

Before coding much, define the exact checks the script will enforce:

- Commit SHA lists must match exactly between original and pruned repos
- The kept-window commits must have readable file contents
- The out-of-window commits must fail in a documented, non-corrupting way
- Bundle creation inside the kept window must succeed
- At least one merge scenario inside the kept window must succeed
- Repo size must decrease by a meaningful amount on at least one nontrivial repo

If a check is only "interesting to observe" and not "required for branch success," label it as exploratory.

## Phase 1: Build representative local repos

Construct several repos with deterministic history so results are reproducible.

### Repo A: Typical app history

- Around 50 commits on `main`
- Text files, deletes, renames, and a few binary blobs
- At least one merged feature branch
- Lightweight and annotated tags

Use this repo as the primary correctness repo.

### Repo B: Many small files

- Around 50 commits
- Each commit touches many small files

This is mostly for I/O sensitivity.

### Repo C: Few large files

- Around 50 commits
- Each commit touches a few relatively large files

This is mostly for blob-size sensitivity.

### Optional Repo D: Merge-heavy history

Only add this if Repo A does not adequately stress merge-base behavior.

The repos should be generated locally and deterministically enough that repeated runs are comparable.

## Phase 2: Establish the baseline pruning flow

For Repo A, implement the baseline flow:

1. Create a blobless clone using `git clone --filter=blob:none file:///...`
2. Rehydrate blobs for the chosen boundary-to-HEAD window
3. Remove the promisor remote
4. Run repack / prune / gc as needed
5. Validate behavior before and after cleanup

This phase should answer a critical clarification question:

> What exact sequence of git commands is sufficient to produce a stable blob-pruned repo with no hidden dependency on the original remote?

If the answer is ambiguous, the branch is not done.

## Phase 3: Validate correctness on Repo A

### Repository integrity checks

- Compare full commit SHA lists
- Compare branch heads
- Compare tags and their targets
- Confirm `git log` works across full history

### Kept-window checks

For every commit in the kept window:

- `git checkout <sha>` succeeds
- File contents are readable
- `git show <sha>:path` works for representative files
- Diff and merge operations needed for recent work behave normally

### Out-of-window checks

For representative commits outside the kept window:

- `git show`
- `git diff`
- `git log -p`
- `git checkout`

Capture the exact failure mode for each command. We want evidence of "cleanly unavailable historical blobs," not vague breakage.

### Blob absence proof

Command-level failures don't prove blobs are gone — they could be present but unused. Use `git cat-file -e <blob-sha>` (returns 0 if present, 1 if missing) on specific blob SHAs from outside the window to confirm the data is actually not on disk. This is the definitive check for Open Clarification #4.

### Bundle checks

Validate with ordinary git bundle mechanics first:

- `git bundle create out.bundle boundary..HEAD` succeeds when all required blobs are kept
- That bundle applies on a separate full clone
- A bundle created from the full repo can be applied to the pruned repo when prerequisites are satisfied

Also test a clearly out-of-window case and record the failure.

### Merge checks

Create at least these cases:

- Branch diverges within the kept window and merges successfully
- Branch diverges before the boundary and fails in a documented way

The plan should record exactly which object absence causes the merge failure.

## Phase 4: Benchmark rehydration strategies

Compare these strategies on Repos A, B, and C:

1. Checkout each commit in the kept window
2. `rev-list --objects` plus `cat-file --batch`
3. Pack-driven rehydration via `pack-objects` or equivalent explicit-object packing
4. `diff-tree`-based selective walk

For each strategy, capture:

- Wall-clock time
- Bytes written to `.git/objects/pack` if practical
- Whether the resulting repo passes the kept-window correctness checks
- Implementation complexity / brittleness notes

The branch should end with a clear recommendation, not just a table.

## Phase 5: Edge cases and anti-goals

Check the most decision-relevant edge cases:

- Pruning an already pruned repo
- Boundary equals `HEAD`
- Boundary covers all history
- Merge commits
- Binary files
- Renames
- Tags
- Symlinks, if easy to include locally

Do not let this phase sprawl. If a case is speculative and not relevant to Cod Sync's near-term needs, note it and move on.

## Validation Standard

The branch is successful only if a bright critic could inspect the outputs and agree with all of the following:

1. We know exactly what the technique preserves.
2. We know exactly what it breaks.
3. We know whether the breakage is acceptable for Cod Sync's intended usage.
4. We have enough evidence to choose either "proceed to protocol design" or "stop here."

## Cod Sync Implications

This section is intentionally about implications, not implementation commitments for this branch.

### What the current codebase supports today

Current `packages/cod-sync` code assumes:

- Clone walks back to an `initial-snapshot`
- Initial clone starts from a full snapshot bundle
- Incremental fetch logic expects ordinary reachable prerequisites
- Chain compaction is specified as "fresh initial-snapshot bundle from current state"

So this branch should not claim that Cod Sync already supports pruned-chain remotes. It does not.

### What this branch can legitimately inform

If the experiment succeeds, it can justify later work in three separate areas:

1. **Local pruning API**: A future `CodSync.prune_local(boundary)` or similar
2. **Protocol extension**: A future replacement or extension of `initial-snapshot` compaction semantics
3. **Boundary selection**: A future mechanism for choosing safe boundaries across teammates

Those should remain separate follow-up branches because they solve different problems.

## Architectural Guardrails

Any follow-up design inspired by this branch must keep these repository rules intact:

- Production network access must still go through the Hub
- Only Small Sea Manager should directly touch the core station database
- Testing should remain local-only wherever possible

For this branch specifically, all experiments should use local file-backed repos and local bundle operations only. No network behavior needs to be introduced to validate the pruning idea itself.

## Future Work, Clearly Separated

### Future branch A: Local pruning API

Add a local-only API around the experimentally validated pruning flow. This should remain usable without any remote protocol changes.

### Future branch B: Pruned-chain compaction

Decide whether Cod Sync should support a compacted chain that is not a full historical snapshot. This likely requires a format-spec update and corresponding clone/fetch changes.

### Future branch C: Safe boundary determination

Figure out how an app chooses a safe boundary in a distributed team setting. This is a separate correctness problem and should not be smuggled into the pruning mechanics branch.

### Future branch D: Product integration

Only after the above are proven should Small Sea Manager expose a user-facing compact/prune workflow.

## Concrete Implementation Steps for This Branch

1. Create `Experiments/git_history_pruning/` with `README.md` and the experiment script(s).
2. Generate deterministic local repos for the chosen scenarios.
3. Implement one baseline pruning flow and make it pass the core correctness checks.
4. Add the other rehydration strategies and benchmark them.
5. Record a concise result summary and recommendation.
6. Update this plan if the evidence changes the proposed follow-up direction.

## Exit Criteria

Do not call this branch done until all of the following are true:

- The experiment is runnable from a clean checkout with local prerequisites clearly stated
- The script reports pass/fail results, not just ad hoc console output
- The chosen recommendation is justified by measured evidence
- The plan and write-up clearly distinguish proven facts from future design ideas

## Evidence Update

This branch now has enough evidence to support a narrow recommendation:

- Proceed to a future **local pruning API** design branch
- Do **not** yet proceed to protocol changes for pruned-chain remotes

The current experimental conclusions are:

- The kept window must be defined as the full DAG closure after the chosen boundary, not merely the first-parent slice on `main`
- `checkout` is the current best rehydration baseline
- `diff-tree` is promising but still secondary
- `rev-list --objects` plus `cat-file --batch` and `pack-objects` tend to over-hydrate and are poor default pruning candidates
- A workable cleanup sequence is currently:
  1. Remove the promisor remote
  2. Run `git repack -a -d --filter=blob:none --filter-to=<temp-dir>`
  3. Run `git prune --expire=now`
- `git gc --prune=now` should not be part of the current recipe because it still triggers an unfiltered repack
- Repo-size savings are meaningful on larger binary-heavy histories, but may be negligible on small repos
- The experiment has direct proof that old blobs are truly absent, not merely unexercised

What remains intentionally out of scope for this branch:

- Cod Sync protocol changes for pruned-chain remotes
- Automatic safe-boundary determination across teammates
- Product/UI integration

## Open Clarifications

These questions should be resolved in the implementation or explicitly answered in the write-up:

1. Is "window" best defined as a single ancestry slice on `main`, or as the full DAG closure from `HEAD` back to `boundary`?
2. Which git commands are the minimum set that must keep working for Cod Sync's practical needs?
3. Does bundle creation depend only on reachable objects in the requested range, or are there surprising object requirements in edge cases?
4. Can we prove that old blobs are actually absent after cleanup, rather than merely not exercised?
5. Is there a rehydration strategy that is both fast and simple enough to maintain in production code?

If any of these remain fuzzy at the end of the branch, the write-up should say so explicitly rather than implying certainty.
