# Consistency audit

Checked 2026-09-05 UTC against live GitHub issues and initially local HEAD `20a7f54`.
The user then updated this branch to `d3c900fb1cfa99abe5785148ce0cad4be92107b7`, matching the checked GitHub main through PR #239.
Rechecked the local history and diff after the update: the six added commits have no net file changes, so the source findings and implementation plan still apply.
The working tree was clean before creating these documents.

## Findings that affect this plan

- [#224's closing decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) supersedes its still-questioning issue body and older #139 comments.
  Devices represent a participant-owned replica; account connection does not allocate or announce another location.
  #237, #238, #139, and #235's current bodies agree on this split.
- `provisioning.add_cloud_storage` always inserts a fresh shared account and a local credential row.
  `remove_cloud_storage` attempts to delete both rows; foreign-key enforcement can reject deletion of an allocated account.
  Do not interpret the issue's description of Remove as proof that every removal succeeds, or add cascading deletion as part of this branch.
- `web.py`, `cli.py`, and Manager spec line 472 still prescribe a credentialed replacement account.
  `test_core_storage_ux.py::test_missing_location_and_missing_credentials_get_different_guidance` explicitly requires the words “replacement” and “select”.
  These are superseded behavior, not compatibility requirements.
  `Archive/design-record-manager-cloud-ux.md` explains the historical limitation and should not be silently rewritten.
- `cloud_storage.html` falls back to shared `client_id` in its Credentials column.
  An inherited OAuth account can therefore display an app identifier with no device-local credentials.
  `list_cloud_storage` also documents masking while returning the unmasked access key; masking currently occurs in the template.
  Add an honest local-state read model without exposing secrets or asserting provider validity.
- Hub `_cloud_credentials_missing` requires both S3 key fields and has token-expiry/refresh rules for OAuth.
  A row's existence is not proof of usable credentials.
  Manager should describe saved local material and let Hub I/O report usability, avoiding a second provider-auth policy implementation.
- `test_note_to_self_refresh.py::_wire_device_b_credentials` writes B's local SQLite row directly, exactly as #237 reports.
  Its setup also uses Hub account registration and provisioning helpers, and its clients share a mutable global app backend.
  Replacing the credential shortcut is useful component evidence but does not satisfy #235's stronger blank-installation, isolated-stack capstone.
- Core route readiness and local credential availability are different facts.
  A ready inherited route can coexist with missing credentials; repair guidance must not require signing another announcement just to supply credentials.
- #10's remaining questions about one active provider and remove-then-add are stale relative to explicit per-berth allocation and #237's operation split.
  Its OAuth callback sketch also places token exchange in the callback without specifying the Hub I/O boundary.
  Record these for issue maintenance; do not implement OAuth here.
- #238 explicitly owns the broader stale device-location and local-commit/cloud-publication documentation, as well as concurrency and interrupted route publication.
  #237 can correct labels it touches without claiming that ordering is already implemented.

## Known risks outside this branch

Hub `_add_cloud_location` creates a fresh shared account ID and a local credential row, duplicating account-registration logic rather than connecting an inherited ID.
Its OAuth refresh path updates an existing local credential row in place.
Both paths remain unchanged under the plan's #181 deferral; routing them through Manager would expand this branch's ownership work.
OAuth refresh semantics belong in #10, including a refresh that completes after credential replacement or disconnection.
Complete replacement is the new operation's contract; a speculative merge mode would not resolve refresh races.

`packages/small-sea-manager/small_sea_manager/sql/core_note_to_self_schema.sql` still puts credential columns in shared `cloud_storage`.
This is stale reference SQL; use the live separate `local.cloud_storage_credential` table for this implementation and defer reference cleanup.

Concurrent shared account removal can leave a sibling's local credential row orphaned because device-local credentials are not replicated or cross-device foreign-key constrained.
The Hub resolves through the shared account and allocation, so an orphan is not itself a valid route.
Shared-account removal semantics and eventual local secret cleanup deserve follow-up rather than a distributed deletion mechanism in this credential-connection branch.

Connecting keys proves neither that the provider principal is the intended account nor that it has access to every allocated location.
The current account model identifies protocol/endpoint rather than an independently verified provider principal.
Keep UI claims limited to locally saved credentials and report actual Hub failures; broader provider identity verification belongs with provider onboarding.

No implementation or executable micro tests were run for this planning-only change.

## Implementation record

Implemented 2026-09-04 on `issue-237-device-cloud-creds`.

### What was built

`provisioning.connect_cloud_storage_credentials` / `disconnect_cloud_storage_credentials`, with matching `TeamManager` methods.
Both resolve the account by ID in shared NoteToSelf and raise `ValueError` before any write on a malformed or unregistered ID.
Connection is a complete replacement (`INSERT OR REPLACE`), rejects an incomplete access-key/secret-key pair for `s3`, and touches only `local.cloud_storage_credential`.

`list_cloud_storage` gained `credentials_on_this_device`, computed from the five material columns in `_CREDENTIAL_MATERIAL_COLUMNS`.
`token_expiry` is excluded deliberately; shared `client_id` is not consulted.

`POST /cloud-storage/{id}/connect` and `.../disconnect` in `web.py`, both re-rendering `_cloud_storage_fragment`.
The template's `{% elif p.client_id %}` credentials fallback is gone.
Non-`s3` rows get "Credential setup for this provider is not available in Manager yet" and no form.
Add/remove are labelled participant-wide; disconnect is offered separately and only when material is saved.

`credentials_missing` guidance in `web.py` and `cli.py` now names connecting the selected account on this device.
`spec.md` line 471-472 and the "Cloud storage accounts" section were rewritten accordingly.

### Validation actually run

```
uv run pytest tests/test_cloud_credentials.py tests/test_core_storage_ux.py tests/test_manager.py \
  tests/test_note_to_self_refresh.py tests/test_core_route_reconciliation.py \
  tests/test_core_route_status.py tests/test_identity_bootstrap.py \
  ../small-sea-hub/tests/test_cloud_api.py
```
129 passed, 0 failed (129s), from `packages/small-sea-manager`.

`tests/test_cloud_credentials.py` is the new module: 15 fast micro tests plus the two-device MinIO witness.
The witness runs both installations through their own Hub backend, rebinding `app.state.backend` immediately before building each device's `TestClient`, so a sibling-independence assertion cannot be answered by the wrong Hub.
It uses `minio_server_gen(port=None)`.

Existing test edits: `test_core_storage_ux.py`'s obsolete "replacement"/"select" assertions became "onnect"/"on this device"/no-"econcil" assertions; `test_manager.py`'s cloud-listing regression asserts the new boolean and the renamed form heading.
`test_note_to_self_refresh.py::_wire_device_b_credentials` now selects the inherited account through `list_cloud_storage` and calls the public Manager operation; its direct SQL write and the then-orphaned `device_local_db_path` import are gone.

### Invariant evidence

Credential operations are wrapped in `_unchanged_by`, which snapshots every `cloud_storage` and `berth_cloud_allocation` row (IDs, locators, allocation generation IDs, `created_at`) before and after.
`test_saving_credentials_publishes_nothing` monkeypatches `push_note_to_self`, `push_team`, `publish_teammate_berth_storage_announcement`, `reconcile_team_route`, and `refresh_note_to_self` to raise, and checks the NoteToSelf commit count is unmoved.

The witness's end-state check is deliberately narrower than "nothing changed": `create_team` allocates the new team's own Core berth, so the assertion is that no account row changed and every inherited allocation row survives byte-identical.
Core storage announcements are not exercised, because the NoteToSelf berth syncs in passthrough mode.

### Rendered browser check

Performed 2026-09-04 in Chrome against `small-sea-manage serve` on a throwaway installation carrying all three states at once:
an inherited `s3` account with no local row, a connected `s3` account, and a `gdrive` account holding only a shared `client_id`.
The installation was deleted afterwards.

Confirmed visually:

- The three states render as intended, including the case the old template got wrong.
  The `gdrive` row says "No credentials saved on this device" and offers no form, where the removed `{% elif p.client_id %}` fallback would have shown `app: ****id` and read as configured.
- Filling the inherited row's key fields and pressing "Connect on this device" swapped the fragment in place:
  that row became "∗∗∗∗D001 — Credentials saved on this device — access not verified", its button became "Update credentials on this device", and "Disconnect this device" appeared.
  The sibling account's row did not move.
- A database read after the click confirmed the real write, three accounts still registered, and no allocation created.
- Registration is labelled participant-wide and sits below the per-account controls; removal reads "Remove account".

Not confirmed visually: "Disconnect this device" and "Remove account".
Both carry `hx-confirm`, which fires a blocking `window.confirm()` that would freeze the browser-automation session, so neither was clicked.
Both are covered by the micro tests and, for disconnect, by the two-device witness.
A human clicking those two buttons is what remains of this check.

### Observation from the rendered panel

With several accounts registered, every `s3` row carries its own always-visible access/secret key form, and the generic `credentials_missing` guidance ("Connect it under Cloud Storage below") does not say which of them to connect.
The plan allowed for an account-specific repair target "when the allocation is known" and this implementation did not build one.
Recorded in `follow-up.md` rather than fixed here.
