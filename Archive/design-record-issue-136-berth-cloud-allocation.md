# Design Record: Berth Cloud Allocation and Hub Materialization (Slice A)

**Branch:** `issue-136-berth-cloud-allocation`
**Primary issue:** #136
**Predecessor:** #134 (vocabulary and outcome shape)
**Slice:** A of a planned A→E sequence.
B = `member_berth_storage_announcement` (peer-read routing);
C = legacy `team_device(protocol, url, bucket)` cleanup;
D = Manager web UI;
E = orphaned-object cleanup.

## What this slice accomplished

The Hub used to synthesize provider-facing storage names from `berth_id`
(`ss-{berth_id[:16]}`) as a hidden default.
This slice replaces that with explicit Manager-owned allocation state:
a `berth_cloud_allocation` row joining a `berth_id` to a `cloud_storage` row
and a `location`.
The Hub now resolves own-storage operations through that join, materializes
lazily but explicitly against the provider, and surfaces a structured
`cloud_storage_required` 409 family when state is missing or repairable.

## Interesting choices

### Auto-allocate Core, not the full cross-product

The Manager auto-allocates the Core berth at team creation and at invitation
acceptance when a `cloud_storage` row exists.
App berths are never pre-allocated.

Reasoning: Core is the one berth that is always needed for a usable team,
so eager allocation here matches user expectations.
App berths are 1-per-team-per-app and we don't want a freshly-created team to
spawn N orphan allocations before the user has decided they want any of those
apps; that would also force every test setup to provision cloud accounts
the test does not exercise.

The cost of this choice is asymmetry: Core is eager, apps are lazy.
Future readers should treat that asymmetry as deliberate, not as an
inconsistency waiting to be flattened.

### Materialization lives on the adapter, not in the backend

`adapter.materialize() -> MaterializationOutcome` is owned by each
protocol-specific adapter.
The backend orchestrates (resolve → build → materialize → maybe writeback +
rebuild → upload/download); the adapter implements.

The alternative considered was a free-standing `_materialize_for_session` in
the backend that switched on protocol.
Both have to know credentials and location.
Putting it on the adapter keeps the protocol-specific logic with the
protocol-specific code and resolved the chicken-and-egg of "materialize needs
credentials + location, which is what an adapter is".

### `materialized_with_locator` requires rebuilding the adapter

When a provider returns a different final locator (the GDrive case, in
principle), the backend writes it back to the allocation and **re-resolves +
rebuilds the adapter** before doing the storage op.

This is non-obvious: the original adapter was constructed against the
*requested* locator.
Doing an upload through it would route to a stale name even after the
allocation row has been corrected.
The micro test
`test_materialized_with_locator_rebuilds_before_storage_op` is the regression
guard for this trap.

### Conditional CAS writeback

Locator writeback uses `UPDATE ... WHERE id = ? AND location = ?`.
A 0-row update is the CAS conflict signal; the Hub re-reads once and retries.
Persistent mismatch surfaces `cloud_allocation_conflict`.

This is the only place where the Hub legitimately writes to a Manager-owned
table.
It is framed as the Hub "recording provider reality", not the Hub choosing
policy — a narrow exception to the Manager-writes-only rule.

### Bootstrap descriptor sources protocol+url+location together

The two Core-bootstrap sites (`provisioning.py:4132` for `team_device` and
`create_invitation` for the proposal token) used to mix caller-supplied
`inviter_cloud.protocol/url` with allocation-derived `bucket`.
That combination admits a descriptor pointing at endpoint A with bucket B if
the caller passed a different `cloud_storage` row than the one the Core
allocation references.

Both sites now read all three fields from a single
`berth_cloud_allocation JOIN cloud_storage` row.
If no Core allocation exists, the inviter-side path raises the structured
`cloud_location_missing` error rather than emitting a half-valid descriptor.

### Invitees do not inherit the inviter's location

Invitee Core allocation generates a fresh `ss-{uuid7_hex}` against the
invitee's own cloud account.
Two reasons:
S3 buckets are globally-namespaced on AWS, so inheriting a name would
collide outright;
and inheriting the inviter's naming would re-introduce exactly the
identity-formula coupling this slice is removing.

### Run materialize() before every storage op

Decision: idempotent re-materialize on every storage op rather than
caching "already materialized" in the Hub.
Costs roughly two extra S3 round trips per upload (`CreateBucket` returning
`BucketAlreadyOwnedByYou` plus an idempotent `PutBucketPolicy`).

This is the simpler invariant and avoids any hidden Hub-only state.
An in-memory `set[allocation_id]` cache or a `HeadBucket` short-circuit is the
obvious future optimization once other lower-hanging fruit is picked.
The design record #134 explicitly permits this — correctness must not depend
on Hub-only materialization state, but caching is allowed.

### Two acceptable Slice-A/Slice-B/C border-crossings

PLAN invariant #6 documents two unavoidable exceptions to "Slice A leaves
peer-read paths and `team_device` untouched":

1. The Core-bootstrap `team_device` write now sources `protocol/url/bucket`
   from the allocation (otherwise the legacy-fallback descriptor would
   diverge from the allocation during the transition).
2. The Core peer-read path in `_download_peer_file` uses
   `legacy_transport.bucket` (which is now allocation-sourced via
   `team_device`) rather than the `ss-{berth_id[:16]}` formula, so Core reads
   find the correct allocated bucket.
   Vault and other app berth peer reads still use the legacy formula —
   that is explicitly Slice B (member-berth storage announcements).

The single remaining `_bucket_name_for_protocol` call site
(`finalize_linked_device_bootstrap`) is documented as an acceptable Slice C
remainder.

## Notable structural artifacts

- `packages/small-sea-hub/small_sea_hub/cloud_errors.py` — outcome dataclass
  plus the five structured exception types.
  Mirror class on the Manager side
  (`small_sea_manager.provisioning.CloudLocationMissingError`) intentionally
  duplicates the reason string rather than sharing a base class.
  FOLLOW-UP.md captures the future unification decision.
- `berth_cloud_allocation` in `shared_schema.sql` has a unique index on
  `berth_id` enforcing v1's one-allocation-per-berth rule.
- The five `cloud_storage_required` reason strings
  (`cloud_location_missing`, `cloud_credentials_missing`,
  `cloud_user_action_required`, `cloud_materialization_failed`,
  `cloud_allocation_conflict`) are the stable contract — every reason is
  exercised by at least one micro test in `tests/test_cloud_api.py`.

## What this slice does NOT do

- Peer-read routing in general — Slice B.
- `member_berth_storage_announcement` — Slice B.
- Removing legacy `team_device(protocol, url, bucket)` columns — Slice C.
- Manager web UI for allocation — Slice D.
- Provider cleanup of orphaned objects when materialization races across
  sibling devices — Slice E.
- Full GDrive OAuth and folder-ID acquisition — the outcome path is wired,
  but real GDrive bootstrap stays stubbed.

## Validation summary

- `uv run pytest packages/small-sea-hub/tests packages/small-sea-manager/tests
  packages/shared-file-vault/tests` is green
  (261 passed, 3 pre-existing skips).
- `grep -rn 'ss-{.*berth_id' packages/small-sea-hub/small_sea_hub/` returns
  exactly two hits: a notification topic in `server.py` (unrelated to
  storage) and the documented Slice B-deferred peer-read site in
  `backend.py`'s `_download_peer_file`.
- `_bucket_name_for_protocol` has exactly one remaining call site
  (`finalize_linked_device_bootstrap`), an acknowledged Slice C remainder.
