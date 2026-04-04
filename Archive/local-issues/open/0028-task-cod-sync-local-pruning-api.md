> Migrated to GitHub issue #11.

---
id: 0028
title: Cod Sync local pruning API
type: task
priority: medium
---

## Context

The git-history-pruning experiment in [Experiments/git_history_pruning/README.md](../Experiments/git_history_pruning/README.md) now supports a cautious recommendation to proceed with a **local-only pruning API**.

The experiment established these points:

- A repo can preserve commit hashes, branches, and tags while dropping old blob data outside a retained boundary-to-HEAD window
- The retained window must be the full DAG closure after the chosen boundary, not just the first-parent slice of `main`
- Within-window bundle creation and within-window merge can work after pruning
- The current best baseline is checkout-based rehydration
- The current best cleanup sequence is:
  1. remove the promisor remote
  2. `git repack -a -d --filter=blob:none --filter-to=<temp-dir>`
  3. `git prune --expire=now`
- `git gc --prune=now` should not currently be part of the recipe

This issue is deliberately local-only. It should not introduce protocol changes for pruned-chain remotes.

## Goal

Add a local pruning API to `cod-sync` that can take an explicit boundary commit SHA and convert an existing local repo into the experimentally validated blob-pruned form.

The caller supplies the boundary. This issue does **not** solve automatic safe-boundary selection across teammates.

## Work To Do

1. Add a local pruning entry point to `packages/cod-sync/` such as `CodSync.prune_local(boundary)` or an equivalent helper API.
2. Encode the experimentally validated window definition:
   - keep the full DAG closure after the boundary
   - preserve refs needed for current local behavior
3. Use the current recommended rehydration baseline:
   - checkout-based rehydration first
4. Use the current recommended cleanup sequence:
   - remove promisor remote
   - filtered repack
   - prune
   - do not run `git gc`
5. Add micro tests around the boundary/window computation and cleanup helpers.
6. Add integration-style local tests that validate:
   - commit hashes preserved
   - kept-window access works
   - old blob absence is directly provable
   - within-window bundle creation works
   - within-window merge works

## Non-Goals

- No Cod Sync wire-format changes
- No `window-snapshot` protocol support
- No teammate boundary negotiation
- No Small Sea Manager UI work

## References

- [Issues/0019-task-git-history-pruning.md](./0019-task-git-history-pruning.md)
- [Experiments/git_history_pruning/README.md](../Experiments/git_history_pruning/README.md)
- [Experiments/git_history_pruning/run_experiment.py](../Experiments/git_history_pruning/run_experiment.py)
- [branch-plan.md](../branch-plan.md)
