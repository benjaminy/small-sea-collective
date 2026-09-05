# Notes

## Pre-implementation verification (2026-09-05)

Checked the plan's code and document references against the tree before handoff.
Everything the plan names exists as described.
The findings below are scope facts, not new decisions; nothing here changes the agreed boundary.

### Symbols the plan names

| Reference | Location |
| --- | --- |
| `add_cloud_location` / `_add_cloud_location` | `packages/small-sea-hub/small_sea_hub/backend.py:1035`, `:1061` |
| `Nickname`, `Team`, `App`, `TeamAppBerth` | `backend.py:178`, `:185`, `:193`, `:200` |
| Live `Nickname` query | `backend.py:438`, inside `_find_participant` (`:416`) |
| `_resolve_berth` | `backend.py:443` |
| `provisioning.add_cloud_storage` | `packages/small-sea-manager/small_sea_manager/provisioning.py:6452` |

`Team`, `App`, and `TeamAppBerth` have no remaining references, so step 3 removes three dead definitions and rewrites one live query.

### The migration target is a strict superset

`provisioning.add_cloud_storage` performs the same two inserts as `_add_cloud_location`,
into `cloud_storage` and `local.cloud_storage_credential`,
and additionally accepts `access_token` and `token_expiry` where the Hub hardcodes both to `None`.
Both return the storage id as hex.

This matters for step 2: migrating a call site is a parameter change, not a behavior change,
and it does not need to be split into a registration call plus a separate credential-connect call.
The #237 witnesses are the exception, because separating those two operations is the thing they exist to demonstrate.

### Caller inventory

29 call sites in 15 test files.
No callers in `devtools/`, in root `tests/`, or in non-test code.

| Package | Files | Sites |
| --- | --- | --- |
| `small-sea-manager` | `test_invitation.py` (5), `test_note_to_self_refresh.py` (4), `test_signed_bundles.py` (2), `test_hub_invitation_flow.py` (2), `test_core_peer_fetch_encrypted.py` (2), `test_publish_core_state.py` (1), `test_identity_bootstrap.py` (1), `test_cloud_roundtrip.py` (1), `test_cloud_credentials.py` (1) | 19 |
| `small-sea-hub` | `test_notifications.py` (2), `test_cloud_api.py` (2), `test_backend_smoke.py` (1) | 5 |
| `ssc-files` | `test_web_sync.py` (2), `test_hub_sync.py` (2) | 4 |
| `cod-sync` | `test_smallsea_store.py` (1) | 1 |

`ssc-files` was not named in the original plan.

### No new package dependencies

Every affected test file in `cod-sync` and `ssc-files` already imports `small_sea_manager.provisioning`,
so moving these call sites to Manager provisioning introduces no dependency that the test suite does not already have.
`ssc-files/pyproject.toml` declares neither `small-sea-manager` nor `small-sea-hub`,
but its tests already import both, so this is pre-existing and outside the branch.

### The removal is contained to the Python API

No HTTP route in `packages/small-sea-hub/small_sea_hub/server.py` and no client-SDK method exposes `add_cloud_location`.
Removing it changes no wire surface.

### Documents carrying affected claims

`Documentation/local-folder-layout.md` and `Documentation/apps-and-teams.md` mention the `SmallSeaCollectiveCore` berth
but make no exclusivity or never-writes claim, so they need no edit.
The live contradictions are listed in plan step 4.

## Implementation inventory (2026-09-05)

Step 1 of the plan.
Hub package persistence paths by database, separating provider I/O from management action
and shared Core from device-local state.

### Hub-local database (`path_local_db`) — Hub-owned, not an exception

| Operation | Location |
| --- | --- |
| Schema creation and `user_version` marker | `backend.py:302`-`:395` |
| `unknown_app_sighting` upsert, read, delete | `backend.py:567`, `:609`, `:647`, `:679` |
| `pending_session` insert, `session` insert/read/delete | `backend.py:747`, `:801`, `:953`, `:1030` |
| `bootstrap_session` insert, read, delete | `backend.py:990`, `:1009`, `:1020` |

### Shared Core reads (NoteToSelf `core.db` and team `core.db`) — permitted by the decision

| Operation | Location |
| --- | --- |
| Participant lookup by nickname | `backend.py:438` (ORM `Nickname`; step 3 rewrites) |
| Team / app / berth resolution | `backend.py:458`, `:526`, `:539`, `:544` |
| Cloud storage account and berth allocation reads | `backend.py:1111`, `:1142`, `:1890` |
| Teammate and invitee-label reads | `backend.py:1372`, `:1381`, `:1385` |
| Key certificates, berth storage announcements, team device keys | `backend.py:1693`, `:1724`, `:1752` |
| `self_in_team` and team device public key reads | `backend.py:1767`, `:1781` |
| Watcher peer enumeration | `server.py:64` |

### Shared Core writes

| Operation | Location | Disposition |
| --- | --- | --- |
| `cloud_storage` + `local.cloud_storage_credential` insert | `backend.py:1079`, `:1086` | Account registration; step 2 removes it |
| `berth_cloud_allocation.location` conditional update | `backend.py:1267` | Exception 1, retained |

`berth_cloud_allocation.location` is the only shared Core write that survives this branch.

### Device-local writes (`device_local.db`)

| Operation | Location | Disposition |
| --- | --- | --- |
| Refreshed access token and expiry | `backend.py:1305` | Exception 2, retained |
| Team sender key save | `crypto.py:commit_encrypted_upload` | Exception 3, retained |
| Peer sender key save after decrypt | `crypto.py:decrypt_group_payload` | Exception 3, retained |

### Indirect writes through imported Manager helpers

Not visible as SQL in the Hub package.
All are device-local; none writes shared Core, and none makes a management decision.

| Helper called from `server.py` | Writes |
| --- | --- |
| `reconcile_runtime_state` (`:233`) | Team sender key rotation and runtime reconciliation state |
| `mark_redistribution_delivery` (`:245`) | `redistribution_delivery` row |
| `receive_sender_key_distribution` (`:332`) | Received peer sender key |
| `mark_redistribution_receipt` (`:345`) | `redistribution_receipt` row |
| `mark_admission_event_notified` (`:194`) | `notified` disposition in the per-team admission event store |

Resolved with the user rather than grandfathered:
the first four are the same kind as exception 3 — the Hub performs the operation that advances
device-local cryptographic runtime state — so exception 3's wording is broadened to cover
key material, rotation, reconciliation state, and redistribution delivery/receipt marks.
`mark_admission_event_notified` is classified with sessions and sightings as an ordinary
Hub-local runtime record: the Hub owns notification delivery, so it owns the record of having
notified, and the mark carries no authority over admission.
It is worth noting that this store lives under the participant's team directory rather than in
the Hub database.

### Protocol allowlist resolution

Step 2 moves unknown-protocol validation into Manager registration.
Adopting the Hub's `s3`, `webdav`, `gdrive`, `dropbox` list unchanged would reject `localfolder`,
which existing Manager callers use throughout and which `manager.py:173`, `:289` and
`provisioning.py:1966`, `:1985`, `:1997`, `:2319` branch on explicitly.
The registration contract is therefore those four plus `localfolder`.
`webdav` has no adapter dispatch in `backend.py` but is retained, because dropping it would
narrow the accepted set beyond what this branch decided.

## Validation (2026-09-05)

All commands run as `.venv/bin/python -m pytest <target> -q` from the repository root.
No real provider was contacted; the S3 tests use the local MinIO fixture and the rest use
localfolder or temporary databases created by the real initializers.

| Target | Result |
| --- | --- |
| `packages/small-sea-manager/tests/test_cloud_registration.py` (new) | 4 passed |
| `packages/small-sea-hub/tests/test_backend_smoke.py` + `test_cloud_api.py` | 24 passed |
| `packages/small-sea-manager/tests/test_cloud_credentials.py` + `test_cloud_roundtrip.py` + `test_publish_core_state.py` | 34 passed |
| `test_invitation.py` + `test_hub_invitation_flow.py` + `test_signed_bundles.py` + `test_core_peer_fetch_encrypted.py` + `test_identity_bootstrap.py` + `test_note_to_self_refresh.py` | 30 passed |
| `packages/ssc-files/tests/test_web_sync.py` + `test_hub_sync.py` + `packages/cod-sync/tests/test_smallsea_store.py` + `packages/small-sea-hub/tests/test_notifications.py` | 27 passed |
| `packages/small-sea-hub/tests` (whole package) | 126 passed |
| `packages/small-sea-manager/tests` (whole package) | 349 passed, 5 failed — see below |

### The five Manager-package failures

Four are pre-existing and unrelated to this branch.
They reproduce identically with the branch stashed, at `edc065e`:

- `test_device_link.py::test_device_link_honored_after_fetch_merge_without_extra_shared_state`
- `test_invitation_route_delivery.py::test_the_couriered_row_merges_cleanly_with_the_invitees_own_history`
- `test_merge_conflict.py::test_concurrent_invitations_merge`
- `test_sender_key_rotation.py::test_parallel_device_prekey_bundle_rows_merge`

All four fail inside `cod_sync/git.py` during a merge path, none touches cloud account
registration, and none is in a file this branch modified.
They are noted here rather than fixed, since they are outside this branch.

The fifth, `test_invitation.py::test_full_invitation_flow`, was a fixture-port collision:
the full-package run overlapped a second pytest process, and the S3 tests bind fixed MinIO
ports.
It passes with nothing else running, together with the other files that failed the same way
during overlapping runs (`test_hub_invitation_flow.py`, `test_cloud_roundtrip.py`,
`test_signed_bundles.py`): 7 passed.
Do not run two MinIO-backed suites concurrently.

One earlier attempt passed `--timeout=600`, which this pytest does not accept; that run
exited without collecting anything and was rerun without the flag.
No unavailable local prerequisite was hit.

### What the existing suites already witness

Existing suites cover locator writeback and cryptographic runtime progress.
Independent review found that Hub token-refresh persistence still needed the planned micro test; that witness was added after review.

- Locator materialization and the stale-allocation rejection: `test_cloud_api.py`
  `test_materialized_with_locator_rebuilds_before_storage_op`,
  `test_locator_writeback_race_returns_cloud_allocation_conflict`,
  `test_a_stale_locator_cannot_claim_a_replacement_at_the_same_location`, and
  `test_lost_race_recovery_requires_the_same_account`.
- Credential isolation from shared state: `test_cloud_credentials.py` `_unchanged_by`,
  which asserts shared `cloud_storage` and `berth_cloud_allocation` rows are byte-identical
  across Manager credential operations.
  The added `test_hub_token_refresh_changes_only_the_allocated_accounts_token_and_expiry` exercises Hub refresh for Google Drive and Dropbox, asserting that only the allocated account's local token and expiry change.
- Cryptographic runtime progress and round-trip: the group crypto and peer transport tests
  in the Hub package, all passing in the full-package run above.

The new `test_cloud_registration.py` covers what moved rather than what stayed: the accepted
protocol set, rejection before either database changes, that registration makes no allocation
decision, and that secret material stays out of the shared account row.

### Boundary review

- No reference to `add_cloud_location` or `_add_cloud_location` remains outside this branch's
  own documents.
- `Nickname`, `Team`, `App`, and `TeamAppBerth` remain defined only in
  `small-sea-manager/provisioning.py`, which owns that schema. The Hub's mirrors are gone.
- The Hub package gained no ORM usage and no import. `server.py` already imported
  `small_sea_manager`, so no new package dependency was introduced and there is no cycle:
  the Manager declares no dependency on the Hub.
- `sqlalchemy.text` is imported and unused in Hub `backend.py`. It was already unused before
  this branch, so it is left in place and recorded in `follow-up.md`.

### Document judgment calls the plan reserved

`packages/small-sea-hub/README.md:10` is unchanged.
"Provide read-only access to Small Sea metadata to local clients" describes what the Hub
offers its API clients, which is still exactly right and is the boundary the decision
preserves.

`packages/small-sea-manager/spec.md:1036` is unchanged.
It says the *web layer* does not write registration or disposition state directly, in the
app-sightings section, and `web.py` still reaches cloud registration through
`TeamManager.add_cloud_storage`.
Moving account registration into Manager provisioning does not touch that claim.

Two items in `Documentation/open-architecture-questions.md` §2 were removed rather than
reworded, because the decision resolves them outright: removing `/cloud_locations` (whose
endpoint was already gone, and whose last remnant was the Python method this branch deleted)
and routing Hub `open_session` through a Manager-owned boundary.

## Independent implementation review (2026-09-05)

Reviewed `b449cd9` against `edc065e` and the agreed plan.
No functional regression found in the changed implementation or migrated callers.

The review identified one validation gap: the plan explicitly requires mocked Hub token refresh with before/after database assertions.
The credential witnesses cited above exercise Manager connect/replace/disconnect, not Hub token refresh persistence.
`test_oauth.py` checks the provider helper responses without exercising the Hub database write, and the GDrive cloud API witness uses an already-fresh token.
The requested correction was a micro test exercising Hub refresh and verifying only the intended account's local access token and expiry change, while other credential fields, another account, shared account rows, and allocations remain unchanged.
The original claim that existing suites covered every write exception was too strong and is corrected above.

Independent validation: 146 passed across the whole Hub package, Manager `test_cloud_registration.py`, and Manager `test_cloud_credentials.py`.
Command: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false .venv/bin/python -m pytest packages/small-sea-hub/tests packages/small-sea-manager/tests/test_cloud_registration.py packages/small-sea-manager/tests/test_cloud_credentials.py -q`.
The initial sandboxed attempt encountered denied MinIO port binding and GPG access for fixture commits; the successful run allowed local service startup and disabled fixture commit signing only through the command environment.
The full Manager suite and the reported baseline merge failures were not independently rerun during this review.

### Review finding addressed

Added `test_hub_token_refresh_changes_only_the_allocated_accounts_token_and_expiry` in Manager `test_cloud_credentials.py`, parameterized for Google Drive and Dropbox.
It provisions real shared and local databases, allocates a berth to the second account, and exercises the Hub's adapter construction with only the provider token-refresh helper mocked.
Assertions verify the correct refresh credentials and adapter token, the persisted access token and expiry, and unchanged remaining credentials, unrelated account, shared account rows, and allocation rows.
No production code changed.
Validation: 30 passed across Manager `test_cloud_credentials.py`, Manager `test_cloud_registration.py`, and Hub `test_oauth.py`.
Command: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false .venv/bin/python -m pytest packages/small-sea-manager/tests/test_cloud_credentials.py packages/small-sea-manager/tests/test_cloud_registration.py packages/small-sea-hub/tests/test_oauth.py -q`.
Provider refresh was mocked; the existing two-device credential witness used local MinIO.
`git diff --check` passed.
