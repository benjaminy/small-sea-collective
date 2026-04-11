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
  retry loop. This branch should stay stylistically close to that existing
  pattern unless there is a clear reason not to.
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
2. try a short HTTP GET against `http://localhost:{port}/` with a small
   per-attempt timeout
3. if the request succeeds with any 2xx/3xx response, the server is ready —
   break out of the loop
4. if the request returns an unexpected HTTP status (4xx/5xx), raise a clear
   `RuntimeError` immediately. The server is reachable, so this is not a
   readiness delay; it is an unexpected app-level failure worth surfacing
   directly
5. on connection refused / connection reset / read timeout, sleep a short
   backoff (e.g. 25-50 ms) and retry
6. if the overall deadline is hit, terminate the subprocess, wait with a
   short timeout, and `kill()` as a fallback before raising a clear
   `RuntimeError` that names the port and elapsed time

Why `GET /` and not a new `/healthz`:

- `/` is already wired up, returns fast, and does not require auth or a
  session. Adding a dedicated health route is out of scope per the non-goals.
- If `/` ever grows auth requirements, this fixture will break loudly, which
  is a reasonable forcing function to revisit the probe rather than a hidden
  footgun.

Overall timeout: pick something generous enough for a cold `fastapi dev`
startup on a loaded machine but still much faster than "hang forever." A
5-second ceiling is the starting point; if CI shows it's too tight we widen
it, but the point is that the happy path on a fast machine becomes tens of
milliseconds instead of a full second.

HTTP client: use `httpx`. It is already a repo dependency via
`small-sea-hub`/`small-sea-client`, and there is already local precedent for
startup polling with `httpx` in `packages/small-sea-hub/tests/conftest.py`.

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

## Validation

- the sleep is gone from `tests/conftest.py`
- a focused micro test proves the helper returns once `GET /` starts
  succeeding after one or more connection failures
- a focused micro test proves the helper raises the existing
  `Small Sea Hub exited early (code X)` error immediately if `proc.poll()`
  goes non-None during the probe
- a focused micro test proves timeout cleanup is bounded: `terminate()` is
  called, `wait(timeout=...)` is attempted, and `kill()` is used if needed
- the top-level tests that actually use `hub_server_gen` still pass locally:
  at minimum `tests/test_small_sea_hub_smoke.py` and the relevant fixture
  consumer path
- on a fast machine, a representative fixture user starts materially faster
  than the old fixed one-second sleep

## Implementation Order

### Phase 1: Lock the helper contract

- add a module-level `_wait_for_hub_ready(...)` helper in `tests/conftest.py`
- define the retryable exception set and the behavior for unexpected HTTP
  statuses
- define bounded timeout cleanup (`terminate()` + `wait(timeout=...)` +
  `kill()` fallback)

### Phase 2: Add micro tests

- add focused micro tests for delayed readiness, early subprocess exit, and
  timeout cleanup
- use fakes / monkeypatching rather than a real Hub subprocess so these tests
  stay fast and deterministic

### Phase 3: Land the fixture change

- swap the sleep call site for the helper
- keep the `proc.poll()` early-exit check as the first thing the helper does
  each iteration
- remove the `TODO: sleep seems like a hack` comment

### Phase 4: Verify integration

- run the relevant existing test coverage around `hub_server_gen`
- spot-check one or two Hub-using tests with a stopwatch to confirm the
  happy path is noticeably faster than the old fixed sleep

## Risks

- **`GET /` might grow an auth requirement later.** Mitigation: if/when that
  happens, the probe breaks loudly during test runs, which is the right time
  to revisit. Not worth pre-solving now.
- **Module-local helper extraction is a tiny scope increase from the current
  nested fixture layout.** Mitigation: keep the helper private to
  `tests/conftest.py`; the point is testability, not API design.
- **Short per-attempt HTTP timeouts on a very slow machine could falsely
  look like "not ready yet" even though the server is fine.** Mitigation:
  the overall deadline is what matters; per-attempt timeouts just bound the
  retry cadence. Tune if CI complains.
- **The repo still has another fixed-sleep Hub launcher after this branch.**
  Mitigation: call it out explicitly as a follow-up rather than silently
  widening the branch.
