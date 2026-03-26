---
id: 0019
title: Git history pruning (squash old commits to bound repo size)
type: task
priority: low
---

## Background

Each niche is backed by a git repository. Git keeps the full commit graph
forever by default, which is required for correct 3-way merges. But most
applications don't need arbitrarily deep history — they care about the current
state and maybe a short recent window.

Over time the local git repo grows. For a niche used as a live sync mechanism
(rather than an archival source), this is pure overhead.

## Goal

Periodically squash old history to a single synthetic "epoch" commit so the
repo size stays bounded, while keeping enough history for ongoing merges to
work correctly.

## Why this is harder than it looks

Cod Sync's incremental bundles use commit SHAs as prerequisites:

```yaml
bundles:
  - [B-abc123, [main, <prev-tip-sha>]]
```

If local history is rewritten (new SHAs), any bundle produced before the
rewrite will have prerequisites that no longer exist in the rewritten repo.
A client that has the old SHAs won't be able to apply new bundles without
first fetching the full squashed base.

This means **history pruning and cloud chain compaction (issue 0018) must be
coordinated**: prune locally → push a new initial-snapshot bundle to the cloud
at the same time → compact the cloud chain. They are logically one operation.

## Candidate approaches

**Shallow clone / `git fetch --depth=N`**
Makes new clones shallow but does not rewrite existing repos. Incremental
bundles from a shallow repo exclude the truncated history, which breaks
prereq resolution for deep clients. Not straightforwardly usable here.

**`git replace` + grafts**
Creates a synthetic root commit that replaces the real initial commit in the
DAG without rewriting SHAs. Keeps the existing tip SHA valid. However,
replace refs are local and not carried by default in bundles — needs
`--include-tag`-style handling to propagate.

**`git filter-repo` / orphan branch squash**
Rewrites the entire branch to a single root commit containing the current
tree. Clean and simple, but produces entirely new SHAs — all existing
incremental bundles become orphaned. Requires a simultaneous cloud compaction.

**Recommended approach: orphan squash + coordinated compaction**

1. Create a new orphan commit with the current HEAD tree (no parent).
2. Reset `main` to the orphan commit.
3. Immediately push a new initial-snapshot bundle to cloud (no prerequisites).
4. Perform cloud chain compaction (issue 0018) atomically with this push.
5. All subsequent incremental bundles chain from the new root.

Clients that have the old HEAD and are up to date can continue syncing — their
next pull will see the new initial-snapshot bundle, detect that its prereq
(`initial-snapshot`) is satisfied, and fetch+merge normally. The merge will be
a fast-forward if they were at the same tree.

Clients that are behind by more than one bundle will need to re-clone from the
new root. This is acceptable if the squash cadence is long enough (weeks or
months).

## Open questions

- What is the right squash cadence? Probably manual / operator-initiated to
  start, same as compaction.
- Should the epoch commit include any metadata (timestamp, previous HEAD SHA
  as a note) for auditability?
- How does this interact with blame / log that apps might want to surface?
  Probably apps should not expose raw git history to users anyway.

## References

- Issue 0018 — cloud chain compaction (must be done in lockstep with pruning)
- `packages/cod-sync/cod_sync/protocol.py` — bundle prereq logic in
  `fetch_chain` and `build_link_blob`
- `packages/shared-file-vault/shared_file_vault/vault.py` — `push_niche`,
  `pull_niche`
