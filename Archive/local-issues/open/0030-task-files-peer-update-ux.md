> Migrated to GitHub issue #13.

---
id: 0030
title: SmallSeaCollectiveFiles — teammate update detection and pull UX
type: task
priority: medium
---

## Context

Issue 0026's Hub-backed sync slice is now in place:

- Files can open and cache Hub team sessions
- Files can push and pull through the Hub from CLI and web UI
- the Hub exposes `GET /session/peers`
- Files no longer requires raw peer member ID entry in the normal web pull flow
- the team DB now stores `peer.display_name`, so peer labels are team-scoped and
  not just inferred from invitation history

That is enough for a credible manual sync demo, but the product still leaks too
much implementation detail.

The next UX bar is closer to:

- "Bob has changes for Project Eagle"
- "Pull Bob's changes"
- eventually "2 teammates have updates"

Right now Files can list peers, but it cannot yet answer the more useful
question: which teammates appear to have newer content for this niche?

## What needs to happen

### 1. Hub endpoint for peer update hints

Files needs a sanctioned way to ask the Hub for teammate update signals for the
active team session.

A good first shape would be:

- peer list scoped to the active team session
- human-friendly label from `peer.display_name`
- current `signal_count`
- enough data for Files to compare "what I have seen" vs "what appears newer"

This should stay tightly scoped. Files still should not read team DBs directly.

### 2. Files-side notion of "seen" update state

Files needs a small local record of the last seen or last pulled signal per
teammate and niche, so it can distinguish:

- peers who exist
- peers who may have updates
- peers already pulled since their last signal bump

This can likely live in Files' own config or app-local metadata rather than
requiring new team DB state.

### 3. Web UI language based on teammate changes

The niche detail view should move from generic peer actions toward
change-oriented language, for example:

- "Bob has changes"
- "Pull Bob's changes"
- "No known teammate updates"

If update detection is only approximate in the first version, the UI should say
so honestly rather than implying a stronger guarantee than the system has.

### 4. Background refresh of update hints

Files should refresh peer update state without requiring a full page reload.

This does not require full background auto-sync. A first pass could use:

- light polling from the web UI, or
- Hub-backed long-polling against `/notifications/watch`

The goal is user awareness, not automatic merges.

## Not in scope

- Fully automatic background pulls
- Rich conflict resolution UI
- A full identity/profile system beyond team-scoped `peer.display_name`
- Team DB reads from Files runtime code
- Reworking path prefixes around `team_id`

## References

- `branch-plan.md` / archived branch plan for the `files-building-blocks` branch
- `packages/ssc-files/ssc_files/web.py`
- `packages/ssc-files/ssc_files/sync.py`
- `packages/small-sea-hub/small_sea_hub/server.py`
- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- Issue 0026 — initial Hub-backed sync slice
