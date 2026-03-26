---
id: 0022
title: Sandbox — reconnect to existing workspace on restart
type: task
priority: low
---

## Background

The sandbox currently only supports starting fresh. When the sandbox process
exits all child processes (MinIO, Hubs, Managers) are terminated. Restarting
the sandbox and pointing it at the same workspace directory starts everything
from stopped.

## What needs to happen

When a workspace directory is opened that already contains a `sandbox.json`,
the dashboard should:

1. Read the existing state (participants, port assignments, MinIO credentials).
2. Show all participants and services as **stopped**.
3. Let the user start individual services with the same port assignments as
   before, or start everything at once with a single button.

### State that needs to persist

`sandbox.json` already records participants and port assignments. What needs
to be added:

- MinIO credentials (root user/password) — so the same credentials are
  used across restarts and the participant DBs don't need updating.
- Per-participant cloud storage config should reference credentials stored in
  `sandbox.json`, not re-generated on each start.

### Process hygiene

On restart, before starting MinIO or Hubs, the sandbox should check whether
any of the assigned ports are already in use (from a previous run that didn't
clean up) and warn the user.

## References

- `devtools/sandbox/main.py` — workspace loading, process management
- `devtools/sandbox/workspace.py` — `sandbox.json` schema
