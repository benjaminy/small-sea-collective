# Planning observations

The working tree was clean when planning began.
No implementation changes or test runs have been made.

The Manager MinIO fixture already accepts `port=None` and allocates distinct API and console ports together, but its default remains 9000.
Other fixture copies default to fixed ports, and `ssc-files` selects only the API port dynamically while deriving the console port as API plus one.
The root sync roundtrip test launches its own Hub despite accepting the shared Hub fixture.

The sandbox persists ports and uses MinIO API ports in account references, routes, and data-directory names.
Its README explicitly promises stable ports across restarts, and the UI presents S3 endpoints for manual configuration through Manager.
The initial discussion suggested separate server identity for runtime port changes.
The plan instead retains persisted service ports and limits dynamic allocation to new configurations, avoiding endpoint rewriting and data migration unrelated to #249.
Fresh workspaces gain dynamic allocation; automatic recovery when a saved endpoint is occupied remains out of scope.

Shared development support placement and the server binding mechanism need a short implementation-time check of the local environment.
Port probing followed by subprocess launch is advisory, so concurrent success must not be described as a proof that allocation is race-free.

# Implementation

## What the shared fixture does

`test_support.reserve_ports` binds every requested port at once before releasing them,
so ports handed out in one call cannot collide with each other.
An entry of `None` means "any free port"; an explicit number is bound as given,
and an occupied one raises immediately.
That explicit-port check is what makes a collision a startup failure:
MinIO's health endpoint does not identify which server answered,
so a squatter on the requested port would otherwise answer the readiness probe
before the child's own exit became visible.
Polling `proc.poll()` alone was not sufficient for that case.

Allocation remains advisory.
The sockets are closed before MinIO binds them, so a concurrent process can still take a port in between.
The concurrent runs below are evidence that the window is small in practice, not proof that it is closed.

`minio_server_gen` now polls `/minio/health/live` with a 15s bound instead of sleeping 2s.
That shortened the full suite from 13m25s to 11m41s.
Failed startup terminates the child and removes the temp root directory it created.

## Hub listeners

The root `hub_server_gen` allocates dynamically and gained an `extra_env` parameter.
That let `tests/test_sync_roundtrip.py` drop its own duplicate Hub launcher
while keeping `SMALL_SEA_AUTO_APPROVE_SESSIONS=1`,
so the roundtrip Hub also gained the readiness probe it previously lacked (it slept 2s).

## Retained numeric ports

Every remaining numeric port in the tests was checked; none bind a socket.
`test_hub_readiness_probe.py` passes `http://localhost:11437/` to a fully mocked `httpx.get`.
`ssc-files` Hub tests construct `SmallSeaClient(port=11437)` and `SmallSeaClient(port=7777)` over an in-process ASGI transport.
The Manager credential, registration, and manager UI tests store `http://localhost:900x` as sample configuration values.
`test_dropbox_latency.py` is excluded by the plan.
`packages/small-sea-client/tests/test_client.py` uses a respx-mocked base URL.

`tests/conftest.py` still carries an unused `import boto3`.
That predates this branch and was left alone.

## Validation

Baseline, unmodified tree, one invocation of
`uv run pytest tests packages/cod-sync/tests packages/small-sea-manager/tests packages/ssc-files/tests packages/small-sea-hub/tests -q`:
789 passed, 3 skipped, 805s, exit 0.
The same invocation after the change: 794 passed, 3 skipped, 701s, exit 0.
The five extra tests are `tests/test_service_port_allocation.py`.
Module-name collisions did not appear, so package-specific invocation was not needed.

Two independent pytest processes over the 22 MinIO-backed and real-Hub test files,
each with its own `--basetemp` and `-p no:cacheprovider`,
launched simultaneously and timestamped per output chunk:

- A: started 13:27:22.067589, finished 13:30:58.440306, 199 passed, exit 0.
- B: started 13:27:22.067574, finished 13:30:58.440307, 199 passed, exit 0.

The windows are essentially identical, so each process ran entirely inside the other's.
Interleaved evidence from the named #249 cases, A then B:

```
13:27:56.860239 test_invitation.py::test_full_invitation_flow PASSED
13:27:57.038424 test_invitation.py::test_full_invitation_flow PASSED
13:28:05.451166 test_hub_invitation_flow.py::test_invitation_flow_via_hub PASSED
13:28:05.607684 test_hub_invitation_flow.py::test_invitation_flow_via_hub PASSED
13:28:06.096811 test_cloud_roundtrip.py::test_local_provision_then_hub_roundtrip PASSED
13:28:06.250335 test_cloud_roundtrip.py::test_local_provision_then_hub_roundtrip PASSED
13:28:09.305715 test_signed_bundles.py::test_signed_bundle_roundtrip PASSED
13:28:09.439829 test_signed_bundles.py::test_signed_bundle_roundtrip PASSED
```

`tests/test_small_sea_hub_smoke.py` and `tests/test_sync_roundtrip.py` were in the same set,
so the real-Hub listeners were exercised concurrently too and did not merely move the collision.

After both runs, `pgrep -f "minio server"` and `pgrep -f "fastapi dev"` returned nothing.
A separate scripted check drove the fixture directly:
a start against an occupied port raised `Port N is not available: [Errno 48] Address already in use`,
leaked no temp directory, and left no child process;
teardown after a successful start removed its temp directory.

## Defect found outside this branch's scope

The Hub package's `ntfy_server` fixture still publishes a container on fixed host port 9090.
Two concurrent runs of `packages/small-sea-hub/tests/test_notifications.py` reproduce it:
one passed, the other errored in the fixture with
`Bind for 0.0.0.0:9090 failed: port is already allocated`,
and left a created-but-not-started container behind (removed manually).
ntfy is neither a MinIO nor a Hub listener, so it was left alone; see `follow-up.md`.

## Review corrections

The pre-launch bind check does not prevent another listener taking a port after reservation.
MinIO readiness now requires an S3 bucket listing authenticated with credentials generated separately for each launch.
A generic HTTP 200 lacks the required S3 response, and another MinIO rejects the new credentials.
The returned credentials continue to flow through the existing test consumers.
The readiness client disables proxies and SDK retries and retains bounded connection/read timeouts.
MinIO binds and advertises IPv4 loopback, matching the address checked by allocation.
This matters because a wildcard MinIO launch can fail on IPv4 loopback while still serving on another address; `localhost` resolution can then choose a different listener.
Unexpected readiness exceptions also terminate the child and discard its owned directory.

Hub allocation now happens before creating an owned temporary directory, so an occupied port cannot leak that directory.
New micro tests cover a listener taking the port after reservation, rejection of another real MinIO's credentials, immediate failed-start cleanup, and Hub allocation failure without a temporary root.

Final focused command: `.venv/bin/python -m pytest tests/test_service_port_allocation.py tests/test_hub_readiness_probe.py -q`.
Result: 15 passed in 3.60s, exit 0.

Final concurrent command, launched twice with separate `--basetemp=/tmp/ssc-249-review-a` and `--basetemp=/tmp/ssc-249-review-b` arguments:

```sh
.venv/bin/python -m pytest tests/test_service_port_allocation.py tests/test_hub_readiness_probe.py tests/test_small_sea_hub_smoke.py tests/test_sync_roundtrip.py packages/cod-sync/tests/test_smallsea_store.py packages/small-sea-hub/tests/test_cloud_api.py packages/small-sea-manager/tests/test_hub_invitation_flow.py packages/ssc-files/tests/test_hub_sync.py -q -p no:cacheprovider
```

Both processes ran concurrently, with both logs showing 52 completed cases before either finished.
A: 57 passed in 81.34s, exit 0.
B: 57 passed in 81.34s, exit 0.
Logs: `/tmp/ssc-249-review-a.log` and `/tmp/ssc-249-review-b.log`.
These selected integration tests cover all five fixture locations; the full suites and original 22-file acceptance set were not repeated for these corrections.
`git diff --check` passed, and importing `test_support` still does not import Manager provisioning.
