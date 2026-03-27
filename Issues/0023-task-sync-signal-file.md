---
id: 0023
title: Sync signal file — per-member push counter for cheap polling
type: task
priority: high
---

## Context

After implementing push/pull plumbing (issue 0015), there is still no mechanism
for a participant to know that a teammate has new data available. Push
notifications (ntfy or similar) will be the low-latency path when configured,
but the system needs a reliable fallback that works without any external service.

The chosen approach: each participant maintains a small `signals.yaml` file in
their own cloud bucket, written only by them. After pushing data to any app
station, they increment that station's counter in their signal file and upload
it. Teammates poll this file cheaply — it is tiny and can be checked with a
HEAD request (etag comparison) before downloading.

## Signal file format

Initial (simple) form — a flat map of station ID → push count:

```yaml
version: 1
{station_id_hex}: 5
{station_id_hex}: 2
```

This is the form to implement now. The file lives at a well-known path in the
participant's own cloud bucket: `signals.yaml`. It is uploaded via the Hub's
`POST /cloud_file` endpoint (same as any other cloud file), so existing auth
and transport apply.

## Future: matrix / gossip form

A later version can extend the format to a matrix where each participant records
not just their own counts but their last-known counts for all teammates:

```yaml
version: 2
{station_id_hex}:
  {alice_member_id_hex}: 5   # Alice's own push count
  {bob_member_id_hex}: 3     # Alice's last-seen count for Bob
  {eve_member_id_hex}: 7     # Alice's last-seen count for Eve
```

This allows Bob to learn about Eve's pushes by reading only Alice's signal file,
reducing polling fan-out from O(members) to O(1) in the best case. The downside
is that Alice's view of others becomes stale when the network is unreliable —
exactly the scenario where the fallback matters most. This tradeoff means the
gossip form is an enhancement, not a replacement for direct polling.

The version field allows readers to handle both formats without a breaking
change. Implement v1 now; v2 is deferred.

## What to build

### 1. Signal file writer (push side)

After `TeamManager.push(repo_dir)` succeeds, atomically increment the signal
count for the pushed station and re-upload `signals.yaml`.

- Read current `signals.yaml` (if absent, start from empty)
- Increment `{station_id}: N` for the station that was pushed
- Upload via `POST /cloud_file` with `expected_etag` (CAS) to handle concurrent
  pushes from multiple devices
- On CAS conflict: re-read and retry (the count just needs to be strictly
  greater than before; exact value does not matter)

The station ID comes from the Hub session (`ss_session.station_id`). The Hub
already knows this — it can be returned from `POST /cloud/setup` or a new
`GET /session/info` endpoint, so the client does not need to read the DB itself.

### 2. Signal file reader (poll side)

A new Hub endpoint (or an extension of `GET /peer_cloud_file`) that returns
the parsed signal file for a given peer:

```
GET /peer_signal?member_id={hex}
→ {"version": 1, "stations": {"{station_id}": N, ...}, "etag": "..."}
```

The etag should be forwarded from S3 so callers can do conditional polls (pass
`If-None-Match: {etag}` and get a 304 if nothing changed).

### 3. Polling loop skeleton

A simple polling helper in `small-sea-client` or `small-sea-manager` that:

- Accepts a list of peer member IDs and a callback
- Polls each peer's signal file on a configurable interval
- Calls the callback when any station counter increases
- Tracks last-seen etag per peer to minimize download traffic

This does not need to be production-quality; a simple `threading.Thread` loop
is fine for now. Apps can subscribe to a peer's changes without knowing about
`signals.yaml` directly.

### 4. Update issue 0015

Mark item 1 (sync triggers) as addressed once the signal file + polling loop
are wired end-to-end. Long-poll and push notification integration remain
separate work.

## What is NOT in scope

- ntfy / push notification integration (separate task)
- Long-poll Hub endpoint (would eliminate the polling interval delay; deferred)
- Gossip / matrix signal file (v2 format; deferred)
- Per-device vs. per-participant signal semantics (treat as per-participant for now)

## References

- `packages/small-sea-manager/small_sea_manager/manager.py` — `TeamManager.push()`
- `packages/small-sea-hub/small_sea_hub/server.py` — `POST /cloud_file`, `GET /peer_cloud_file`
- `packages/small-sea-client/small_sea_client/client.py` — `SmallSeaSession`
- Issue 0015 — sync orchestration (parent task)
