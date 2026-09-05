# Issue #237: device-local cloud credential connection

## Status and goal

Branch plan clarified after implementer questions; no implementation has started.
On branch `issue-237-device-cloud-creds`, let a linked device connect credentials to an inherited cloud account through Manager and disconnect them without changing the participant's shared storage configuration.
The selected account, berth allocation generation and locator, and storage announcements must survive both operations unchanged.

Checked live issues on 2026-09-05 UTC: [#237](https://github.com/benjaminy/small-sea-collective/issues/237), the closing decision on [#224](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384), and #238, #139, #235, and #10.
The user updated this branch during planning; local HEAD now matches the checked GitHub main at `d3c900fb1cfa99abe5785148ce0cad4be92107b7`.
The six added commits have no net file differences from the initially inspected `20a7f54`, verified with the local Git diff after the update.
Recheck these issues and the affected code before implementation; the discrepancy audit is in `notes.md`.

## Scope and boundaries

- Add Manager-owned connect, replace-local-credentials, and disconnect operations keyed by an existing shared account ID.
- Expose S3/MinIO connection and disconnection in the existing web account panel, with truthful device-local credential status.
  Existing #10 scope leaves OAuth onboarding for a separate change; do not invent an OAuth ceremony here.
  The storage helper can accept the existing local credential fields without changing shared provider metadata.
- Replace missing-credential repair guidance in web, CLI, and Manager documentation with connection of the selected account on this device.
  CLI guidance can point to the web flow; a new CLI credential-entry interface is not required for this branch.
- Clearly distinguish device-local connection, participant-wide account registration/removal, and berth allocation changes in the Manager surface.
  Keep explicit existing account and route operations; do not make either a side effect of connecting credentials.
- Reuse the existing schema and Manager/provisioning boundary.
  Provider I/O remains in the Hub.
  A local credential save does not validate provider access or promise that storage is ready.

Allocation publication ordering and route concurrency belong to #238.
Generic app-berth provisioning belongs to #139, and the full two-device Files witness belongs to #235.
Do not change linked-team baseline acquisition, introduce credential replication, add a device-owned allocation, or implement keyring storage, migrations, or recovery machinery.
Existing Hub direct NoteToSelf access remains a separately tracked #181 limitation; this branch must not add another bypass or claim to resolve it.
Leave Hub account creation and OAuth token persistence unchanged; do not route the existing Hub cloud API through the new Manager operation on this branch.
The user confirmed this deferral during planning, so two paths still write local credential rows at branch end: the new Manager operation and Hub `_add_cloud_location`.
“Manager-owned” describes the new connect/replace/disconnect operations, not exclusive ownership of all existing credential writes.

## Implementation sequence

### 1. Local credential operations and read model

Add narrow provisioning helpers and `TeamManager` methods alongside the existing cloud account operations.
Suggested names are `connect_cloud_storage_credentials(storage_id_hex, ...)` and `disconnect_cloud_storage_credentials(storage_id_hex)`.
Both resolve the existing account in the current participant's shared NoteToSelf before changing its local credential row.
Malformed IDs and unknown accounts fail without mutation; never search by endpoint or pick the first account.
Multiple accounts may use the same endpoint.

Connection inserts or replaces only the local row for that ID, in one transaction.
Replacement should have complete supplied-credential semantics rather than silently retaining stale omitted tokens or secrets.
Do not add a merge mode for future OAuth refresh; #10 must define that separate mutation contract and its interaction with replacement/disconnection.
For the exposed S3 path, reject empty access-key/secret-key pairs before replacing a working row.
Keep shared `protocol`, `url`, `client_id`, and `path_metadata` out of the credential mutation interface.
Disconnection deletes only that local credential row and is harmless when an existing account is already disconnected.
Neither operation calls allocation, reconciliation, announcement, NoteToSelf commit, or cloud publication code.

Extend the account list with a non-secret indication of credentials saved on this device.
Add it to the `list_cloud_storage` dicts as `credentials_on_this_device`, beside the existing `id`, `protocol`, `url`, `access_key`, and `client_id` keys.
The boolean means a local row contains at least one nonempty credential field (`access_key`, `secret_key`, `client_secret`, `refresh_token`, or `access_token`).
An absent or empty row, or `token_expiry` alone, does not count.
Do not use shared `client_id` as evidence that this device is connected.
Show “Credentials saved on this device — access not verified” or “No credentials saved on this device”.
Do not label the status “connected” or “usable”, or add a per-protocol completeness rule table.
Pre-existing partial credentials count as saved material; complete S3-pair validation applies when saving through the new operation.
Distinguish local credential presence from provider-verified usability; the Hub remains the authority for typed I/O failures, including expired or rejected credentials.
Avoid importing Hub backend internals into Manager to implement a status badge.
Preserve masking where useful, and never return secret keys or tokens to a rendered form.

### 2. Manager surface and repair guidance

Add account-specific POST handlers and forms in `web.py` and `templates/fragments/cloud_storage.html`.
Follow the existing `/cloud-storage/{storage_id}/remove` convention: `POST /cloud-storage/{storage_id}/connect` for both first connection and replacement, and `POST /cloud-storage/{storage_id}/disconnect`.
Both return `_cloud_storage_fragment` so the list re-renders in place, as removal already does.
Use the existing account ID and display its provider/endpoint as context; the connection form must not edit shared account metadata.
Provide Connect on this device, Update credentials on this device, and Disconnect this device as appropriate.
An inherited unsupported-provider account must not receive an S3 form or appear connected solely because it has a client ID.
Concretely, the Credentials column's `{% elif p.client_id %}` fallback goes away; render the new boolean rather than inferring device state from shared metadata.
Show “Credential setup for this provider is not available in Manager yet” independently of its saved-credential status.

Label account addition and removal explicitly as participant-wide configuration actions.
Removal must remain distinct from disconnect and must not cascade through allocations to make the new UI work.
Describe the existing Core account/location controls as changes to shared berth placement, not credential repair.
For `credentials_missing`, direct the user to the selected account's connection control, preferably with an account-specific target when the allocation is known.
After saving credentials, tell the user to retry the original operation.
Do not automatically reconcile or publish a route: an inherited valid route can already be usable, and a separate missing-route failure has its own repair.

Update `_ROUTE_HELP` in web and CLI, related comments, and the credential/account sections of `packages/small-sea-manager/spec.md`.
Draft `credentials_missing` wording, which the revised assertions should match:

- web: "The selected cloud account has no credentials saved on this device. Connect it under Cloud Storage below, then retry."
- CLI: "The selected account has no credentials saved on this device. Connect it in the Manager under Cloud Storage, then retry."

Replace the substring assertions rather than inverting them.
"replacement" must be gone, and both messages must name credentials on this device.
Note that "selected" survives as an ordinary adjective, so a bare "select" check tests nothing useful in either direction.
`location_missing` keeps its current text and its existing "econcil" assertion.
Keep `credentials_missing` outside the no-change reconcile repair set.
Maintain separate session, route, and local credential facts; a matching route must not imply credentials exist on this device.
Leave the broad route-publication documentation correction to #238, recording overlap for its implementer.

### 3. Component and surface evidence

Add focused micro tests for the new Manager boundary, using read-only SQL assertions where needed to inspect invariants.
Cover:

- Inherited account with no local row: connect to that exact ID, then replace its local credentials, then disconnect twice.
  Compare shared account and allocation rows before and after each operation, including all metadata, IDs, and locators.
  Other accounts and their credentials remain unchanged.
- Malformed, nonexistent, and another participant's account IDs, and incomplete S3 credentials.
  Failures leave existing credentials and shared state unchanged.
- A local save produces no shared Git commit/publication or storage announcement and invokes no provider I/O.
- Missing and empty local rows, expiry-only rows, partial S3 credentials, complete S3 credentials, and OAuth accounts with only a shared client ID or with local credential material.
  Assert the specified saved-material boolean and labels, including partial credentials counting as saved and shared client ID alone not counting.
  The list does not falsely claim verified connectivity or expose secrets, and unsupported providers show the setup limitation independently of saved status.
- Real web POSTs reach the Manager operation and return the correct account status.
  Cover connect, replacement, disconnect, invalid input, and error responses without echoing secrets.
- Web and CLI missing-credential messages name connection on this device and do not require a replacement account, new location, or reconciliation.
  Revise the obsolete assertions in `test_core_storage_ux.py`; preserve the meaningful distinction between missing allocation and missing credentials.
- Account add/remove and Core allocation controls remain visibly separate, including their participant-wide effect.

Prefer a new focused credential micro-test module plus small extensions to `test_core_storage_ux.py` and the existing cloud-list regression in `test_manager.py`.
Do not turn this into a broad Manager test refactor.

### 4. Two-device local MinIO witness

Use two isolated installation roots and device-specific Manager/Hub contexts.
Build on the identity bootstrap and NoteToSelf refresh coverage in `test_note_to_self_refresh.py`.
Replace `_wire_device_b_credentials`' direct SQL write with the public Manager operation, selecting the account through Manager's listing.
Existing fixture shortcuts outside credential enrollment are component setup, not evidence that #235 is complete.
Device A's existing Hub account-registration setup may remain; it creates a fresh account ID and does not exercise inherited-account credential connection.

The focused witness must demonstrate:

1. A has an allocated, usable account and publishes shared NoteToSelf state.
   B completes identity bootstrap and inherits the same account and allocation, with no local credentials.
2. An own-store operation through B's Hub fails specifically with `cloud_credentials_missing`.
3. B connects via `TeamManager` to the inherited ID and retries through B's Hub successfully against local MinIO.
   Verify exact uploaded/downloaded bytes or an equivalent NoteToSelf round trip, not just a successful HTTP response.
4. B disconnects and its next own-store request fails with `cloud_credentials_missing` using the same running Hub context.
   This detects accidental credential caching across requests.
5. A's own Hub still completes I/O using A's untouched credentials and the same allocation.
6. B reconnects and can use the same storage again.

Snapshot the shared account rows, allocation rows including generation IDs, and applicable announcement rows around connection/disconnection.
Assert no additional account, allocation, or announcement is created, and existing values do not change.
Distinguish expected NoteToSelf sync bookkeeping or explicit content publications from forbidden credential-triggered shared changes.
Use the passthrough NoteToSelf path to isolate this issue from ordinary-team baseline work; separately assert that credential operations never call route publication.
Do not claim this proves inherited ordinary-team route usability or the full Files flow.

Ensure every provider operation under test runs through the correct device's Hub, with no direct adapter or SDK calls from Manager or the witness to transfer data.
The existing refresh file swaps a global app backend; avoid accidentally leaving B's request bound to A when testing sibling independence.
Use isolated app contexts where available, or explicitly bind and verify the active backend for each sequential request.
Use local MinIO with an available port.
`minio_server_gen(port=None)` allocates one, but only in `packages/small-sea-manager/tests/conftest.py`; the cod-sync copy of that fixture has no `None` branch and would build a `":None"` address.

## Validation and completion

Run the new credential micro tests and affected Manager suites with the workspace environment, for example:

```sh
uv run pytest packages/small-sea-manager/tests/test_cloud_credentials.py packages/small-sea-manager/tests/test_core_storage_ux.py packages/small-sea-manager/tests/test_manager.py packages/small-sea-manager/tests/test_note_to_self_refresh.py
uv run pytest packages/small-sea-manager/tests/test_core_route_reconciliation.py packages/small-sea-manager/tests/test_core_route_status.py packages/small-sea-manager/tests/test_identity_bootstrap.py packages/small-sea-hub/tests/test_cloud_api.py
```

The first path is the proposed new module, not an existing test.
Record actual commands, outcomes, and environmental limitations in `notes.md` when implementation occurs.
If implementation changes Hub behavior, include the relevant Hub regression coverage; do not broaden production code solely to simplify a witness.
Keep provider tests on loopback and do not use real credentials.

Perform one rendered browser check of inherited-account connection, disconnection, the selected-account repair link, and separation from shared removal/allocation controls.
Search active code, guidance, and tests for replacement-account instructions and direct credential injection in the updated witness.
Historical Archive records remain historical evidence.
Review the diff for package coupling, accidental schema changes, and any shared-state mutation reachable from the new operations.
Passing tests plus invariant assertions and the visible flow must demonstrate the branch contract; happy-path connection alone is insufficient.

Update `follow-up.md` with evidence for issue changes after implementation.
A human handles the PR; starting this plan does not close #237 or any related issue.
