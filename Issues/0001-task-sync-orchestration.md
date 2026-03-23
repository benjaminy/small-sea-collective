---
id: 0001
title: Wire Cod Sync through the Hub API
type: task
priority: high
---

## Context

Cod Sync has a working sketch of `sync_to_cloud()` and `sync_from_cloud()` but they are not connected to the Hub. Currently `S3Remote` is test-only infrastructure. For the system to function end-to-end, sync must go through the Hub's cloud storage API rather than hitting S3 directly.

This is the primary blocker for a working system demo.

## Work to do

- Replace direct S3Remote usage with Hub upload/download API calls in Cod Sync
- Wire the Hub's `_register_cloud_location` / `_get_cloud_link` into the sync flow
- Determine how sync is triggered (on commit? on demand? Hub-initiated?)
- Decide where sync orchestration lives — in the Hub, in the manager, or as a separate coordinator

## References

- `packages/cod-sync/cod_sync/sync_sketch.py` — incomplete sync sketch
- `packages/small-sea-hub/small_sea_hub/backend.py:464` — `_register_cloud_location`
- `packages/small-sea-hub/small_sea_hub/backend.py:482` — `_get_cloud_link`
- `Scratch/WIP.txt` — original prioritization notes (item 1 of 3 blockers)
