# Async I/O in Small Sea

Small Sea is moving its I/O to async code, so that one slow Hub, storage provider, or Git command does not stall everything else a process is doing.
New async code uses [AnyIO](https://anyio.readthedocs.io/) on the asyncio backend: task groups for background work, cancel scopes for timeouts and shielding.
FastAPI, Starlette, and httpx already run on AnyIO.
This note records the rules the first async code settled on, and the questions it left open.
The design discussion is [issue #188](https://github.com/benjaminy/small-sea-collective/issues/188).

The rules come from code that already follows them:
Cod Sync's async modules (`packages/cod-sync/cod_sync/aio/`),
the async Hub session (`packages/small-sea-client/small_sea_client/aio.py`),
and the Hub's peer watcher (`packages/small-sea-hub/small_sea_hub/server.py`).
Tide Table (`packages/tide-table/`) was written before we chose AnyIO and still uses plain asyncio.
It is a finished demo, and we left it as it is.

## Interface shape

Async code lives in its own modules, named after the sync modules they replace: `cod_sync.aio.repo` beside `cod_sync.repo`, `small_sea_client.aio` beside `small_sea_client.client`.
An async module shares its sync twin's exceptions, result types, and pure helpers, and imports them rather than copying them.
No method is sometimes awaitable and sometimes not.

An async module holds only what some async caller uses.
Callers move over package by package.
Once nothing uses a sync module, we delete it rather than keep both.

## Who owns what

**HTTP clients.**
The caller creates one `httpx.AsyncClient` whose `base_url` is the Hub, and passes it to every session, store, and signer it builds.
The caller closes the client, after stopping everything that uses it.
The client's timeout must outlast the longest watch the caller asks for, because the Hub holds a watch open until something changes.
(Tide Table uses the watch timeout plus 30 seconds.)

The Manager is the exception.
Each async TeamManager method opens its own client and closes it before returning (`_hub_http` in `manager.py`).
TeamManager is long-lived, and its callers are web routes, scripts that enter through `anyio.run`, and tests that each start their own event loop;
a client opened per call never outlives the loop that made it.

**Streamed responses and temporary files.**
A call that opens a streamed download, or makes a temporary directory, finishes with it before returning.
Neither outlives the call, even when the call is cancelled.

**Background tasks.**
Background tasks run in a task group, and whoever owns the task group stops them by cancelling it.
The `async with` block that holds the group does not end until every task in it has finished.
Stop tasks before closing the HTTP client they use, and before the Hub they talk to shuts down.
When tasks feed one another, nest their task groups so the ones that start work stop first.

The Hub's lifespan owns one task group.
The peer watcher runs in it, and so do the ntfy listeners, which session routes start through `app.state.task_group`.
Shutting the Hub down cancels the group.

## Git commands

Cod Sync runs Git with `anyio.open_process`, in Git's own process group.
When the caller cancels, or the call fails while Git still runs, Cod Sync sends SIGTERM to the whole group, waits up to five seconds, then sends SIGKILL to anything still running.
It does this inside `anyio.CancelScope(shield=True)`, so the cancellation does not cut the stop short.
The cancellation reaches the caller only after every process in the group has exited.

SIGTERM comes first because Git then removes its lock files, such as `index.lock`.
A SIGKILL would leave them behind, and the next write would fail.
The signal goes to the whole group because Git starts children (ssh, hooks, the signing program), and a child left running could hold Git's output pipes open or outlive Git unnoticed.

## Cancelling around durable changes

A single Git command may be cancelled; the rule above stops it cleanly.
A change that takes several steps and must not stop halfway — a merge that runs Git and then records its result, for example — runs in a shielded cancel scope while it holds its lock:

```python
async with store.lock:
    with anyio.CancelScope(shield=True):
        await store.merge(teammate_id)
```

A cancellation that arrives during the merge waits until the shielded block ends, and the caller sees it then.
The lock is released either way.

A shielded block also ignores the deadlines of the scopes around it.
If the change waits on the network, as a merge does when it asks the Hub to sign its commit, a dead Hub keeps the change waiting forever.
Do not fix that with a deadline around the whole block:
it could expire after Git has changed the repository but before the result is recorded, which is the half-finished state the shield exists to prevent.
Put the deadline on the network step alone, such as the HTTP client's timeout on the signing request, and only on a step whose failure the change already handles, the way it handles a failed Git command.

Tide Table's merge worker (`handle_event` in `spikes/two_teammates_demo.py`) does the same thing in plain asyncio, with a separate task and `asyncio.shield`.
Its conflict resolution (`resolve` in `spikes/caldav_spike.py`) does not wait for the shielded task when its caller is cancelled.
The caller returns at once, while the resolution still holds the lock and is using the repository.

## Local SQLite

Short SQLite reads and writes on local files stay synchronous and run on the event loop.
So do the small file operations of Cod Sync's local-folder store.
We do not use aiosqlite: it runs SQLite in a thread, and that buys nothing for queries this short.

## Code that cannot be async yet

Some libraries we depend on only block: boto3, and the Hub's storage and notification adapters, which use synchronous httpx.
Async code runs each such call in a worker thread with `anyio.to_thread.run_sync`, one call at a time.
Changes to shared state stay on the event loop, so no two threads change it at once.

The Hub passes `abandon_on_cancel=True` (see `_in_thread` in `server.py`), so a cancelled caller stops waiting at once and Hub shutdown does not wait out a storage provider's retries.
The thread runs on until the blocking call returns, and its result is dropped.
That is why a worker thread is a stopgap: it frees the event loop, but nothing can stop it.

Code that waits on a thread lets other tasks run in the meantime.
If two tasks could now overlap where they could not before, guard them with an `anyio.Lock`.

## Event loops in tests

An AnyIO lock or event belongs to the event loop that first waits on it.
Waiting on it from another loop raises an error.
In production each process has one loop, but each FastAPI `TestClient` starts its own, so an event left in `app.state` by one test can belong to a loop that has already finished.
The Hub creates its watcher lock at each startup for this reason.
A watch with a timeout of 0 checks for news and returns without touching the berth's event at all.

## Open questions

- **What a cancelled publish or fetch leaves behind.**
  Cod Sync stops a cancelled Git command cleanly, but nobody has yet checked whether every step at which a publish or fetch can be cancelled leaves the repository and the store in a state the next attempt handles.
- **A sync Hub client.**
  Command-line tools could enter async code once through `anyio.run`.
  We have not yet found a caller that needs a separate sync client.
- **The Hub's routes and the Manager.**
  Many of the Hub's `async def` routes call the synchronous backend directly, so one slow storage read in a request still stalls the Hub.
  Most of the Manager's web routes call TeamManager the same way.
  So far only `push_team` and `fetch_teammate_core` are async.
  These are the next migration steps; see #188.
