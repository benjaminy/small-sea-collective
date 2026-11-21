# Async I/O in Small Sea

Small Sea is moving its I/O to asyncio, so that one slow Hub, storage provider, or Git command does not stall everything else a process is doing.
This note records the rules the first async code settled on, and the questions it left open.
The design discussion is [issue #188](https://github.com/benjaminy/small-sea-collective/issues/188).

The rules come from code that already follows them:
Cod Sync's async modules (`packages/cod-sync/cod_sync/aio/`),
the async Hub session (`packages/small-sea-client/small_sea_client/aio.py`),
Tide Table's sync agent and demo (`packages/tide-table/`),
and the Hub's peer watcher (`packages/small-sea-hub/small_sea_hub/server.py`).

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

**Streamed responses and temporary files.**
A call that opens a streamed download, or makes a temporary directory, finishes with it before returning.
Neither outlives the call, even when the call is cancelled.

**Background tasks.**
Whoever starts a background task stops it: cancel it, then wait for it to finish.
Stop tasks before closing the HTTP client they use, and before the Hub they talk to shuts down.
When tasks feed one another, stop the ones that start work first.
Tide Table stops its merge workers, then its CalDAV servers, then its sync agents, then closes its HTTP clients.

## Git commands

Cod Sync runs Git with `asyncio.create_subprocess_exec`, in Git's own process group.
When the caller cancels, Cod Sync sends SIGTERM to the whole group, waits up to five seconds, then sends SIGKILL to anything still running.
It re-raises the cancellation only after every process in the group has exited.

SIGTERM comes first because Git then removes its lock files, such as `index.lock`.
A SIGKILL would leave them behind, and the next write would fail.
The signal goes to the whole group because Git starts children (ssh, hooks, the signing program), and a child left running could hold Git's output pipes open or outlive Git unnoticed.

## Cancelling around durable changes

A single Git command may be cancelled; the rule above stops it cleanly.
A change that takes several steps and must not stop halfway — a merge that runs Git and then records its result, for example — runs in its own task:

1. The caller takes the lock that guards the state.
2. The caller starts the change as a new task and awaits it through `asyncio.shield`.
3. The task releases the lock in a `finally` block when it finishes.
4. If the caller is cancelled while waiting, it still waits for the task to finish, and only then re-raises the cancellation.

Tide Table's merge worker does all four steps (`handle_event` in `spikes/two_teammates_demo.py`).
Its conflict resolution (`resolve` in `spikes/caldav_spike.py`) skips step 4.
A cancelled caller returns at once, while the resolution still holds the lock and is using the repository.

## Local SQLite

Short SQLite reads and writes on local files stay synchronous and run on the event loop.
So do the small file operations of Cod Sync's local-folder store.
We do not use aiosqlite: it runs SQLite in a thread, and that buys nothing for queries this short.

## Code that cannot be async yet

Some libraries we depend on only block: boto3, and the Hub's storage and notification adapters, which use synchronous httpx.
Async code runs each such call in a worker thread with `asyncio.to_thread`, one call at a time.
Changes to shared state stay on the event loop, so no two threads change it at once.

Code that waits on a thread lets other tasks run in the meantime.
If two tasks could now overlap where they could not before, guard them with an `asyncio.Lock`.
Create the lock inside the running event loop (the Hub creates its watcher lock at startup), because an `asyncio.Lock` cannot be used from a different loop than the one it first waited on.

A worker thread is a stopgap.
It frees the event loop, but a cancelled task does not stop its thread, and the thread runs on until the blocking call returns.

## Open questions

- **asyncio or AnyIO.**
  Issue #188 suggested AnyIO task groups, cancel scopes, and deadlines.
  All the async code so far uses plain asyncio, and the shield-and-wait pattern above is what that costs.
  Should we keep asyncio, or switch before more code depends on it?
- **What a cancelled publish or fetch leaves behind.**
  Cod Sync stops a cancelled Git command cleanly, but nobody has yet checked whether every step at which a publish or fetch can be cancelled leaves the repository and the store in a state the next attempt handles.
- **A sync Hub client.**
  Command-line tools could enter async code once through `asyncio.run`.
  We have not yet found a caller that needs a separate sync client.
- **The Hub's routes and the Manager.**
  Many of the Hub's `async def` routes call the synchronous backend directly, so one slow storage read in a request still stalls the Hub.
  The Manager's web routes call TeamManager the same way.
  These are the next migration steps; see #188.
