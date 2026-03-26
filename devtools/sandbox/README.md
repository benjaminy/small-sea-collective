# Small Sea Sandbox

A browser-based dashboard for spinning up interactive multi-participant test
environments locally. Useful for testing invitation flows, sync, and anything
else that requires more than one user or device.

## What it runs

- **MinIO** — one shared S3-compatible object store for all participants
- **Hub** — one Hub process per participant (auto-approve mode: no PINs)
- **Manager UI** — one Manager web UI per participant

## Prerequisites

- `minio` in your PATH ([minio.io/download](https://min.io/download))
- Everything else is handled by the workspace's uv environment

## Usage

```
uv run small-sea-sandbox
```

Opens the dashboard at `http://127.0.0.1:7000`.

Optional flags:

```
--workspace PATH   Open an existing workspace directory on startup
--port INTEGER     Dashboard port (default: 7000)
```

## Workspace

A workspace is a directory that holds all state for one sandbox session:

```
my-sandbox/
  sandbox.json          # participants, port assignments, MinIO credentials
  minio-data/           # MinIO object data
  Participants/
    {hex}/              # one directory per participant
      NoteToSelf/Sync/core.db
      {team}/Sync/core.db
      ...
```

`sandbox.json` is created on first open and reused across dashboard restarts.
Port assignments and MinIO credentials are stable across restarts — you can
close and reopen the dashboard without losing your configuration.

> **Note:** Restarting the dashboard does not automatically restart Hub and
> Manager processes. Use the Start buttons to bring them back up. See
> [issue 0022](../../Issues/0022-task-sandbox-restart.md) for planned
> reconnect improvements.

## PIN flows

Hubs started by the sandbox run with `SMALL_SEA_AUTO_APPROVE_SESSIONS=1`,
which skips PIN confirmation entirely. To test the PIN flow, see
[issue 0021](../../Issues/0021-task-sandbox-pin-flows.md).
