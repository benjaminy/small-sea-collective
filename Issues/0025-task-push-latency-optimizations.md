---
id: 0025
title: Reduce push/pull round-trip latency
type: task
priority: low
---

## Background

Each `push_niche` (and by extension any `SmallSeaRemote.upload_latest_link` call) makes
5–6 sequential Dropbox API round-trips on the critical path. At ~700 ms per call this
totals 3.5–4.5 s per push, dominating the end-to-end sync latency even when ntfy push
notifications are used for the signal delivery.

The sequential calls in order are:

1. GET `latest-link.yaml` — read the current remote head
2. POST `B-{bundle_uid}.bundle` — upload the git bundle
3. POST `L-{link_uid}.yaml` — archive the link for future chain traversal
4. POST `latest-link.yaml` — CAS-write the new head (`notify=True`)
5. GET `signals.yaml` — read for CAS in `_bump_signal`
6. PUT `signals.yaml` — write the incremented signal count

## Optimizations

### 1. Publish the push notification immediately after step 4

`_ntfy_publish_signal` is currently called after step 6 (once `signals.yaml` is written).
That means Bob's Hub fetches `signals.yaml` after the ntfy message arrives and sees the
updated count. However, the notification could fire right after step 4 and carry the new
count inline — the ntfy message body already contains `{"event": "push", "count": N}`.

If Bob's Hub trusts the count from the ntfy payload directly (updating `peer_counts`
without reading `signals.yaml`), steps 5 and 6 are off the critical path from Bob's
perspective. Alice still needs to write `signals.yaml` eventually (for peers who weren't
listening on ntfy at the time), but that write can happen in the background.

### 2. Cache `signals.yaml` to optimistically skip the CAS read (step 5)

`_bump_signal` always reads `signals.yaml` before writing (CAS loop). If the Hub caches
the last-known etag and content per session, it can attempt an optimistic write immediately
and only fall back to a read on a 409 conflict. In the common case (no concurrent writers)
this eliminates one Dropbox round-trip.

### 3. Cache `latest-link.yaml` to optimistically skip the read (step 1)

`push_to_remote` reads `latest-link.yaml` to find the current tip before building the
incremental bundle. The Hub (or `SmallSeaRemote`) could cache the last-pushed etag and
link content per session and attempt an optimistic push directly. On a CAS conflict it
falls back to re-reading. Eliminates one Dropbox round-trip in the common single-writer
case.

### 4. Upload bundle and `L-{link_uid}.yaml` in parallel (steps 2 and 3)

Steps 2 and 3 are independent — the bundle and the archived link file can be uploaded
concurrently. `L-{link_uid}.yaml` is only accessed during gap-fill chain traversal (rare),
so it is never on the critical path relative to `latest-link.yaml`. Parallelising with a
background thread in `SmallSeaRemote.upload_latest_link` saves ~700 ms.

## Expected impact

Applying all four optimizations could reduce the critical path from 6 sequential Dropbox
calls to roughly 2 (optimistic latest-link write + optimistic signals write), cutting push
latency from ~4 s to ~1.5 s or less in the common case.

## References

- `packages/cod-sync/cod_sync/protocol.py` — `SmallSeaRemote.upload_latest_link`,
  `push_to_remote`
- `packages/small-sea-hub/small_sea_hub/backend.py` — `_bump_signal`,
  `_ntfy_publish_signal`
- Issue 0018 — cloud chain compaction (related: also reduces the chain read cost)
