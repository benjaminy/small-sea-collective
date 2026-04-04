> Migrated to GitHub issue #27.

---
id: 0023
title: Sync signal file — per-member push counter for cheap polling
type: task
priority: high
status: closed
---

## Context

After implementing push/pull plumbing (issue 0015), there is still no mechanism
for a participant to know that a teammate has new data available. Push
notifications (ntfy or similar) will be the low-latency path when configured,
but the system needs a reliable fallback that works without any external service.

The chosen approach: each participant maintains a small `signals.yaml` file in
their own cloud bucket, written only by them. The Hub manages this file entirely
— no app or manager involvement required. Apps trigger the machinery by setting
a flag on upload; everything else happens inside the Hub.

## Signal file format

Initial (simple) form — a flat map of station ID → push count:

```yaml
version: 1
{station_id_hex}: 5
{station_id_hex}: 2
```

The file lives at the well-known path `signals.yaml` in the participant's own
cloud bucket. It is written only by the participant's own Hub; teammates read
it anonymously (bucket is public-read after `ensure_bucket_public`).

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

### 1. `notify` flag on `POST /cloud_file`

Add an optional boolean field `notify` to the `CloudUploadReq` model (default
`false`). When `true` and the upload succeeds, the Hub bumps `signals.yaml` for
the session's station before returning.

```json
{
  "path": "latest-link.yaml",
  "data": "...",
  "expected_etag": "...",
  "notify": true
}
```

The cod-sync layer (`SmallSeaRemote.upload_latest_link`) sets `notify=true`
when uploading `latest-link.yaml` — it is the only caller that knows an upload
is semantically significant. Arbitrary file writes (bundle uploads, etc.) do not
set the flag. This keeps the Hub protocol-agnostic: it does not need to know
that `latest-link.yaml` is special to cod-sync.

### 2. Hub-internal signal bump

When `notify=true` on a successful upload, the Hub performs an atomic
increment of `signals.yaml`:

- Download current `signals.yaml` + etag (if absent, start from empty)
- Increment `{station_id_hex}: N`
- Re-upload with `expected_etag` (CAS)
- On 409 conflict: re-read and retry

The count only needs to be strictly greater than before; exact value does not
matter. A CAS conflict means two devices pushed simultaneously — both retries
will succeed and the count will be bumped twice. This is fine: it causes an
extra unnecessary poll by teammates, which is a minor performance issue, not
a correctness problem. If the signal bump fails entirely after retries (network
failure), teammates miss this notification but will catch up on the next push.

The station ID is already available from `ss_session.station_id` inside the Hub.
No client involvement required.

### 3. Hub-internal peer watcher

A background task in the Hub that polls teammates' `signals.yaml` files and
triggers notifications when a counter increases.

- On session creation, register the session's team peers in the watcher
- Periodically fetch each peer's `signals.yaml` via anonymous S3 read (same
  path as `download_from_peer` but for the well-known `signals.yaml` path)
- Compare against last-seen counts; on any increase, fire a notification
  (ntfy if configured, else store in an internal mailbox for the app to poll)
- Track last-seen etag per peer; skip download if etag unchanged (cheap check)

The poll interval is configurable; a sensible default is 60 seconds.

### 4. `GET /peer_signal` endpoint

A convenience endpoint that returns the parsed signal file for a given peer,
forwarding the S3 etag so clients can do conditional polls:

```
GET /peer_signal?member_id={hex}
→ {"version": 1, "stations": {"{station_id_hex}": N, ...}, "etag": "..."}
```

This lets apps (or the Manager) check peer signal state without going through
the background watcher, useful for on-demand "check now" flows.

### 5. Update issue 0015

Mark item 1 (sync triggers) as addressed once the signal file + peer watcher
are wired end-to-end. Long-poll and push notification integration remain
separate work.

## What is NOT in scope

- ntfy / push notification integration (separate task)
- Long-poll Hub endpoint (would eliminate the polling interval delay; deferred)
- Gossip / matrix signal file (v2 format; deferred)
- Per-device vs. per-participant signal semantics (treat as per-participant for now)

## References

- `packages/small-sea-hub/small_sea_hub/server.py` — `POST /cloud_file`, `GET /peer_cloud_file`
- `packages/small-sea-hub/small_sea_hub/backend.py` — `upload_to_cloud`, `download_from_peer`
- `packages/cod-sync/cod_sync/protocol.py` — `SmallSeaRemote.upload_latest_link`
- Issue 0015 — sync orchestration (parent task)
