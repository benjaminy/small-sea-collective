> Migrated to GitHub issue #5.

---
id: 0026
title: SharedFileVault — wire push/pull sync through the Hub
type: task
priority: high
---

## Context

SharedFileVault's web UI and CLI have no sync capability.
The vault library (`vault.py`) provides `push_niche`, `pull_niche`, `push_registry`, `pull_registry` but they require caller-supplied `SmallSeaRemote` / `PeerSmallSeaRemote` objects.
Neither the web UI nor the CLI construct those remotes or hold Hub sessions.
Sync is only exercised in the latency tests, which build remotes manually.

## What needs to happen

### 1. Hub session management in SharedFileVault

The vault needs a Hub session (token) per team berth. Options:

- **Config file** — store `hub_port` and `session_token` in a per-participant
  config alongside the vault root. Simple; session tokens are already
  persisted in the Hub's DB so they survive Hub restarts.
- **Session discovery via `/session/info`** — once issue 0020 lands, the app
  can call `/info` to find itself and open a session without the user
  supplying a hex ID. For now, config file is simpler.

Session tokens should be cached after the first successful open so the user
doesn't re-approve on every vault restart.

### 2. Remote construction

`push_niche` / `pull_niche` need `SmallSeaRemote` and `PeerSmallSeaRemote`
instances with the correct `path_prefix` (e.g.
`vault/{team}/niches/{niche}/`). These can be built from the session token
and the team/niche names. The peer member IDs come from the team DB (already
accessible via provisioning helpers).

### 3. Web UI push/pull buttons

Add push and pull actions to the niche detail view:
- **Push** — `push_registry` then `push_niche`; report success or CAS conflict
- **Pull from peer** — for each peer known from the team DB, `pull_registry`
  then `pull_niche`; report new commits or conflicts
- Pull should be triggerable manually and eventually auto-triggered by
  `/notifications/watch` (long-poll from the browser via SSE or htmx polling)

### 4. CLI push/pull commands

Add `push` and `pull` subcommands to the SharedFileVault CLI, mirroring the
web UI actions. Useful for scripting and for the test harness.

### 5. Conflict handling (surface only)

For now, report merge conflicts to the user (show the conflicting files) and
leave resolution to the user in their file manager. Do not attempt
auto-resolution beyond what `git merge` does. This connects to the broader
conflict resolution design in issue 0015 §3.

## Not in scope

- Auto-triggered background sync (can be layered on once push/pull work)
- Cuttlefish encryption (issue 0008)
- Multi-device session management (issue 0007)

## References

- `packages/shared-file-vault/shared_file_vault/vault.py` — push/pull ops
- `packages/shared-file-vault/shared_file_vault/web.py` — web UI (no sync yet)
- `packages/cod-sync/cod_sync/protocol.py` — `SmallSeaRemote`, `PeerSmallSeaRemote`
- `packages/small-sea-client/small_sea_client/client.py` — `SmallSeaSession`
- Issue 0015 §3 — conflict resolution design
- Issue 0020 — Hub app self-config API (would simplify session bootstrap)
- Issue 0025 — push latency optimizations
