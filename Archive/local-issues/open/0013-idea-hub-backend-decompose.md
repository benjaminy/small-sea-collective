> Migrated to GitHub issue #19.

---
id: 0013
title: Decompose hub/backend.py into smaller modules
type: idea
priority: low
---

## Context

`backend.py` is large and handles multiple concerns: session management, cloud storage, schema checks, team operations, invitation handling. As it grows this will become harder to navigate and test.

## Ideas

- Split by concern: `sessions.py`, `cloud_storage.py`, `teams.py`, `invitations.py`
- Keep a thin `backend.py` that assembles them (or rename it to `app.py` / `server.py`)
- Easier to add permission checks (issue 0010) if each domain is its own module

## References

- `packages/small-sea-hub/small_sea_hub/backend.py`
- `Scratch/suggestions.md` — original source
