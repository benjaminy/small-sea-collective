# Replace Hub Startup Sleep With a Readiness Probe

Branch plan for `hub-startup-readiness-probe`.
Primary tracker: #17.

## Goal

Remove the `time.sleep(1)` hack in the Hub startup fixture and replace it with
a deterministic readiness check. Same fixture contract, less flakiness, less
wasted wall-clock on fast machines.

## Context

`tests/conftest.py:90` starts the Hub via `uv run fastapi dev ... --port N` in
a subprocess and then just sleeps one second before handing the endpoint back
to tests. The comment on line 90 literally calls it a hack.

Two concrete problems with the sleep:

- on a slow/loaded machine, one second isn't always enough — tests can race
  the server's first-request readiness and fail spuriously
- on a fast machine, every Hub startup pays a full second it doesn't need,
  and the suite starts multiple Hubs

The fixture does already do one correct thing: after the sleep it checks
`proc.poll()` to catch the case where the subprocess exited before it could
serve anything. Whatever replaces the sleep has to preserve that "server
died on startup" signal, not hide it behind a long poll timeout.

## Branch Claim

At the end of this branch:

- the fixture no longer contains a fixed `time.sleep` for readiness
- the fixture still fails fast and loud if the Hub subprocess exits during
  startup
- the fixture waits no longer than necessary on fast machines and waits long
  enough on slow ones, up to a bounded timeout
- the existing test suite still passes without other changes

## Non-Goals

- no new health/readiness endpoint on the Hub — the existing routes are enough
  to tell whether FastAPI is accepting requests (see "Approach" below)
- no refactor of how the Hub is launched (still `uv run fastapi dev ...`)
- no change to the fixture's public shape: `start_server(root_dir=, port=)`
  still returns the same dict
- no attempt to speed up Hub startup itself
- no parallelization of Hub launches

## Repo Findings

- `tests/conftest.py:72-99` — `start_server` helper inside the Hub fixture.
  The sleep is at line 91; the `proc.poll()` liveness check is at 92-93.
- `packages/small-sea-hub/small_sea_hub/server.py` — FastAPI app. `@app.get("/")`
  is defined at line 271 and returns an `HTMLResponse`. It does not require
  a session, so it's usable as a readiness probe without auth setup.
- `packages/small-sea-hub/tests/conftest.py:91-124` already contains a
  bounded startup poll for the test ntfy server using `httpx.get(...)` in a
  retry loop. This branch should reuse that general pattern (`httpx` +
  bounded polling), but it should intentionally use a much tighter cadence:
  the ntfy fixture tolerates slower Docker cold starts, while this Hub probe
  is local-process startup and should optimize fast-path latency.
- Hub is launched with `fastapi dev`, which prints its own startup banner on
  stdout/stderr but the fixture does not currently capture either stream, so
  parsing log lines is not the cheapest path.
- The fixture appends each started server to a list and the teardown block
  (lines 103-110) calls `terminate()` + `wait()` and cleans up the temp dir.
  The replacement must not change how that teardown sees `proc`.
- `tests/test_sync_roundtrip.py:44-65` contains a separate ad hoc Hub launcher
  with its own fixed `time.sleep(2)`. This branch is about the shared
  `hub_server_gen` fixture only; the duplicate launcher is a follow-up, not
  implicit scope creep.

## Approach

Add a small module-level helper in `tests/conftest.py`, e.g.
`_wait_for_hub_ready(proc, url, startup_timeout=5.0)`, so the probe logic can
be micro-tested directly without spinning up a real subprocess. Poll the Hub's
`GET /` over HTTP in a tight loop with a bounded overall timeout. On each
iteration:

1. check `proc.poll()` — if the subprocess has exited, raise the same
   `RuntimeError(f"Small Sea Hub exited early (code {proc.returncode})")` as
   today, so the existing failure mode is preserved exactly
2. try a short HTTP GET against `http://localhost:{port}/` with a concrete
   per-attempt timeout of 250 ms
3. if the request succeeds with any 2xx/3xx response, the server is ready —
   break out of the loop
4. if the request returns an unexpected HTTP status (4xx/5xx), raise a clear
   `RuntimeError` immediately. The server is reachable, so this is not a
   readiness delay; it is an unexpected app-level failure worth surfacing
   directly. This relies on the assumption that once FastAPI is accepting
   requests on the socket, route registration for `GET /` is effectively in
   place; if that assumption proves false in practice, we can revisit the
   status-handling rule
5. on connection refused / connection reset / read timeout, sleep a short
   backoff (25-50 ms) and retry
6. if the overall deadline is hit, terminate the subprocess, wait with a
   short timeout, and `kill()` as a fallback before raising a clear
   `RuntimeError` that names the port and elapsed time

Why `GET /` and not a new `/healthz`:

- `/` is already wired up, returns fast, and does not require auth or a
  session. Adding a dedicated health route is out of scope per the non-goals.
- If `/` ever grows auth requirements, this fixture will break loudly, which
  is a reasonable forcing function to revisit the probe rather than a hidden
  footgun.

Overall timeout: 5 seconds. This is generous enough for a cold `fastapi dev`
startup on a loaded machine; the happy path on a fast machine resolves in tens
of milliseconds.

HTTP client: `httpx`. Already a repo dependency via `small-sea-hub` /
`small-sea-client`, and there is local precedent for startup polling with
`httpx` in `packages/small-sea-hub/tests/conftest.py`.

## What We Should Avoid

- **Do not swallow subprocess death in the poll loop.** If `proc.poll()`
  returns non-None mid-loop, raise immediately with the exit code — don't
  keep polling until the overall timeout, and don't log-and-continue.
- **Do not leave the subprocess running on timeout.** If the readiness probe
  gives up, call `terminate()` + bounded `wait()` before raising, with
  `kill()` fallback so the test run doesn't hang or leak a zombie Hub.
- **Do not parse `fastapi dev` log output.** That ties the fixture to log
  formatting we don't control and requires piping stdout/stderr through a
  reader thread. HTTP polling is simpler and more robust.
- **Do not retry forever.** The bounded deadline is the whole point — an
  unbounded wait just replaces a one-second hang with a silent infinite one.
- **Do not bury unexpected HTTP responses inside the retry loop.** Once the
  app is returning a real 4xx/5xx from `GET /`, fail with that status instead
  of pretending the server is still merely "not ready."
- **Do not add subprocess stdout/stderr capture in this branch just to enrich
  timeout errors.** That is a reasonable follow-up if CI flakes need more
  diagnostics, but it adds process-I/O complexity beyond issue #17's core fix.

## Scope

### In scope

- replace the sleep in `tests/conftest.py` with a bounded HTTP readiness poll
  against `GET /`
- add targeted micro tests for the readiness helper's success and failure
  modes
- preserve the existing early-exit `RuntimeError` on subprocess death
- make sure the replacement cleans up the subprocess if the probe times out
- run the relevant existing tests to confirm no regressions in fixture users

### Out of scope

- a dedicated Hub health endpoint
- changes to how the Hub is launched or to its startup sequence
- fixture-level parallelization or caching of Hub instances
- touching any other `time.sleep` in the codebase
- cleaning up the separate fixed sleep in `tests/test_sync_roundtrip.py`
- capturing Hub stderr/stdout and attaching log tails to timeout errors

## Validation

All validation criteria were met:

- the sleep is gone from `tests/conftest.py`
- micro tests cover: delayed readiness (connection refused → 200), early
  subprocess exit before any HTTP attempt, exit detected mid-poll, unexpected
  HTTP status, timeout with clean `wait()`, timeout with `kill()` fallback
- `test_hub_server_gen_returns_only_after_hub_is_reachable` does a real Hub
  spin-up and confirms `GET /` returns 200 with the expected page content
- `test_small_sea_hub_smoke.py` failure is pre-existing
  (`CloudStorage` attribute missing), not introduced by this branch
- on a fast machine, the integration test completes in ~1.3s total (7 tests
  including one real Hub launch), well under the old fixed 1s per launch

## Implementation Notes

### Helper signature

```python
def _wait_for_hub_ready(proc, url, startup_timeout=5.0):
```

Located at module level in `tests/conftest.py` so it can be imported directly
by micro tests without a fixture.

### Retryable exceptions

`httpx.ConnectError`, `httpx.ConnectTimeout`, `httpx.ReadError`,
`httpx.ReadTimeout`. The `ConnectTimeout` was added during review — it covers
the case where the OS accepts the TCP handshake but the server is not yet
reading from the socket.

### Timeout cleanup sequence

`terminate()` → `wait(timeout=3)` → on `TimeoutExpired`: `kill()` →
`wait(timeout=3)`. The second `wait()` after `kill()` reaps the zombie; the
original plan only specified one `wait()` but the second is necessary for
clean process accounting.

### Integration test

`test_hub_server_gen_returns_only_after_hub_is_reachable` uses
`_free_local_port()` (random ephemeral port via `socket.bind(("127.0.0.1", 0))`)
to avoid port conflicts with other Hub fixtures in the session.

## Risks (retrospective)

- **`GET /` might grow an auth requirement later.** Still unresolved; fixture
  will break loudly if it does.
- **The repo still has a fixed `time.sleep(2)` Hub launcher in
  `tests/test_sync_roundtrip.py:59`.** Explicitly deferred as a follow-up.
- **Timeout errors are low-context** (no stderr capture). Deferred as a
  follow-up if startup flakes appear in CI.
