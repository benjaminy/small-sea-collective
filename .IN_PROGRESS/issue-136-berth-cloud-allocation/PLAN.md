# Branch Plan: Berth Cloud Allocation and Hub Materialization (Slice A)

**Branch:** `issue-136-berth-cloud-allocation`
**Base:** `main`
**Primary issue:** #136 "Implement berth cloud allocation and Hub materialization"
**Predecessor:** #134 (design pass — settled the vocabulary and outcome shape)
**Related issues:** #114 (subsumed by this slice), #16 (will use the new error family)
**Kind:** Implementation branch. Schema, Manager helpers, Hub resolution, error responses, materialization, micro tests.
**Reference docs:**
- `Archive/design-record-issue-134-berth-cloud-location-semantics.md`
- `packages/small-sea-hub/spec.md` (sections: Berth Allocation Resolution, Provider Materialization, Concurrency)
- `packages/small-sea-manager/spec.md` (sections: Berth cloud allocations, Materialization feedback)

## Purpose

Slice A of the #134 design.
Replace `ss-{berth_id[:16]}` synthesis in the Hub's own-storage path with explicit `berth_cloud_allocation` lookup.
Add Manager provisioning helpers.
Surface the materialization outcome family on `POST /cloud/setup` and on first-use storage operations.
Implement the Hub's conditional writeback of provider-issued locators.

## Branch Contract

When this branch is done, all of the following are true:

1. Shared NoteToSelf has a `berth_cloud_allocation` table with a unique index on `berth_id`.
2. Manager exposes `add_berth_cloud_allocation` plus `get_berth_cloud_allocation_for_berth` that resolve berths the same way the Hub does.
3. For S3, the Manager generates a provider-safe `ss-{uuid7_hex}` location by default.
4. Hub own-storage file operations resolve `session.berth_id → berth_cloud_allocation → cloud_storage → local cloud_storage_credential`.
5. The Hub never synthesizes a bucket name from `berth_id` in the own-storage path.
   (The peer-read S3 override at `backend.py:1462` belongs to Slice B and remains untouched.)
6. `POST /cloud/setup` returns the materialization outcome JSON described in the spec.
7. Missing allocation, missing credentials, materialization failure, user-action requirement, and allocation conflict all surface as `cloud_storage_required` 409 responses with stable `reason` values.
8. Provider-issued final locator writeback uses a conditional UPDATE and does not publish anything to peers.
9. Team creation auto-allocates the Core berth when a `cloud_storage` row exists; otherwise the team is created without an allocation (see "Bootstrap Decision" below).
10. Invitation/bootstrap descriptors source the inviter's Core bucket from the allocation, not from `_bucket_name_for_protocol`. (Peer-read routing in general, and `team_device` legacy cleanup, remain Slice B/C.)

## Bootstrap Decision

The #134 plan deferred this to Slice A:
*"The first implementation slice should decide which Manager workflows immediately allocate storage for the Core berth, but the architecture should not require eager allocation for every possible app berth."*

**Decision: team creation auto-allocates the Core berth when a `cloud_storage` row exists.**

Rationale:

- The Core berth is the one berth that is always needed for a usable team.
- Auto-allocating it during team creation matches Principle 7 for the cross-product (app berths still require explicit allocation) without making every team be born unsyncable.

**No-cloud behavior:** if the participant has no `cloud_storage` row at team-creation time, `create_team` produces no allocation. The team is locally valid but storage-missing. Subsequent storage operations surface `cloud_location_missing` per the design's repairable-state framing. This matches current `create_team` behavior (which already tolerates missing cloud) and avoids forcing the test suite to set up cloud accounts everywhere.

**Invitee Core allocation:** admission allocates the invitee's own Core berth on the invitee's own cloud account, with a *fresh* `ss-{uuid7_hex}` location. The invitee does **not** inherit the inviter's naming convention or formula — that would collide on globally-namespaced S3 (AWS), and it would re-introduce the identity-formula pattern Slice A is removing. If the invitee has no `cloud_storage` row, admission proceeds without an allocation, same repairable state as team creation.

**Invitation/bootstrap descriptor:** the inviter's bootstrap transport *as advertised to the invitee* must be sourced as a single coherent record — `protocol`, `url`, and `location` all from the Core allocation joined to its `cloud_storage` row. Reading just the `bucket` half from the allocation while keeping `protocol`/`url` from caller-supplied `inviter_cloud` admits a descriptor with endpoint A and bucket B if the caller's `cloud_storage` row differs from the one referenced by the Core allocation. Concrete sites:

- `provisioning.py:4132` (`creator_bucket = _bucket_name_for_protocol(...)`) — replace with the auto-allocated Core location for the writeback into `team_device`. This keeps `team_device.bucket` consistent with the allocation during the legacy-fallback period (Slice C will remove the column).
- `provisioning.py:4330` (`create_invitation(root_dir, participant_hex, team_name, inviter_cloud, ...)`) — drop the caller-supplied `inviter_cloud` as the source of truth for descriptor transport. Instead, look up the Core berth allocation, JOIN to `cloud_storage`, and read all three (`protocol`, `url`, `location`) from that join. If no Core allocation exists, raise the structured `cloud_location_missing` error rather than emitting a half-valid descriptor. `inviter_cloud` may stay as a parameter for one release (ignored, or asserted to match the allocation's account) — pre-alpha, the cleanest move is to remove it; whatever the choice, the descriptor source must be the allocation.
- `provisioning.py:4371` (`inviter_bucket = _bucket_name_for_protocol(...)`) — removed by the same change as `create_invitation`.

General peer-read routing (`_download_peer_file`) is **not** touched in this slice — that's Slice B.

App berth allocation (Vault, etc.) remains explicit and is a documented test setup step.

## Scope

### In scope

- Schema: `berth_cloud_allocation` table + unique index in `packages/small-sea-note-to-self/small_sea_note_to_self/sql/shared_schema.sql`.
- Manager helpers in `provisioning.py`:
  - `add_berth_cloud_allocation(root_dir, participant_hex, team_name, app_name, cloud_storage_id_hex, *, location=None)` — primary form; resolves the berth the same way the Hub does.
  - `add_berth_cloud_allocation_by_berth_id(...)` — raw-berth_id form for tests.
  - `get_berth_cloud_allocation_for_berth(root_dir, participant_hex, berth_id)`.
  - Location generation: `ss-{uuid7_hex}` for S3 (validates against bucket naming rules); `ss-{uuid7_hex}` for Dropbox folder prefix; provider-issued for GDrive (writeback path).
- Manager team-creation flow: auto-allocate the Core berth when a `cloud_storage` row exists; no allocation otherwise.
- Manager invitation/admission flow: auto-allocate the invitee's own Core berth on the invitee's own cloud account with a fresh `ss-{uuid7_hex}` location; no inheritance from the inviter's naming.
- Source the Core bootstrap descriptor as one joined allocation record. The two Core-bootstrap sites (`provisioning.py:4132` and `provisioning.py:4330` / `:4371`) must read `protocol`, `url`, and `location` together from the Core `berth_cloud_allocation` joined to its `cloud_storage` row — never `protocol`/`url` from caller-supplied `inviter_cloud` plus `location` from the allocation. Caller-supplied `inviter_cloud` is either dropped or asserted to match the allocation. This keeps `team_device` writes and invitation descriptors consistent with the allocation during the legacy-fallback period.
- Hub resolution in `backend.py`:
  - New `_resolve_berth_cloud_or_raise(ss_session)` that returns a record carrying `allocation.id`, `allocation.location`, `cloud_storage` fields, and `cloud_storage_credential` fields, or raises a structured exception.
  - `_make_s3_adapter`, `_make_dropbox_adapter`, `_make_gdrive_adapter` switched to consume `allocation.location` rather than synthesizing.
- Hub materialization:
  - `MaterializationOutcome` dataclass with `status` and optional `final_location`.
  - Materialization lives on the adapter: `adapter.materialize() -> MaterializationOutcome`. This keeps protocol-specific logic with the protocol-specific code and resolves the chicken-and-egg of "materialize needs credentials + location, which is also what the adapter needs."
  - The own-storage flow is: **resolve allocation → build adapter → adapter.materialize() → [if materialized_with_locator: writeback + re-resolve + rebuild adapter] → adapter.upload/download**. The backend orchestrates; the adapter implements. The rebuild step is required because the original adapter was constructed against the requested locator; an upload through it would route to a stale name.
  - S3 `materialize()` wraps the existing `ensure_bucket_public` logic and returns `materialized` (idempotent on retry; `BucketAlreadyOwnedByYou` is success). Generic `ClientError` returns `failed`.
  - Dropbox `materialize()` is a no-op returning `materialized`.
  - GDrive `materialize()` remains stubbed; returns `needs_user_action` until a folder ID is present (this slice does not finish GDrive).
  - The backend calls `adapter.materialize()` before each storage operation; it is idempotent and caches no state.
- Conditional writeback for `materialized_with_locator`:
  - `_writeback_locator(participant_hex, allocation_id, expected_location, new_location)` performing `UPDATE … WHERE id = ? AND location = ?`.
  - 0-row update is the CAS conflict signal: Hub re-reads once and retries; persistent mismatch surfaces `cloud_allocation_conflict`.
- Endpoint integration in `server.py`:
  - `POST /cloud/setup` returns the outcome JSON.
  - `POST /cloud_file` and `GET /cloud_file` (own-storage endpoints) map structured exceptions to `409` with the `cloud_storage_required` family.
  - The full reason list: `cloud_location_missing`, `cloud_credentials_missing`, `cloud_user_action_required`, `cloud_materialization_failed`, `cloud_allocation_conflict`.
  - **`GET /cloud_proxy` is intentionally excluded.** It is descriptor-scoped bootstrap transport — callers pass `protocol`/`url`/`bucket` query params and the Hub proxies a read using credentials it picks from its participant cloud accounts. It does not resolve through `_resolve_berth_cloud_or_raise` and does not emit `cloud_storage_required` errors. Routing this through the allocation path would break invitation bootstrap, since the invitee has no allocation yet when they fetch the team repo. `/cloud_proxy`'s credential-acquisition rule stays unchanged in this slice.
- Micro tests (see below).

### Out of scope

- **Peer reads.** `_download_peer_file` keeps its current S3 override and `team_device` fallback. Slice B.
- **`member_berth_storage_announcement`.** Slice B.
- **Removing legacy `team_device(protocol, url, bucket)`.** Slice C.
- **Manager web UI.** Slice D.
- **Provider cleanup of orphaned objects.** Slice E.
- **Full GDrive provider integration.** This slice wires the outcome path but does not finish OAuth or folder-ID acquisition.
- **Real cloud calls in tests.** MinIO for S3; mocks for Dropbox and GDrive.

## Implementation Passes

Each pass should be reviewable and leave the test suite green.

### Pass 1 — Schema and idle code paths

- Add `berth_cloud_allocation` and unique index to `shared_schema.sql`.
- Pre-alpha: no migration shim. Existing test fixtures will get fresh DBs.
- No behavior change yet.

Exit criteria: schema present; `uv run pytest packages/small-sea-note-to-self/tests` green.

### Pass 2 — Manager helpers

- Add the three `add_berth_cloud_allocation*` and `get_berth_cloud_allocation_for_berth` functions in `provisioning.py`.
- Location generator: `ss-{uuid7().hex()}` truncated to fit S3's 63-char limit (3-63 lowercase, no underscores).
- Validate input: caller may pass `location`; otherwise generate.
- Reject if a row for this `berth_id` already exists (v1: one allocation per berth).

Exit criteria: helpers exist with their own micro tests; nothing else changed.

### Pass 3 — Team creation and admission auto-allocation

- In `create_team`, after the Core berth is created and if a `cloud_storage` row exists, call `add_berth_cloud_allocation` for the Core berth using the participant's first `cloud_storage` row. If no `cloud_storage` row exists, skip allocation (team remains locally valid but storage-missing).
- Replace `provisioning.py:4132` (`creator_bucket = _bucket_name_for_protocol(...)`) and the `_upsert_team_device_row` call at line 4139 so all three fields (`protocol`, `url`, `bucket`) come from the freshly-allocated Core record joined to its `cloud_storage` row. Do not mix caller-side `creator_cloud` for protocol/url with allocation-derived bucket — that would let `team_device` describe an inconsistent (endpoint A, bucket B) pair. When no allocation was created, pass `None` for all three, matching today's no-cloud path.
- In the admission code path, after the invitee's new team is set up, call `add_berth_cloud_allocation` for the invitee's own Core berth on the invitee's own `cloud_storage` row, with a fresh Manager-generated location.
- Rework `create_invitation` (`provisioning.py:4330`) so the descriptor sourcing for `protocol`, `url`, and `location` is a single JOIN: Core `berth_cloud_allocation` → `cloud_storage`. The caller-supplied `inviter_cloud` parameter must not be the source of `protocol`/`url` while the bucket comes from the allocation; that combination produces a descriptor pointing at endpoint A with bucket B. Either drop `inviter_cloud` from the signature or assert it matches the allocation's `cloud_storage_id`. If no Core allocation exists, raise the structured `cloud_location_missing` error rather than emitting a half-valid descriptor. Line `:4371`'s formula-derived `inviter_bucket` is removed as part of this rework.
- Other `_bucket_name_for_protocol` call sites (anything not feeding Core bootstrap) stay for now; they're Slice C territory.
- Update existing tests that previously pre-created `ss-{berth_id[:16]}` buckets to either rely on the auto-allocation (for Core) or call `add_berth_cloud_allocation` explicitly (for app berths).

Exit criteria: existing test suite passes; team creation produces a `berth_cloud_allocation` row when cloud is configured; admission produces one for the invitee; the inviter's invitation descriptor advertises the allocation location.

### Pass 4 — Hub resolution refactor

- Add `MaterializationOutcome` dataclass and structured exception classes (`CloudLocationMissingExn`, `CloudCredentialsMissingExn`, `CloudMaterializationFailedExn`, `CloudUserActionRequiredExn`, `CloudAllocationConflictExn`) in `backend.py` or a new `cloud_errors.py` module.
- Implement `_resolve_berth_cloud_or_raise`. Two query path:
  1. Lookup allocation by `session.berth_id`.
  2. JOIN allocation to `cloud_storage` and `local.cloud_storage_credential`.
- Rewrite `_make_s3_adapter`, `_make_dropbox_adapter`, `_make_gdrive_adapter` to take a resolved cloud record carrying `location`.
- Stop calling `_get_cloud_link` as the entry point; replace with `_resolve_berth_cloud_or_raise` for the own-storage path.

Exit criteria: Hub still works for the auto-allocated Core berth across all current tests; the synthesized S3 bucket name string `ss-{ss_session.berth_id.hex()[:16]}` is no longer present in `_make_s3_adapter`.

### Pass 5 — Materialization with outcomes and writeback

- Add `materialize()` returning `MaterializationOutcome` to each adapter (`SmallSeaS3Adapter`, `SmallSeaDropboxAdapter`, `SmallSeaGDriveAdapter`).
  - S3: wrap the existing `ensure_bucket_public` body; `BucketAlreadyOwnedByYou` is success; generic `ClientError` returns `failed`.
  - Dropbox: no-op, returns `materialized`.
  - GDrive: returns `needs_user_action` if no folder ID is present.
- Backend orchestration: own-storage flow becomes `resolve → build adapter → adapter.materialize() → [if materialized_with_locator: writeback + re-resolve + rebuild adapter] → adapter.upload/download`.
- If `materialize()` returns `materialized_with_locator`, the backend calls `_writeback_locator(participant_hex, allocation_id, expected_location, new_location)` (conditional UPDATE). On successful writeback the backend re-resolves the allocation (now carrying `new_location`) and **rebuilds the adapter** before any upload/download. The original adapter was constructed against the requested locator and would otherwise route to a stale name. On 0-row writeback the backend re-reads once and retries; persistent mismatch raises `CloudAllocationConflictExn`.
- `ensure_cloud_ready` is renamed to `materialize_for_session` and returns the outcome. Pre-alpha — no compatibility alias.

Exit criteria: `/cloud/setup` returns outcome JSON; storage ops still work end-to-end.

### Pass 6 — Endpoint and exception mapping

- Update `POST /cloud/setup` to return outcome JSON.
- Add FastAPI exception handlers (or per-endpoint try/except) that translate the structured exceptions into 409 + `{"error": "cloud_storage_required", "reason": "..."}`.
- Update `POST /cloud_file` and `GET /cloud_file` to honor the new error family.
- Leave `GET /cloud_proxy` alone — it is descriptor-scoped bootstrap transport, not own-storage. See the Scope section for the rationale.

Exit criteria: every reason in the spec table is reachable from at least one endpoint and produces the documented response.

### Pass 7 — Micro tests

See the explicit test list below.
Some tests need a controllable adapter to drive `materialized_with_locator` and `needs_user_action`; introduce a small `FakeStorageAdapter` test helper rather than mocking boto.

### Pass 8 — Cleanup and spec sync

- Verify the Hub spec and Manager spec descriptions match the implementation (they were target-state in #134; this branch makes them current-state).
- Remove the "Target behavior:" qualifier on `GET /peer_cloud_file` only if Slice B is also in this branch — otherwise leave it.
  Spoiler: not in this branch. Qualifier stays.
- Run the full test suite.
- Run `grep -rn 'ss-{.*berth_id' packages/small-sea-hub/small_sea_hub/` and confirm only the peer-read site at `backend.py:1462` remains (Slice B will remove that).

## Concrete File Changes

- **New:** `packages/small-sea-hub/small_sea_hub/cloud_errors.py` — outcome dataclass + exception classes (or co-located in `backend.py` if small).
- **Modified:** `packages/small-sea-note-to-self/small_sea_note_to_self/sql/shared_schema.sql` — add table + index.
- **Modified:** `packages/small-sea-manager/small_sea_manager/provisioning.py` — add helpers; auto-allocate in `create_team` and admission.
- **Modified:** `packages/small-sea-hub/small_sea_hub/backend.py` — `_resolve_berth_cloud_or_raise`, adapter constructors, `_materialize`, `_writeback_locator`, own-storage paths.
- **Modified:** `packages/small-sea-hub/small_sea_hub/server.py` — `/cloud/setup` response shape; exception handlers; updated docstrings.
- **Modified test fixtures:** wherever a test previously pre-created `ss-{berth_id[:16]}`, replace with the new allocation-driven setup. Affected files:
  - `packages/small-sea-hub/tests/test_cloud_api.py`
  - `packages/small-sea-hub/tests/test_notifications.py`
  - `packages/small-sea-hub/tests/test_peer_transport.py` (own-storage parts only)
  - `packages/small-sea-manager/tests/test_invitation.py`, `test_signed_bundles.py`, `test_hub_invitation_flow.py`
  - `packages/shared-file-vault/tests/test_hub_sync.py`

## Micro Tests

Per the issue's validation list, plus tests for the auto-allocation behavior.

**Allocation resolution:**

- Session with no allocation → `POST /cloud_file` returns 409 with `reason: "cloud_location_missing"`.
- Allocation exists, no local credential → 409 with `reason: "cloud_credentials_missing"`.
- Allocation exists with credential → S3 upload uses the allocation's location, not `ss-{berth_id[:16]}`.
- Direct grep: no own-storage code path computes `ss-{...berth_id...}` after this branch.

**Materialization outcomes:**

- `/cloud/setup` happy path returns 200 with `{ "status": "materialized", "location": "ss-..." }`.
- `/cloud/setup` second call on the same berth is idempotent (still 200, `materialized`).
- A `FakeStorageAdapter` configured to return a different final locator drives `materialized_with_locator`; the allocation row is updated via conditional UPDATE, and `/cloud/setup` returns the final location.
- **First-use storage** (not just `/cloud/setup`) exercises `materialized_with_locator`: an upload via `POST /cloud_file` triggers materialization, writeback, adapter rebuild, and the actual `put` against the *new* locator. Verify the upload lands at `new_location`, not `requested_location`. Regression test for the "adapter built from stale name" trap.
- A `FakeStorageAdapter` configured to require OAuth drives `needs_user_action` → 409 with `reason: "cloud_user_action_required"`.
- A `FakeStorageAdapter` configured to fail drives `failed` → 409 with `reason: "cloud_materialization_failed"`.
- `cloud_allocation_conflict`: race test where another local connection updates `berth_cloud_allocation` between the Hub's read and its writeback. The Hub re-reads, retries once, and on persistent mismatch surfaces `cloud_allocation_conflict`.

**Auto-allocation:**

- `create_team` with a configured `cloud_storage` row produces a `berth_cloud_allocation` row for the Core berth, with a `ss-{uuid7_hex}` location.
- `create_team` with no configured `cloud_storage` row produces no allocation; the team is created; a subsequent `cloud_file` returns `cloud_location_missing`.
- Admission allocates the invitee's own Core berth on the invitee's own `cloud_storage` row with a fresh location — not inherited from the inviter.
- App berth without explicit allocation → `cloud_file` returns `cloud_location_missing` (confirms Principle 7 for app berths).

**Bootstrap descriptor consistency:**

- After `create_team`, the inviter's `team_device.bucket` equals the auto-allocated Core location, not `ss-{berth_id[:16]}`.
- After invitation creation, the invitation descriptor's `protocol`, `url`, **and** `bucket` (`location`) all match the inviter's auto-allocated Core allocation joined to its `cloud_storage` row — not the caller-supplied `inviter_cloud` if that diverges.
- `create_invitation` with no Core allocation returns the structured `cloud_location_missing` error rather than emitting a half-valid descriptor.
- End-to-end: inviter creates team and invites, invitee accepts and bootstraps — the invitee successfully reads the team repo from the inviter's allocation bucket. (Regression test for the bug this review caught.)
- `GET /cloud_proxy` continues to work for an invitee NoteToSelf session with no `berth_cloud_allocation` rows (regression test: it must not route through allocation resolution).

**Integration:**

- Existing Cod-Sync round-trip test continues to pass against the new flow.
- Vault smoke test with an explicit `add_berth_cloud_allocation` for the Vault berth passes.

## Validation

A skeptical reviewer should be able to confirm:

1. The Hub never synthesizes a bucket name in own-storage code (verify by `grep`).
2. Every reason in the spec mapping table is exercised by at least one micro test.
3. The auto-allocation decision is explicit in `create_team` and admission, not hidden in synthesis.
4. The conditional writeback is race-tested.
5. `materialized_with_locator` is exercised even though no real provider in this slice produces it (FakeStorageAdapter).
6. No production code path depends on the legacy `_bucket_name_for_protocol` for own-storage routing **or for the Core-bootstrap `team_device` write**. The function may remain in use at non-bootstrap call sites that still feed `team_device` for the legacy fallback; those are Slice C. Grep target: `_bucket_name_for_protocol` no longer appears at `provisioning.py:4132` or in the `create_invitation` flow.
7. `uv run pytest packages/small-sea-hub/tests packages/small-sea-manager/tests packages/shared-file-vault/tests packages/small-sea-note-to-self/tests` is green.

## Non-Negotiable Invariants

1. The Hub must not synthesize provider-facing storage names from `berth_id` in any own-storage path.
2. A missing allocation produces `cloud_location_missing`, not a generic 500 or a silently-created bucket.
3. Provider-issued locator writeback must use a conditional UPDATE keyed on `(id, previous_location)`.
4. Materialization is idempotent. `BucketAlreadyOwnedByYou` is success, not failure.
5. No real cloud calls in tests. MinIO for S3; FakeStorageAdapter for outcome-driven tests.
6. Peer-read paths are untouched in this branch. The `team_device` table is mostly untouched: legacy `team_device` cleanup and column removal stay in Slice C. The one exception is the Core-bootstrap `team_device` write, which is updated to source `protocol`, `url`, and `bucket` from the auto-allocated Core allocation record so the legacy-fallback descriptor stays consistent with the allocation during the transition.
7. Use "micro tests" terminology in all code comments and docstrings.

## Open Questions

These should be settled during implementation, with the choice recorded in this plan:

- **Should `_materialize` run on every storage op, or only when the adapter operation fails with a not-materialized signature?**
  Decision: run before every storage op (simpler; relies on idempotency). Costs ~2 extra S3 round trips per upload (`CreateBucket` returning `BucketAlreadyOwnedByYou` plus an idempotent `PutBucketPolicy`).
  When implementing, leave a comment at the materialize call site noting that an in-memory `set[allocation_id]` cache (or a `HeadBucket` short-circuit) is the obvious future optimization once lower-hanging fruit is picked. Reference: #134 design record allows this — "Implementations may later cache or short-circuit provider checks, but correctness must not depend on hidden Hub-only materialization state."

- **Should `ensure_cloud_ready` be renamed or kept as an alias?**
  Plan default: rename to `materialize_for_session` and remove `ensure_cloud_ready`. Pre-alpha, no compatibility shim.

- **How should the Manager-triggered Hub operation authorization model work for materialization?**
  Plan default for this slice: any session can call `/cloud/setup` for its own berth. The broader auth model from #134's open questions is deferred to its own issue (#16-adjacent).
