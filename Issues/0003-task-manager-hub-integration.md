---
id: 0003
title: Wire small-sea-manager stubs to Hub
type: task
priority: medium
status: closed
---

## Context

Five methods in `manager.py` were stubs returning placeholder data. The original plan was to query the Hub, but the architecture was clarified: reads should come from the local SQLite DB directly, with Hub sessions reserved for cloud sync only.

## Resolution

All five stubs wired up via `provisioning.py` (direct DB reads/writes, no Hub API):

- `create_team()` — calls `provisioning.create_team()`
- `list_teams()` — calls `provisioning.list_teams()` (reads NoteToSelf DB)
- `get_team()` — calls `provisioning.list_members()` + `provisioning.list_invitations()`
- `list_members()` — calls `provisioning.list_members()` (reads team DB)
- `list_invitations()` — calls `provisioning.list_invitations()` (reads team DB)

`TeamManager` now takes `root_dir` + `participant_hex` instead of relying on a Hub session for data. CLI and web UI read these from `SMALL_SEA_ROOT` / `SMALL_SEA_PARTICIPANT` env vars.

## Commits

- `62c73e9` — Fix manager.py to read from local DB (issue 0003)
- `ae45d69` — Follow-up: wire create_team, fix provisioning.py header
