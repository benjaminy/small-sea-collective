# Issue follow-up after implementation

Executed 2026-09-04: comments posted on #237, #139, #235, #10, #181, #238;
new issues filed as #240 (registration/connection split), #241 (`credentials_missing` account identity),
#242 (CLI credential status), #243 (shared-account removal semantics), #244 (stale reference SQL).
#237 was deliberately left open.
The drafts below are retained as the record of what was posted.
Evidence lives in `notes.md` under "Implementation record"; issue text should cite named tests rather than repeating that record.

## #237 — the branch's own issue

Report the delivered surface:

- `TeamManager.connect_cloud_storage_credentials(storage_id_hex, ...)` and `disconnect_cloud_storage_credentials(storage_id_hex)`,
  over `provisioning` helpers of the same names.
  Connection covers both first connection and replacement, and replaces the local row completely.
- `POST /cloud-storage/{storage_id}/connect` and `POST /cloud-storage/{storage_id}/disconnect`, both re-rendering the existing cloud-storage fragment.
- `list_cloud_storage` entries gained `credentials_on_this_device`.
- Provider scope: S3/MinIO credential entry only.
  Other providers render "Credential setup for this provider is not available in Manager yet" and get no form.

Name the evidence in `packages/small-sea-manager/tests/test_cloud_credentials.py`:
`test_connect_replace_and_disconnect_leave_shared_state_untouched`,
`test_saving_credentials_publishes_nothing`,
`test_saved_material_is_reported_without_claiming_the_provider_works`,
`test_web_connect_replace_and_disconnect_drive_the_manager_operation`,
and the two-device MinIO witness `test_linked_device_connects_credentials_to_the_inherited_account`,
which covers sibling independence in both directions.

Two implementation details are worth writing into the issue, because they are policy and not obvious from the code:

- `credentials_on_this_device` is true when any of `access_key`, `secret_key`, `client_secret`, `refresh_token`, or `access_token` is nonempty.
  `token_expiry` is excluded on purpose: it describes credentials rather than being any.
  Shared `client_id` is never consulted.
- The boolean reports stored material only.
  Manager contacts no provider, so the Hub remains the sole authority on whether an account is usable.

The rendered browser check was performed and is recorded in `notes.md`; connection and the corrected provider states were confirmed visually.
Two controls were not clicked, because `hx-confirm` fires a blocking dialog that freezes browser automation: "Disconnect this device" and "Remove account".
Close only after the complete issue contract passes, including the invariant and guidance checks, and after a human has clicked those two.

## #139 — Core storage repair guidance

The replacement-account workaround is gone from active code.
`credentials_missing` in `web.py` and `cli.py` now names connecting the selected account on this device and retrying;
`spec.md` was corrected to match.
`test_core_storage_ux.py::test_missing_location_and_missing_credentials_get_different_guidance` now asserts the connection wording and that "econcil" does *not* appear in the credentials advice, replacing the "replacement"/"select" assertions.
Note in the issue that "select" was never a meaningful assertion, since "selected" survives as an ordinary adjective in the new text.

Retain the issue's generic app-berth allocation and repair scope; this branch touched only the credential reason.

## #235 — two-device capstone

Name `TeamManager.connect_cloud_storage_credentials` as the credential entry point, and `test_linked_device_connects_credentials_to_the_inherited_account` as the reusable local witness.
Mark only credential enrollment and disconnection as delivered.

The witness surfaced a concrete obstacle for #235's blank-installation, isolated-stack requirement, which the issue should record:
`small_sea_hub.server.app` is a module-level singleton carrying one backend in `app.state`.
The witness works around it by rebinding `app.state.backend` immediately before constructing each device's `TestClient`, so no request is answered by the wrong device's Hub.
That is adequate for sequential requests and cannot support two concurrently live Hubs.
A real isolated-stack capstone needs per-device app instances, not a shared app with a swapped backend.

Retain #235's other dependencies and its stronger requirements.

## #10 — OAuth onboarding

Distinguish device-local credential updates from participant-wide account replacement; the two are now separate operations.
Remove the stale single-provider and remove-then-add assumptions.
Provider token exchange must stay Hub-owned.

Keep OAuth onboarding open, explicitly including credential enrollment for inherited Dropbox and Google Drive accounts, which Manager still cannot connect.

Token-refresh persistence needs its own contract, defined against this branch's semantics rather than bolted onto them:

- `connect_cloud_storage_credentials` is a complete replacement.
  Omitted fields are cleared, not retained.
  A refresh implemented by calling it would destroy whatever it did not resupply.
- Refresh therefore needs a narrower in-place mutation, and #10 must say what happens to a refresh that lands after a replacement or a disconnection.
  A generic merge flag on the connect operation does not resolve those races and should not be proposed as the fix.

## #181 — Hub/Manager credential ownership boundary

Retain the consolidation scope unchanged.
This branch added the Manager connect/replace/disconnect operations and left Hub `_add_cloud_location` and the OAuth refresh path alone, so two paths still write local credential rows at branch end.
The witness itself demonstrates the split: device A's account is registered through the Hub, and device B's credentials are connected through Manager.

## #238 — route publication and allocation documentation

Flag the paragraphs this branch touched, to avoid conflicting edits:
`packages/small-sea-manager/spec.md` line 471-472 (the `credentials_missing` repair) and the "Cloud storage accounts" section.
Retain #238's allocation settlement, delayed announcement, provider locator, and retry work; nothing here claims any of it.

## New issues to propose

Check existing coverage before filing any of these.

- **Registering an account always connects it on the registering device.**
  `provisioning.add_cloud_storage` unconditionally inserts a `cloud_storage_credential` row, even when every credential field is `None`, so there is no way to register a participant-wide account without also writing a local row.
  The new read model handles the all-`NULL` row correctly (it reports no saved material), so this is a cleanliness and API-shape problem rather than a live bug.
  Splitting registration from connection would make the two-operation model in the UI true at the provisioning layer as well.
  Evidence: `test_cloud_credentials.py::_register` has to call `disconnect_cloud_storage_credentials` to produce the inherited-account state a linked device actually has.

- **`credentials_missing` does not say which account to connect.**
  The guidance reads "Connect it under Cloud Storage below", and the panel lists every registered account with its own connection form.
  A participant with more than one account is told to connect something without being told which.
  The plan anticipated this and allowed an account-specific repair target "when the allocation is known"; this branch did not build one, because the route reason travels as a bare string with no account identity attached.
  Seen while rendering three accounts at once (see `notes.md`, "Observation from the rendered panel").
  Likely wants the selected account's ID carried alongside the reason, which touches the same route-reporting surface #238 owns — check for overlap before filing.

- **The CLI cannot see or set device-local credential status.**
  `cloud-storage` lists ID, protocol, and URL only, and its `credentials_missing` guidance now sends the user to the web Manager.
  A CLI-only user therefore cannot tell which of several accounts is disconnected.
  Showing `credentials_on_this_device` in the listing is the small half; a CLI credential-entry command is the larger question.
  Deliberately out of scope for #237.

- **Shared-account removal semantics.**
  Covers allocated-account refusal, sibling-local orphan credentials, and what removal means for retained routes.
  Include the existing foreign-key behavior as evidence: `remove_cloud_storage` deletes the local credential row and then the shared account, and foreign-key enforcement can reject the second delete for an allocated account.
  This branch did not exercise that path.
  Do not broaden #237 with cleanup machinery.

- **Stale reference SQL.**
  `packages/small-sea-manager/small_sea_manager/sql/core_note_to_self_schema.sql` still puts credential columns in shared `cloud_storage`, which does not describe the live shared/local split this branch built on.
  Deferred cleanup.
