---
id: 0001
title: Wire Cod Sync through the Hub API
type: task
priority: high
status: closed
---

## Context

Cod Sync has a working sketch of `sync_to_cloud()` and `sync_from_cloud()` but they are not connected to the Hub. Currently `S3Remote` is test-only infrastructure. For the system to function end-to-end, sync must go through the Hub's cloud storage API rather than hitting S3 directly.

This is the primary blocker for a working system demo.

## Resolution

`SmallSeaRemote` in `protocol.py` is the Hub-backed remote. It talks to the Hub's
`/cloud_file` endpoints using Bearer auth and handles bundle upload/download,
CAS on the chain head, and link traversal for clone. `CodSync.push_to_remote`,
`fetch_from_remote`, and `clone_from_remote` all work with it. The old
`sync_sketch.py` was deleted (it predated the real implementation).

The remaining orchestration work — sync triggers, app-facing push/pull API,
conflict notification, `TeamManager.connect()` — is tracked in issue 0015.

## Commits

- Fix `SmallSeaRemote` Bearer auth and `download_bundle` tuple bug
- Update `test_smallsea_remote.py` to current Hub session API
- Delete `sync_sketch.py`

## Original work items

- ✅ Replace direct S3Remote usage with Hub upload/download API calls in Cod Sync
- ✅ Wire Hub cloud storage into the sync flow (`SmallSeaRemote`)
- ➡️ Determine how sync is triggered — see issue 0015
- ➡️ Decide where sync orchestration lives — see issue 0015

## References

- `packages/cod-sync/cod_sync/protocol.py` — `SmallSeaRemote`
- `packages/small-sea-hub/small_sea_hub/backend.py` — `upload_to_cloud`, `download_from_cloud`
