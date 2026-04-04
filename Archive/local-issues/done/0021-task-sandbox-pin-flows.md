> Migrated to GitHub issue #32.

---
id: 0021
title: Sandbox — PIN confirmation flow for Hub sessions
type: task
priority: low
status: closed
---

## Background

The sandbox currently runs with `SMALL_SEA_AUTO_APPROVE_SESSIONS=1`, which
makes the Hub skip PIN confirmation entirely. This is convenient for scripted
and exploratory testing but means the PIN/notification flow is never exercised
interactively.

## What needs to happen

When auto-approve is **off**, each Hub session request produces a 4-digit PIN.
In a multi-participant sandbox the user needs a way to see and enter PINs for
each participant without leaving the sandbox dashboard.

### Dashboard changes

- Each participant row should show a **pending sessions** indicator when
  one or more sessions are awaiting confirmation.
- Clicking it opens an inline panel showing each pending session:
  app name, client name, PIN (retrieved from the Hub), Approve/Deny buttons.
- Approving calls `POST /sessions/confirm` with the PIN on the relevant
  Hub instance.

### Hub changes

- Add a `GET /sessions/pending` endpoint (local-only, no auth) that lists
  pending sessions with their PINs — for sandbox dashboard use only.
  This endpoint should only be available when the Hub is started in sandbox
  mode (e.g. `SMALL_SEA_SANDBOX_MODE=1`), since exposing PINs over HTTP
  would be a security issue in production.

## References

- `packages/small-sea-hub/small_sea_hub/server.py` — session endpoints
- `packages/small-sea-hub/small_sea_hub/backend.py` — `SmallSeaBackend`
  session management, `pending_session` table
- `devtools/sandbox/main.py` — sandbox dashboard
