# GitHub follow-up after implementation

## Issue 181

Update the issue to cover writes as well as reads and record the chosen boundary.
Manager makes management changes by default; Hub direct reads and enumerated execution-related writes are permitted.
Report the authoritative schema owners, removal of Hub account registration, treatment of mirrored ORM models, and actual validation evidence.
Explicitly address the #48 co-writer observation and the #237 credential-registration consolidation comment.
Retained Hub writes mean SQLite publication/adoption coordination remains necessary.

## Issue 8

Reconcile its access-boundary wording with the decision in #181 and link the implementation.
Preserve the ordinary-app Hub API boundary.
Do not implement its proposed discovery endpoints or endorse its unauthenticated-localhost rationale as part of this branch.

## New issue: Consider removing ORM usage in favor of explicit SQL

Deferred by user agreement; create after the implementation work.
This is an evaluation task, not a commitment to remove every ORM.

Inventory remaining SQLAlchemy ORM uses and assess whether relationship handling, type conversion, or session behavior provides enough value to justify the indirection.
Evaluate explicit SQL and small typed result records against reviewability, transaction clarity, schema ownership, and maintenance cost in an agent-generated codebase.
Account for SQLite-specific operations and attached databases.
Distinguish ORM removal from removing SQLAlchemy Core or the dependency altogether.
Recommend a bounded next step backed by representative code paths and a validation story.
Do not justify either choice solely by the ease of generating boilerplate.

Until that evaluation, avoid expanding ORM usage.

## Additional findings

### New issue: `small-sea-hub` imports `small_sea_manager` without declaring it

`packages/small-sea-hub/small_sea_hub/server.py` imports `small_sea_manager.admission_events`
and `small_sea_manager.provisioning`, but `packages/small-sea-hub/pyproject.toml` declares
neither.
The workspace install hides this, so the Hub is only importable in practice because something
else pulls the Manager in.
Pre-existing and unchanged by this branch, which added no import.

Declaring it makes the direction explicit and would be worth doing now that the boundary is
settled: the Hub depends on the Manager, and the Manager declares no dependency on the Hub,
so there is no cycle to resolve first.

The parallel case in `packages/ssc-files/pyproject.toml`, whose tests import both Manager and
Hub without declaring either, belongs in the same issue.

### Not a finding: `text` import in Hub `backend.py`

`sqlalchemy.text` is imported and unused in `packages/small-sea-hub/small_sea_hub/backend.py`.
It was already unused before this branch, so it is left in place and mentioned rather than
removed.

### New issue: four Manager merge-path tests fail at `edc065e`

Four tests in `packages/small-sea-manager/tests` fail on the branch point, before any of this
branch's changes, all inside `cod_sync/git.py` on a merge path:
`test_device_link.py::test_device_link_honored_after_fetch_merge_without_extra_shared_state`,
`test_invitation_route_delivery.py::test_the_couriered_row_merges_cleanly_with_the_invitees_own_history`,
`test_merge_conflict.py::test_concurrent_invitations_merge`, and
`test_sender_key_rotation.py::test_parallel_device_prekey_bundle_rows_merge`.
Confirmed pre-existing by stashing the branch and rerunning.
Check for an existing issue before filing; a shared cause is likely given they share a code path.

### Possible issue: MinIO-backed suites collide on fixed ports

Several S3 tests bind fixed MinIO ports, so two pytest processes running these packages at
once fail in ways that look like real regressions.
Worth a fixture change to allocate a free port, as `ssc-files` already does with `_free_port()`.

Add further focused issue proposals here if later review finds concrete concurrency,
partial-failure, or authorization problems outside this branch.
Check for existing issues before proposing duplicates.

## Done (2026-09-05)

- #181: two comments — the chosen boundary with the enumerated write exceptions, schema owners,
  removal of Hub account registration, ORM mirror removal, validation evidence, and explicit
  answers to the #48 co-writer and #237 consolidation comments; plus a short cross-link comment.
  Left open; a human closes it with the PR.
- #8: comment reconciling its access-boundary wording with #181, preserving the ordinary-app Hub
  API boundary and explicitly not endorsing its discovery endpoints or unauthenticated-localhost
  rationale.
- Filed #246 (ORM evaluation, `status:deferred`), #247 (undeclared cross-package dependencies),
  #248 (four pre-existing Manager merge-path failures), #249 (MinIO fixed-port collisions).
- No issue filed for the unused `sqlalchemy.text` import; mentioned in the #181 cross-link comment.
