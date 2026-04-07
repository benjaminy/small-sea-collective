# Exact-Snapshot Tag-Aware Git History Pruning Experiment

This directory contains the current local-only git-history-pruning experiment
described by the branch plan for `experiment-git-history-tags`.

## Purpose

This round answers a narrower question than the first pruning experiment:

> If an app can mark exact historical states for future reference, and those
> app-level tags map down to git/Cod Sync tags, how quickly do those retained
> snapshots erode the storage savings of history pruning?

The branch deliberately treats retained tags as **exact snapshot retention
only**. A retained tag means:

- the tag ref survives
- `git checkout <tag>` still works
- representative `git show <tag>:path` still works

It does **not** mean:

- full tagged-to-`HEAD` history usability
- `git bundle create <tag>..main`
- old merges whose needed bases fall outside the kept window

This is intentionally a local-only experiment. It does not change Cod Sync's
protocol or production behavior.

## Contents

- `run_experiment.py`
  Builds deterministic fixture repos, creates blobless clones, rehydrates a
  fixed recent window plus retained exact snapshots, prunes, and records the
  resulting storage/behavior metrics across a scenario grid.

## How To Run

From the repository root:

```bash
python3 Experiments/git_history_pruning/run_experiment.py \
  --workspace /tmp/git-history-pruning-tags \
  --json-out /tmp/git-history-pruning-tags/results.json
```

Useful options:

```bash
python3 Experiments/git_history_pruning/run_experiment.py \
  --workspace /tmp/git-history-pruning-tags \
  --keep-commits 20 \
  --commit-count 96 \
  --repos repo_a_typical,repo_b_small_files,repo_c_large_files \
  --json-out /tmp/git-history-pruning-tags/results.json
```

The current branch findings below come from a run that also computed a
compressed exact-snapshot corpus for each scenario:

- workspace: `/tmp/git-history-pruning-tags-corpus`
- json: `/tmp/git-history-pruning-tags-corpus/results.json`

## Important Local-Only Detail

Plain local `file://` clones do not always honor `--filter=blob:none` by
default. The experiment configures each generated source repo with:

- `uploadpack.allowFilter=true`
- `uploadpack.allowAnySHA1InWant=true`

That makes the local partial-clone behavior match the scenario we actually
want to test.

## What The Script Currently Covers

- Deterministic fixture repos:
  - `repo_a_typical`
    Typical app history with renames, binaries, a merge, and an old diverged
    branch
  - `repo_b_small_files`
    Many-small-files history
  - `repo_c_large_files`
    Few-large-files history
- A fixed baseline kept window:
  - the most recent 20 first-parent commits on `main`
- Retained-tag candidate universe:
  - first-parent mainline commits on `main` only
- Tag density levels:
  - `0%`
  - about `10%`
  - about `25%`
  - about `50%`
  - `100%` of first-parent mainline commits
- Tag placement scenarios:
  - `recent-biased`
  - `evenly-spaced`
  - `old-biased`
  - `binary-heavy-milestones`
- Validation in every scenario:
  - commit hash preservation
  - branch/tag preservation
  - kept-window access
  - retained-snapshot access
  - clean old-blob failures for unretained history
- Storage metrics:
  - `.git` size before/after pruning
  - savings vs source repo
  - savings retained vs the no-tag baseline
  - protected blob counts / inflated blob bytes
  - overlap with the baseline kept window
  - compressed exact-snapshot corpus size
  - `pruned_git_kib / compressed_snapshot_corpus_kib`

## Current Pruning Recipe

The current best local pruning recipe remains:

1. Create a blobless clone from a local `file://` source whose upload-pack
   filter support is enabled
2. Rehydrate the kept window and retained exact snapshots using repeated
   `checkout`
3. Remove the promisor remote
4. Run:

```bash
git repack -a -d --filter=blob:none --filter-to=<temp-dir>
git prune --expire=now
```

Do not include `git gc --prune=now` in the current recipe. In these runs it
would obscure which cleanup step actually freed space, and in the earlier
experiment it also triggered unfiltered repack behavior.

## Result

This experiment supports a narrow recommendation:

- proceed with a future local-only pruning API that can retain named **exact
  snapshots**
- do not yet proceed to Cod Sync protocol changes for long-lived retained tags

## One-Sentence Summary

Adding exact snapshot tags degrades pruning savings as expected, and the
degradation can be quite fast once tags spread across older or low-overlap
history; but whether that cost is mostly "inevitable retained payload" or
"representation overhead" depends strongly on the fixture shape.

## Evidence Summary

- Retained exact snapshots worked in every reported scenario:
  - `git checkout <tag>` succeeded
  - representative `git show <tag>:path` checks succeeded
- Unretained historical blob access still failed cleanly in every reported
  scenario.
- Recent-biased tags were often cheap mainly because many landed inside or
  near the already-kept 20-commit window.
- Evenly spaced and old-biased tags were much more expensive than recent-biased
  tags on churny histories, especially the large-file fixture.
- Raw tag count was not the best predictor of cost; overlap with the kept
  window and unique protected blob payload mattered much more.
- The compressed exact-snapshot corpus comparison sharpened the interpretation:
  - large-file history often looks like an unavoidable payload cost
  - many-small-files history looks much more dominated by git/object overhead

## Size Snapshot

### Repo A: Typical app history

- baseline pruning:
  - `257 KiB`
  - `19 KiB` saved
- `10% recent-biased`:
  - `261 KiB`
  - `16 KiB` saved
- `25% evenly-spaced`:
  - `303 KiB`
  - `25 KiB` worse than source
- `100% mainline tagged`:
  - `347 KiB`
  - `61 KiB` worse than source

Interpretation:

- This fixture is small enough that pack/repo overhead dominates.
- It is directionally useful, but not a strong fixture for absolute size
  conclusions.

### Repo B: Many small files

- baseline pruning:
  - `212 KiB`
  - `235 KiB` saved
- `10% recent-biased`:
  - `243 KiB`
  - `206 KiB` saved
- `25% evenly-spaced`:
  - `366 KiB`
  - `84 KiB` saved
- `50% evenly-spaced`:
  - `404 KiB`
  - `49 KiB` saved
- `100% mainline tagged`:
  - `423 KiB`
  - `35 KiB` saved

Interpretation:

- Savings degrade steadily but do not disappear immediately.
- Exact snapshot retention still leaves room for pruning benefit here, even at
  high tag densities.

### Repo C: Few large files

- baseline pruning:
  - `2434 KiB`
  - `7628 KiB` saved
- `10% recent-biased`:
  - `3448 KiB`
  - `6615 KiB` saved
- `25% evenly-spaced`:
  - `9894 KiB`
  - `170 KiB` saved
- `50% evenly-spaced`:
  - `10106 KiB`
  - `39 KiB` worse than source
- `100% mainline tagged`:
  - `10128 KiB`
  - `56 KiB` worse than source

Interpretation:

- This is the sharpest "degrades quickly" case.
- Sparse old snapshots in a large-blob history can wipe out most of the
  pruning benefit well before 100% tag density.

## Compressed Snapshot Corpus Comparison

To separate "inevitable cost of keeping multiple exact snapshots" from possible
representation inefficiency, the experiment also compares:

- pruned git repo size
- compressed archive of the exact snapshots we promise to preserve

Metric:

- `pruned_to_compressed_snapshot_ratio`
  = `pruned_git_kib / compressed_snapshot_corpus_kib`

### Repo A

- ratio range:
  - about `0.359` to `0.474`

Interpretation:

- Fairly stable.
- Mostly consistent with "we are just paying to preserve more snapshots,"
  with some noise from repo/pack overhead.

### Repo B

- ratio range:
  - about `8.294` to `21.2`

Interpretation:

- Very unstable, and always high.
- This strongly suggests representation/object overhead dominates in the
  many-small-files fixture.
- That makes Repo B the clearest sign that there may be an interesting
  efficiency opportunity later.

### Repo C

- ratio range:
  - about `0.334` to `0.671`

Interpretation:

- The ratio moves, but in a meaningful way rather than random noise.
- Old or evenly spaced retained snapshots are expensive because the repo must
  preserve lots of real large-blob payload.
- At `100%`, the ratio drops again, suggesting git's packed representation is
  actually quite efficient once we are effectively keeping everything.

## What Passed

- Local blobless clone using `file://` plus source-repo
  `uploadpack.allowFilter=true`
- Checkout-based rehydration for:
  - the fixed recent kept window
  - retained exact snapshots
- Commit-hash preservation
- Branch preservation
- Tag preservation
- Kept-window file access
- Retained-snapshot file access
- Clean out-of-window blob failures for unretained history
- Direct blob-absence proof on a known missing blob SHA
- Filtered repack plus prune as a workable cleanup path
- Cross-scenario size and overlap comparisons
- Compressed exact-snapshot corpus comparison

## What Failed Or Remains Weak

- Strong size reduction in the tiny Repo A fixture
- Any claim that raw tag count alone predicts storage cost well
- Any claim that exact snapshot retention should preserve full old
  tagged-to-`HEAD` chain behavior
- Any conclusion yet about distributed retained-tag semantics in the protocol

## Clarifications Answered

- **What is the candidate tag universe in this branch?**
  First-parent mainline commits on `main` only.
- **What does `100%` mean?**
  Every first-parent mainline commit on `main` is tagged, not every commit in
  the full DAG.
- **Can recent-biased tags land inside the already-kept window?**
  Yes. The experiment records both total retained tags and retained tags
  outside the baseline window so those "already free" tags are visible.
- **Do tags necessarily degrade pruning savings?**
  Yes. Retaining more exact snapshots means preserving more data.
- **How quickly does the degradation happen?**
  It depends strongly on overlap and blob shape:
  - gradual on the many-small-files fixture
  - very fast on the few-large-files fixture once tags spread into older
    history
- **Is the observed cost just inevitable payload, or is there efficiency
  headroom?**
  The compressed snapshot corpus comparison suggests:
  - Repo C is often mostly inevitable payload cost
  - Repo B likely has meaningful representation overhead

## Recommendation

- Proceed to a future local pruning API that can:
  - prune with an explicit recent boundary
  - optionally retain named refs as exact snapshots
  - warn when requested retained snapshots are old, low-overlap, or
    blob-heavy
- Do not yet treat exact snapshot tags as a protocol feature.
- Treat the current results as support for two different future questions:
  - local pruning API design for exact snapshot retention
  - possible later efficiency work for small-file-heavy histories

## Likely Follow-On

- Encode exact-snapshot retention into a future `cod-sync` local pruning API
- Surface cost-model hints or warnings to callers
- Decide later whether small-file-heavy histories justify a separate
  representation-efficiency experiment
