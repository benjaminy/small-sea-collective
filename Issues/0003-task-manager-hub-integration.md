---
id: 0003
title: Wire small-sea-manager stubs to Hub
type: task
priority: medium
---

## Context

Five methods in `manager.py` are currently stubs that return placeholder data. They need to query the Hub (or the shared database) to return real information.

## Work to do

- `create_team()` — call `session.create_new_team` once Hub supports it (line 29)
- `list_teams()` — query Hub for team list (line 34)
- `get_team()` — query Hub for team details (line 42)
- `list_members()` — query the Team-SmallSeaCore station for membership records (line 57)
- `list_invitations()` — query invitation records from the Team-SmallSeaCore station (line 81)

Depends on Hub session API being stable enough to build against.

## References

- `packages/small-sea-manager/small_sea_manager/manager.py:29,34,42,57,81`
- `packages/small-sea-hub/small_sea_hub/backend.py` — Hub session and team APIs
