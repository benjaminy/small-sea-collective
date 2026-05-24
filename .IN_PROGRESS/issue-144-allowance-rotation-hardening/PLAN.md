# Branch Plan: Bootstrap-Allowance Rotation Hardening

**Branch:** `issue-144-allowance-rotation-hardening`
**Base:** `main`
**Primary issue:** #144 "Harden storage-announcement bootstrap allowance against team-device key rotation"
**Predecessor:** #137 (Slice B — introduced the bootstrap allowance)
**Kind:** Test-only hardening branch. One micro test, no production-code changes.

## Purpose

Slice B added a local-writer own-storage bootstrap allowance in
`small_sea_hub.backend._has_current_device_storage_announcement`: when the
trusted selection is missing, the Hub accepts an announcement matching the
durable allocation and signed by this device's *current* team-device key.

"Current" is read as the newest row by `created_at`/`device_id` in the
local `team_device_key` view.
The invariant we want to pin: a storage announcement signed by a prior,
no-longer-current local device key must not satisfy the allowance.
Today this is true by construction (the allowance loop compares
`announcement.signer_key_id` against the current key's id), but the
property is not exercised by any test, so a future refactor of the
allowance, the "current key" query, or the rotation flow could silently
weaken it.

This branch adds one micro test that pins the property.

## Branch Contract

When this branch is done, all of the following are true:

1. A new micro test exists at
   `packages/small-sea-hub/tests/test_cloud_api.py` named
   `test_team_cloud_file_bootstrap_allowance_rejects_rotated_signer`
   (or similar) that:
   - sets up a team berth, allocates cloud storage, materializes;
   - publishes a storage announcement signed by the team's initial
     `team_device_key` row (call it `K1`);
   - inserts a second `team_device_key` row (`K2`) with a strictly later
     `created_at`, making `K2` the row returned by
     `_current_team_device_public_key`;
   - deletes the local `key_certificate` rows so the trusted selection
     resolves to `"missing"`;
   - asserts `POST /cloud_file` returns
     `409 / cloud_storage_required / announcement_missing` and **not**
     `200`.
2. The existing pass-1 positive-allowance test
   (`test_team_cloud_file_allows_current_device_bootstrap_announcement`)
   still passes — i.e. this branch is exercising a boundary, not
   regressing the allowance for the no-rotation case.
3. No production code under `packages/*/small_sea_*` or
   `packages/wrasse-trust/wrasse_trust` is modified. The branch is
   test-only.
4. `uv run pytest packages/small-sea-hub/tests
   packages/small-sea-manager/tests packages/shared-file-vault/tests`
   is green.

## Scope

### In scope

- One micro test in `packages/small-sea-hub/tests/test_cloud_api.py`
  next to the existing pass-1 allowance tests, reusing the
  `_publish_storage_announcement_for_session` helper that already lives
  in that file.
- A small local helper to insert a synthetic later-dated
  `team_device_key` row (`K2`). UUIDv7 for `device_id`, ISO-8601
  `created_at` strictly greater than `K1`'s.

### Out of scope

- Building a real Manager-side `rotate_team_device_key(...)` helper.
  No such helper exists today; sender-key rotation
  (`rotate_team_sender_key`) is the closest precedent. If a real
  rotation primitive lands later, the test should be ported to use it,
  but that is not in scope here. See **Follow-up**.
- Testing what happens after rotation when the trust chain *does* catch
  up to `K2` (an announcement signed by `K2` after rotation should
  pass). That is positive-allowance territory and is already covered
  by the existing pass-1 test for the non-rotated case.
- Extracting the duplicated `_publish_storage_announcement_for_session`
  test helper — that is tracked separately in #145.

## Implementation Pass

Just one pass.

1. Add the new test next to
   `test_team_cloud_file_allows_current_device_bootstrap_announcement`.
2. Add a tiny helper (local to the test module) for inserting a
   synthetic newer `team_device_key` row.
3. During development, sanity-check that *removing* the rotation step
   in the test causes it to fail with `200` instead of `409`. This
   confirms the test actually exercises the rotation discriminant
   rather than passing for an unrelated reason. Document this
   sanity-check expectation in a comment on the test.

Exit: the new test passes; the existing positive-allowance test still
passes; the full suite is green.

## Concrete File Changes

- **Modified:** `packages/small-sea-hub/tests/test_cloud_api.py` — one
  new test plus a local DB-write helper for the synthetic rotation
  step.

No other file changes anticipated.

## Validation

A skeptical reviewer should be able to confirm:

1. The new test asserts `409 / announcement_missing`, not just any
   non-200 response.
2. The rotation step is what causes the new test to differ from the
   existing positive case: removing the synthetic `K2` insert leaves
   the test passing for the wrong reason (200 via allowance). A
   sanity-check comment on the test names this expectation.
3. The branch does not touch production code paths — `git diff main..HEAD
   -- packages/ ':(exclude)packages/*/tests'` is empty.
4. `uv run pytest packages/small-sea-hub/tests
   packages/small-sea-manager/tests packages/shared-file-vault/tests`
   is green.

## Non-Negotiable Invariants

1. Test-only branch: no production-code modifications.
2. Use "micro tests" terminology in any new docstrings or comments.
3. No real cloud calls; reuse the existing MinIO fixture pattern.
4. The synthetic rotation must use a `created_at` strictly greater
   than `K1`'s, not equal. The tiebreaker is `device_id DESC`, which
   is fragile to rely on for the test's invariant.

## Follow-up

If the rotation primitive becomes a real Manager helper later, port
this test to use it. Until then, the test documents what behavior the
allowance must preserve regardless of how rotation is implemented.
