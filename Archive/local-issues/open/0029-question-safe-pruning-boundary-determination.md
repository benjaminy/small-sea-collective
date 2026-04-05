> Migrated to GitHub issue #12.

---
id: 0029
title: Safe pruning boundary determination
type: question
priority: medium
---

## Context

The git-history-pruning experiment now suggests that a local-only pruning API is plausible, but it intentionally leaves one major correctness problem unsolved:

> How does a caller choose a pruning boundary that is actually safe for a distributed team?

The experiment assumes an explicit boundary commit SHA supplied by the caller. That is enough for local pruning mechanics, but not enough for a product-quality workflow in Small Sea.

If the chosen boundary is too aggressive, a dormant teammate may later need an older merge base than the pruned repo or compacted chain still retains.

This is a distributed-state and product-safety problem, not just a git-mechanics problem.

## Question

What is the right way for Small Sea to determine or approximate a safe pruning boundary across teammates and devices?

## Things To Resolve

1. What exact safety property do we want?
   - oldest ancestor any active teammate may still need?
   - oldest ancestor any reachable device may still need?
2. What data already exists that could inform the boundary?
   - peer chain heads
   - sync metadata
   - hub-visible berth state
3. Which approach is the right first implementation?
   - manual boundary supplied by user/admin
   - conservative fixed window
   - peer chain inspection
   - explicit announced "oldest needed ancestor" metadata
4. What should happen if some peer/device is unreachable?
5. How should the Hub participate, given the architectural rule that Small Sea internet access goes through the Hub?
6. Is this purely local policy, or does it eventually require protocol metadata?

## Suggested Direction

The current evidence suggests treating this as a separate design problem after the local pruning API exists.

A reasonable staged approach may be:

1. Start with explicit/manual boundary selection
2. Add a conservative automated approximation later
3. Only then consider protocol-level announced ancestor metadata if needed

## References

- [Issues/0019-task-git-history-pruning.md](./0019-task-git-history-pruning.md)
- [Issues/0028-task-cod-sync-local-pruning-api.md](./0028-task-cod-sync-local-pruning-api.md)
- [Experiments/git_history_pruning/README.md](../Experiments/git_history_pruning/README.md)
- [branch-plan.md](../branch-plan.md)
