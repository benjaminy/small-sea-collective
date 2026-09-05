# Dynamic test service ports

Branch: `issue-249-minio-ports`.
Issue: https://github.com/benjaminy/small-sea-collective/issues/249.
Status: implemented and validated; awaiting human review.
This revision supersedes the sandbox scope and deferred shared-support placement recorded in the earlier planning observations in `notes.md`.

## Goal and boundaries

Independent pytest processes should not collide because they share hardcoded service ports.
The fixture or test launcher owns port allocation and passes the resulting endpoint to consumers.
Keep this a development tooling change, with no changes to production service defaults or Small Sea protocols.

Include real MinIO test listeners and real Hub test listeners needed for concurrent test execution.
Leave literal URLs in mocked or in-process tests alone when they do not bind a socket.
Leave `packages/ssc-files/tests/test_dropbox_latency.py` alone.
It skips unless `SMALL_SEA_DROPBOX_WORKSPACE` is set and needs internet access, so its four fixed Hub ports never bind during a normal concurrent run.
Do not introduce a port registry, distributed lock service, generic process framework, or unrelated fixture cleanup.

Sandbox port allocation is separate work and is outside this branch's implementation and validation.
That work needs an explicit decision about server identity, runtime endpoints, and S3 URLs configured through Manager.
The sandbox README describes existing behavior; it does not establish endpoint stability as a design requirement.

## Implementation

### 1. Establish the allocation and startup contract

- [x] Inventory actual listeners and their endpoint consumers, including subprocesses started outside shared fixtures.
- [x] Consolidate all five `minio_server_gen` copies into one session-scoped fixture in root `test_support.py`, imported by the relevant `conftest.py` files.
  Keep one implementation of allocation, startup readiness, and teardown, with dynamic allocation as the default.
  Seed it from the Manager copy, which already allocates API and console ports together and carries the comment explaining why `port + 1` collides with the next server's API port.
  This module already serves shared test support through the root pytest configuration's `pythonpath = ["."]`.
  Move its existing module-level Manager provisioning import into the helpers that need it so importing the MinIO fixture does not require Manager provisioning.
  Avoid adding a test dependency to a production package or importing one package's `conftest.py` from another.
- [x] Allocate MinIO API and console ports independently; never derive a console port with `port + 1`.
- [x] Check local server capabilities before choosing the binding mechanism.
  Prefer a retained bound socket or server-selected port when the actual launcher can expose it simply.
  Otherwise use bound ephemeral sockets to select distinct ports, release immediately before launch, and acknowledge the remaining allocation-to-bind race.
- [x] Make process exit and readiness part of startup success, with bounded waiting and cleanup of children and owned temporary directories on failure.
  An HTTP response from an unrelated listener must not turn a failed child launch into success.
  Do not build general recovery machinery; a bind failure must be a clear fixture/startup error.

### 2. Convert real test listeners

- [x] Replace the local MinIO fixture implementations in root tests, `cod-sync`, `small-sea-hub`, `small-sea-manager`, and `ssc-files` with imports of the shared fixture.
  Update the root `minio` wrapper to use its dynamic default and remove helpers and imports orphaned by consolidation.
- [x] Remove arbitrary port constants and explicit numeric arguments from callers, including invitation, cloud roundtrip, signed bundle, notification, peer transport, store, and smoke tests.
- [x] Replace the per-test port helpers with the fixture-owned allocation path: `_free_port()` in the `ssc-files` web and Hub sync tests, and `_free_local_port()` in `tests/test_hub_readiness_probe.py`.
- [x] Use returned endpoints or ports throughout provisioning, clients, and subprocess arguments.
- [x] Convert the root Hub fixture and the separate Hub subprocess in `tests/test_sync_roundtrip.py` to dynamic allocation.
  Preserve the roundtrip test's auto-approval configuration.
- [x] Keep explicit port control only where a concrete caller or collision check needs it.

## Validation: evidence required before calling this complete

Record exact commands, exit codes, relevant logs, and limitations in `notes.md` during implementation.
Use local services and temporary test directories only.

### Behavioral micro tests

- [x] Exercise allocation with occupied sockets and multiple requested ports; verify actual bindability and distinctness rather than asserting implementation details.
- [x] Verify fixture defaults produce usable independent MinIO endpoints and that clients reach their intended isolated data stores.
- [x] Exercise a child bind failure and confirm startup reports failure rather than accepting the service already listening there.

### Real concurrent execution

- [x] Run the affected root and package suites once to establish whether unrelated failures exist.
  Include all five fixture locations; preserve package-specific invocation where pytest module naming requires it.
- [x] Launch two independent pytest processes over the MinIO-backed suites with demonstrable overlap and separate logs.
  Both must pass, including the invitation, Hub invitation, cloud roundtrip, and signed bundle cases named in #249.
  Distinct port values alone are insufficient evidence.
  Overlap means each process logs activity inside the other's start-to-finish window; record both windows and the interleaved log lines that show it.
- [x] Run the root real-Hub smoke and sync roundtrip tests concurrently as well, so moving MinIO does not merely expose the next fixed-port collision.
- [x] Confirm subprocesses terminate and temporary resources are cleaned up after successful and failed runs.

### Repository integrity review

- [x] Search the affected tests for remaining numeric listener assignments and explain each retained one; do not mechanically replace sample or mocked URLs.
- [x] Confirm root and package-specific pytest invocations resolve the shared fixture and only one MinIO fixture implementation remains.
- [x] Confirm importing MinIO support does not import Manager provisioning and existing shared helpers still work.
- [x] Review the diff for unnecessary abstractions, production dependency changes, altered test meaning, and unrelated cleanup.
- [x] Run existing relevant micro tests and repository-required checks once after the final code changes.
  Broaden testing only for failures or unresolved concerns.

## Handoff and completion

Update the checkboxes briefly as work completes and put substantial findings in `notes.md`.
Prepare GitHub issue changes in `follow-up.md`; the human handles the PR and issue closure.
Near completion, write `final-commit-message.md` and create `design-record.md` only if a lasting non-obvious decision warrants it.
Stage implementation changes for human review and leave their commit to the human unless separately authorized.

## Review corrections

- [x] Replace anonymous MinIO health readiness with an authenticated S3 listing using credentials unique to each launch, and align binding and endpoint addresses with allocation.
- [x] Allocate the Hub port before creating an owned temporary directory.
- [x] Validate collision-after-reservation, wrong-instance credentials, cleanup, and existing endpoint consumers across all five fixture locations.
