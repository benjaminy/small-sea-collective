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

## What remains to do

### 1. Sync triggers

Right now nothing drives sync. The spec says sync is user-initiated by default
with Hub-provided reminders, but none of that exists yet:

- **Outgoing:** After a local commit, how does the app (or the Manager) know to
  push? Who calls `push_to_remote`? There is no push entry point yet in the
  Manager or Hub that an app can invoke.
- **Incoming:** The Hub needs to monitor teammates' cloud locations and notify
  waiting apps when new bundles appear. The spec calls this a "mailbox" — it
  doesn't exist yet. Without it, apps have no way to know they should pull.

### 2. App-facing push/pull API

An app developer should not have to know about `CodSync`, `SmallSeaRemote`, or
bundle chains. The intended interface is something like:

```python
session.push(repo_dir)   # commit any local changes, bundle, upload via Hub
session.pull(repo_dir)   # download new bundles, merge into local clone
```

`SmallSeaSession` in `small-sea-client` is the natural home for this. It already
has `upload`/`download`; it needs higher-level sync methods that hide the Cod
Sync internals. The session already knows which station (and therefore which
cloud bucket) it belongs to, so the app just hands it a repo directory.

The push side needs to handle CAS conflicts gracefully (retry with a fresh
head read). The pull side needs to walk the chain, detect which bundles are
already present locally, download only the new ones, and merge.

### 3. Conflict resolution hookup

After a pull and merge, SQLite conflicts in the team DB are resolved by
`harmonic-sqlite-merge`, which is already wired via `.gitattributes`. But
application-level conflicts in app data are not handled — the merge driver runs
but there is no way for the app to be notified that conflicts occurred or to
supply resolution logic. This needs a design before it can be implemented.

### 4. `TeamManager.connect()` is broken

`TeamManager.connect()` calls `self.client.open_session(...)` which does not
exist on `SmallSeaClient`. The two-step flow (`request_session` /
`confirm_session`) is the right API, but this requires user interaction (reading
the PIN) that the Manager UI doesn't yet surface. `connect()` should either be
wired to the two-step flow or left as a clear stub until the UI exists.

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

## What is NOT in scope here

- The Cuttlefish encryption layer (tracked in 0008)
- Deep device unification (tracked in 0007)
- The Hub permissions model (tracked in 0010)

## References

- `packages/cod-sync/cod_sync/protocol.py` — `CodSync`, `SmallSeaRemote`
- `packages/small-sea-client/small_sea_client/client.py` — `SmallSeaSession`
- `packages/small-sea-manager/small_sea_manager/manager.py` — `TeamManager.connect()`
- `packages/small-sea-manager/spec.md` — §Sync, open issue "Sync mailbox API"
