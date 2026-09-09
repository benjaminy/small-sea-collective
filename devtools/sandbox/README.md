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

Opens the dashboard at `http://127.0.0.1:7000`. The default workspace is
`Scratch/Sandbox/` in the repo root, which is gitignored by `Scratch/.gitignore`.

Optional flags:

```
--workspace PATH   Use a different workspace directory
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

## Non-interactive scenarios

The `scenarios/` directory holds connected checks that run real Manager and Hub code against a temporary local MinIO server.
Run them from the repository root:

```sh
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false uv run pytest devtools/sandbox/scenarios/scenario_berth_source_resolution.py -q
```

`scenario_*.py` files are deliberately selected explicitly; the ordinary micro-test invocation does not collect them.
The Git override disables signing only for this command's temporary-repository commits.
MinIO must be installed and localhost sockets must be permitted.

The berth source scenario starts two trusted installations of one participant, makes competing placement choices, and checks pause enforcement, inspection, explicit resolution, preservation of unrelated work and publication retries at either chosen location.
It covers failures before a head write, a lost response after a successful write, and interruption before the Manager records publication success.
Sibling Core divergence is integrated using the installed SQLite Git merge driver before publication retry is exercised.

The scenarios use in-process Hub clients and temporary installation directories; they do not attach to the dashboard or its saved workspace.
Their shared FastAPI app has one active backend, so device switches are explicit and execution within a process is sequential.
`sibling_devices.py` holds their reusable two-installation setup.
Temporary state and MinIO processes are cleaned up by pytest fixtures.
Focused transaction and restoration micro tests remain in the Manager package.
