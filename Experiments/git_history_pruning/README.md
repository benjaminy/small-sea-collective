# Git History Pruning Experiment

This directory contains the first implementation of the git-history-pruning experiment described in [branch-plan.md](../../branch-plan.md).

## Purpose

The experiment is trying to answer a narrow question:

> Can we preserve the full commit DAG and commit SHAs of a git repo while retaining blob data only for a recent boundary-to-HEAD window that still supports recent operations?

This is intentionally a local-only experiment. It does not change Cod Sync's protocol or production behavior.

## Contents

- `run_experiment.py`: builds deterministic local repos, creates blobless clones, rehydrates a kept window, severs the promisor remote, and records what still works afterward

## How To Run

From the repository root:

```bash
python3 Experiments/git_history_pruning/run_experiment.py --keep-workspace
```

Useful options:

```bash
python3 Experiments/git_history_pruning/run_experiment.py \
  --workspace /tmp/git-history-pruning \
  --keep-commits 10 \
  --commit-count 28 \
  --repos repo_a_typical,repo_b_small_files,repo_c_large_files \
  --json-out /tmp/git-history-pruning/results.json
```

## Important Local-Only Detail

Plain local `file://` clones do not always honor `--filter=blob:none` by default. The experiment works around that by configuring each generated source repo with:

- `uploadpack.allowFilter=true`
- `uploadpack.allowAnySHA1InWant=true`

That makes the local partial-clone behavior match the scenario we actually want to test.

## What The Script Currently Covers

- Deterministic fixture repos:
  - Typical app history with tags, renames, binaries, a recent merge, and an old diverged branch
  - Many-small-files history
  - Few-large-files history
- Rehydration strategies:
  - `checkout`
  - `rev-list-cat-file`
  - `pack-objects`
  - `diff-tree`
- Validation on the "typical app" repo:
  - Commit hash preservation
  - Branch/tag preservation
  - Kept-window content access
  - Out-of-window blob access failure
  - Bundle creation/application inside the kept window
  - Merge behavior inside and outside the kept window

## Result

This experiment supports a narrow recommendation:

- Proceed to a future local-only pruning API design branch
- Do not yet proceed to Cod Sync protocol changes for pruned-chain remotes

The strongest current recipe is:

1. Create a blobless clone from a local `file://` source whose upload-pack filter support is enabled
2. Rehydrate the kept window using repeated `checkout`
3. Remove the promisor remote
4. Run:

```bash
git repack -a -d --filter=blob:none --filter-to=<temp-dir>
git prune --expire=now
```

Do not include `git gc --prune=now` in the current recipe. In these runs it still triggered an unfiltered repack and failed on missing historical blobs.

## Evidence Summary

- Commit hashes, branches, and tags were preserved on the validated Repo A flow.
- Kept-window file access, within-window merge, and within-window bundle creation all worked.
- Old blob access failed outside the retained window, and blob absence was directly proven with `git cat-file -e` on a known missing blob SHA.
- The kept window must be defined as the full DAG closure after the chosen boundary, not merely the first-parent slice on `main`.
- The filtered repack plus prune cleanup path preserved validated behavior and produced meaningful savings on the large-file fixture.

## Strategy Comparison

### Recommended baseline

- `checkout`
  - Fastest or near-fastest in the targeted runs
  - Preserved partiality instead of over-hydrating the repo
  - Simplest behavior to reason about so far

### Worth further refinement

- `diff-tree`
  - Often competitive with `checkout`
  - Preserved many missing objects
  - More selective in principle, but still somewhat more complex and brittle

### Poor default pruning candidates

- `rev-list-cat-file`
  - Usually rehydrated almost everything
  - Slow on the many-small-files fixture
- `pack-objects`
  - Also tended to over-hydrate
  - Inflated pack size more than the other approaches

## Targeted Benchmarks

- Repo A typical:
  - `checkout` about `0.5s`
  - `diff-tree` about `0.7s`
  - `rev-list-cat-file` about `2.0s`
  - `pack-objects` about `2.0s`
- Repo B many small files:
  - `checkout` about `0.34s`
  - `diff-tree` about `3.5s`
  - `rev-list-cat-file` about `19.0s`
  - `pack-objects` about `18.8s`
- Repo C few large files:
  - `checkout` about `0.36s`
  - `diff-tree` about `0.5s`
  - `rev-list-cat-file` about `1.8s`
  - `pack-objects` about `2.0s`

## Size Snapshot

- Repo A typical:
  - Essentially neutral in this small fixture
- Repo C large files:
  - About `1709 KiB -> 490 KiB`
  - About `1219 KiB` saved

## Edge Cases

- Boundary equals `HEAD`:
  - Worked
  - Tip content remained readable
  - Old missing objects remained absent
- Boundary covers all history:
  - Worked
  - Missing-object count dropped to zero as expected
- Prune twice:
  - Current filtered-repack cleanup path was stable in the targeted run

## What Passed

- Local blobless clone using `file://` plus source-repo `uploadpack.allowFilter=true`
- Checkout-based rehydration for the kept window
- HEAD-history commit-hash preservation on the validated fixture
- Branch preservation on the validated fixture
- Tag preservation on the validated fixture
- Kept-window file access
- Clean out-of-window blob failures
- Direct blob-absence proof on a known missing blob SHA
- Bundle creation from the pruned repo for a within-window range
- Merge inside the kept window
- Fetching a full-repo-created window bundle into the pruned repo
- Filtered repack plus prune as a workable cleanup path
- Meaningful size savings on the large-file fixture
- Edge-case checks for boundary=`HEAD`, full-history boundary, and prune-twice stability

## What Failed

- Strong size reduction in the tiny Repo A fixture
- Naive repack / gc cleanup after removing the promisor remote
- Using `git gc --prune=now` after filtered repack, because `gc` still tries to run an unfiltered repack
- `rev-list-cat-file` as a pruning strategy, because it tends to rehydrate the entire repo
- `pack-objects` as a pruning strategy, because it tends to rehydrate the entire repo and inflates pack size heavily

## Clarifications Answered

- **What is the right window definition?**
  Use the full DAG closure of commits that must remain available after the chosen boundary, not just the first-parent ancestry slice of `main`.
- **Which git behaviors matter most for Cod Sync?**
  The experiment currently treats these as the practical minimum:
  - kept-window file access
  - within-window bundle creation
  - within-window merge
  - clean failure for old blob access outside the retained window
- **Does bundle creation only depend on the requested range?**
  Not safely. The first implementation failed until merged side-branch commits inside the retained window were included in the kept set.
- **Can we prove old blobs are really absent?**
  Yes, the experiment now checks missing objects directly with `git rev-list --objects --missing=print --all` and uses that to drive old-blob failure probes.
- **Is there a rehydration strategy that is both fast and maintainable?**
  `checkout` is the current best answer. It is the fastest or near-fastest in the targeted runs and preserves partiality better than the object-enumeration approaches.

## Recommendation

- The experiment is now strong enough to justify a cautious "proceed to local pruning API design" recommendation, but not yet a protocol-change recommendation.
- Treat `filtered repack + prune` as the current best cleanup candidate.
- Do not treat `git gc` as part of the current pruning recipe.
- Treat `checkout` as the current correctness baseline and likely frontrunner.
- Treat `diff-tree` as the next-most-interesting strategy to refine, since it appears to preserve partiality better than the object-enumeration approaches.
- Treat `rev-list-cat-file` and `pack-objects` as poor default candidates for pruning, even though they technically succeed at materialization.
- Keep future work split cleanly:
  - local pruning API first
  - protocol changes later, only if needed
  - safe distributed boundary selection as a separate problem
