---
id: 0012
title: Reconsider top-level package and directory structure
type: idea
priority: low
---

## Context

From early organizational feedback: the top-level directory layout mixes concerns in ways that may get confusing as the project grows. Some things to consider:

## Ideas

- `Scratch/` and `Experiments/` serve different purposes but sit at the same level — worth distinguishing or consolidating
- `Documentation/` vs per-package READMEs vs `spec.md` files: three places for docs with no clear rule about what goes where
- `packages/` is flat — as it grows, grouping (e.g., `packages/core/`, `packages/apps/`) might help
- `tests/` at the top level alongside per-package tests: clarify the distinction (integration vs unit?)

## References

- `Scratch/suggestions.md` — original source of this feedback
