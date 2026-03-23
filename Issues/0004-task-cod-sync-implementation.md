---
id: 0004
title: Complete Cod Sync sketch implementation
type: task
priority: high
---

## Context

`sync_sketch.py` is explicitly an incomplete sketch. The two core functions — `sync_to_cloud()` and `sync_from_cloud()` — have their structure but are missing all the real work. This needs to be completed before sync orchestration (issue 0001) can be wired up.

## Work to do

### sync_to_cloud
- Bundle creation from `cached_cloud_hash..HEAD`
- Upload bundle via Hub upload API
- Upload updated chain head file with ETag if-match (for concurrency control)
- Update `cached_head_path` after successful upload

### sync_from_cloud
- Download chain head file
- Walk prerequisite links to find needed bundles
- Download bundles
- Unbundle and merge (harmonic-merge for conflicts)

### Other
- Replace placeholder commit message (line 24: `"TODO: Better commit message"`)
- Decide on ETag concurrency strategy for simultaneous syncs

## References

- `packages/cod-sync/cod_sync/sync_sketch.py` — the incomplete sketch
- `packages/cod-sync/` — rest of the cod-sync package for context
