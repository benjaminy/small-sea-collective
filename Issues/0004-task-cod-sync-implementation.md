---
id: 0004
title: Complete Cod Sync sketch implementation
type: task
priority: high
status: closed
---

## Context

`sync_sketch.py` was explicitly an incomplete sketch. The two core functions —
`sync_to_cloud()` and `sync_from_cloud()` — had their structure but were missing
all the real work. This needed to be completed before sync orchestration (issue
0001) could be wired up.

## Resolution

Obsoleted by the real implementation that was already in `protocol.py`:

- **sync_to_cloud** → `CodSync.push_to_remote`: bundle creation, chain head
  upload with ETag CAS (`CasConflictError`), per-link YAML files
- **sync_from_cloud** → `CodSync.fetch_from_remote` / `clone_from_remote`: chain
  walking, bundle download, unbundle via git fetch
- **Hub upload/download** → `SmallSeaRemote` (fixed to use Bearer auth in 0001)
- **TODO commit message** → not present in the real implementation
- **ETag concurrency** → `CasConflictError` raised and tested

`sync_sketch.py` was deleted when closing issue 0001. The remaining
orchestration work (triggers, app-facing push/pull API) is tracked in 0015.
