---
id: 0015
title: Sync orchestration — triggers, app API, conflict handling
type: task
priority: high
---

## Context

The low-level plumbing for Cod Sync through the Hub is now complete:
`SmallSeaRemote` talks to `/cloud_file` with Bearer auth, `CodSync.push_to_remote`
/ `fetch_from_remote` / `clone_from_remote` work end-to-end in tests. What's
missing is the orchestration layer that ties all of this together into something
an app developer can actually use.

## What has been done

### ✅ 1. Sync triggers

The signal file + peer watcher (issue 0023) together provide the incoming
trigger path. The Hub background task (`_peer_watcher_loop`) polls each
teammate's `signals.yaml` every 60 seconds; on a count increase it updates
`peer_counts` and pulses the station's `asyncio.Event`. The `POST
/notifications/watch` long-poll endpoint allows apps to block until a
teammate's count exceeds a known value, receiving updated counts immediately
or after the next watcher round.

Outgoing trigger: `SmallSeaRemote` sets `notify=true` when uploading
`latest-link.yaml`, causing the Hub to atomically bump `signals.yaml` and
pulse the local station event so same-station sessions are notified without
waiting for the next watcher round.

### ✅ 2. App-facing push/pull API

`TeamManager` (in `small-sea-manager`) now exposes `push(repo_dir)` and
`pull(repo_dir, from_member_id)`. These hide `CodSync`, `SmallSeaRemote`, and
`PeerSmallSeaRemote` from the caller. `push()` returns a `PushResult` that
includes a `"behind"` reason on CAS conflict. `pull()` returns a `PullResult`
with a `has_conflicts` flag.

`SmallSeaSession` in `small-sea-client` exposes `watch_notifications(known,
timeout)` for the long-poll and `ensure_cloud_ready()` for bucket setup.

`CodSync` gained a `repo_dir` parameter so callers no longer need `os.chdir`;
all git commands run with `-C repo_dir`. `_ensure_bundle_remote()` handles
bundle-tmp remote registration transparently.

### ✅ Watcher session lifecycle

The peer watcher cleans up expired sessions: when `get_peer_signal` raises
`SmallSeaNotFoundExn` (session revoked or expired), the session is removed from
`watched_sessions` and all its `watched_peers` entries are pruned. Transient
errors leave the session alive for retry on the next round.

## What remains to do

### 3. Conflict resolution hookup

After a pull and merge, SQLite conflicts in the team DB are resolved by
`harmonic-sqlite-merge`, which is already wired via `.gitattributes`. But
application-level conflicts in app data are not handled — the merge driver runs
but there is no way for the app to be notified that conflicts occurred or to
supply resolution logic. `PullResult.has_conflicts` surfaces the exit code from
`git merge`, but what the app should do with it is not yet designed.

### 4. `TeamManager.connect()` is broken

`TeamManager.connect()` calls `self.client.open_session(...)` which does not
exist on `SmallSeaClient`. The two-step flow (`request_session` /
`confirm_session`) is the right API, but this requires user interaction (reading
the PIN) that the Manager UI doesn't yet surface. `connect()` should either be
wired to the two-step flow or left as a clear stub until the UI exists.

This is tracked separately in issue 0016.

### 5. Manager invitation accept is CLI-only

The `accept_invitation` flow (invitee side) clones the team repo from the
inviter's cloud using `SmallSeaRemote`. Because the Hub session is not yet
wired into `TeamManager`, the CLI currently bypasses this by constructing
`S3Remote` directly from the local cloud config. The Manager web UI offers no
accept flow at all — users are directed to the CLI with an inline note.

Once `TeamManager.connect()` is resolved (above), `accept_invitation` should
use `SmallSeaRemote` via a Hub session instead of direct S3 credentials, and
the web UI can expose the accept step via a paste-token form (same as the
existing complete-acceptance form).

### 6. `GET /session/info` endpoint (pending)

Clients currently have to read SQLite directly to retrieve their `station_id`.
A lightweight `/session/info` endpoint returning `{station_id, team_name, ...}`
would let clients get this from the Hub session they already hold, and is a
prerequisite for wiring Manager flows that don't have direct DB access.

### 7. ntfy hookup from watcher (pending)

The peer watcher detects count increases and logs them, but does not yet fire
an ntfy push notification. This is the low-latency path for out-of-app
notifications and should be added once the ntfy transport in `SmallSeaBackend`
is connected to the watcher.

## What is NOT in scope here

- The Cuttlefish encryption layer (tracked in 0008)
- Deep device unification (tracked in 0007)
- The Hub permissions model (tracked in 0010)
- Signal file gossip/matrix form v2 (tracked in 0023)

## References

- `packages/cod-sync/cod_sync/protocol.py` — `CodSync`, `SmallSeaRemote`, `PeerSmallSeaRemote`
- `packages/small-sea-client/small_sea_client/client.py` — `SmallSeaSession`
- `packages/small-sea-manager/small_sea_manager/manager.py` — `TeamManager.push/pull`
- `packages/small-sea-hub/small_sea_hub/server.py` — `_peer_watcher_loop`, `watch_notifications`
- `packages/small-sea-manager/spec.md` — §Sync
- Issue 0023 — sync signal file (now implemented)
- Issue 0016 — `TeamManager.connect()` broken
