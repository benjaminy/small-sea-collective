# Issue 181: Hub access to Manager state

## Agreed decision

The Hub may read NoteToSelf and team Core databases directly for its framework responsibilities.
Manager makes management changes by default.
Hub writes outside its own local database are exceptions that require a specific execution responsibility and a concrete reason why routing the change through Manager would be awkward.
Ordinary applications still obtain identity and session information through the Hub API and do not open Core databases.
This grants no Hub access to arbitrary application databases.

Manager owns account registration, credential connect/replace/disconnect, storage allocation decisions, app registration and activation, and membership decisions.
The Hub executes provider operations and enforces local client authorization.
No new Manager service, IPC protocol, replicated projection, or compatibility layer is needed.
Direct reads are a deliberate architecture choice, not a temporary mandate violation.

Do not expand ORM usage.
Repository-wide ORM removal is deferred to a separate issue.
Existing ORM usage may remain where it does not prevent satisfying this branch's contract.

## Storage ownership and write exceptions

Keep one authoritative schema implementation per database.
`small-sea-note-to-self` already owns the shared and device-local NoteToSelf schema SQL and version markers.
Manager provisioning owns the team Core schema implementation.
Hub-local schema remains Hub-owned.
These storage implementation owners are distinct from authority to make management decisions.
Hub readers consume the repository's current schema; this branch does not introduce independent component-version compatibility support.
Preserve schema/version markers and existing version checks.

The initial exception list is:

| Storage | Permitted Hub change | Reason and limit |
| --- | --- | --- |
| NoteToSelf `core.db`, `berth_cloud_allocation.location` | Persist a provider-issued locator for an existing allocation | The Hub receives the result of materialization; it must not choose or replace the allocation, and existing conditional writeback must reject a stale allocation result. |
| NoteToSelf `device_local.db`, cloud credentials | Persist refreshed access token and expiry | Refresh happens during Hub provider I/O; this does not authorize account creation or credential connect/replace/disconnect. |
| NoteToSelf `device_local.db`, cryptographic runtime state | Persist sender and receiver key material, key rotation, runtime reconciliation state, and redistribution delivery and receipt marks | The Hub performs the operation that advances the state; this does not transfer membership, admission, or key-distribution policy to the Hub. Broadened from "sender/receiver key records during encryption/decryption" after the audit found the same kind of write reaching `device_local.db` through Manager helpers the Hub imports. |

Hub-owned sessions, sightings, and other Hub-local runtime records are ordinary Hub responsibilities, not exceptions to Manager ownership of Core state.
Audit indirect writes through imported helpers as well as SQL in the Hub package before treating this list as complete.
Do not grandfather another management write merely because it currently exists.
Bring a materially different proposed exception back for discussion.

## Implementation

1. Inventory Hub reads and writes by database, operation, and helper call path.
   Record the compact inventory and any scope discoveries in `notes.md` when implementation begins.
   Include `backend.py`, `server.py`, `crypto.py`, and shared persistence helpers.
   Distinguish provider I/O from management actions and distinguish shared Core from device-local state.
2. Remove Hub-owned cloud-account registration (`add_cloud_location` / `_add_cloud_location`).
   Move callers and test setup to the existing Manager provisioning operation rather than retaining a forwarding compatibility method.
   This is a signature change: `provisioning.add_cloud_storage` requires `root_dir` and `participant_hex` instead of a Hub session token; retain participant IDs from fixture provisioning where possible and use the correct installation root and participant in multi-device witnesses.
   It is only a signature change: the Manager operation is a strict superset of the Hub one, writing the same two rows, so a call site migrates as a parameter change and does not split into a registration call plus a separate credential-connect call.
   The #237 witnesses are the deliberate exception, because separating those two operations is what they exist to demonstrate.
   The caller inventory is in `notes.md`: 29 call sites in 15 test files across five packages, with none in `devtools/` or root `tests/`.
   `packages/ssc-files` is in scope and was not anticipated when this plan was first written.
   Include the two-device credential witnesses from #237 in `test_cloud_credentials.py`.
   Preserve each witness's actual purpose: Manager registration on one device, independent credential connection on another, and successful Hub execution.
   Move unknown-protocol validation into Manager account registration, using a Manager-appropriate exception before any database mutation.
   Check existing Manager callers before adopting the Hub's `s3`, `webdav`, `gdrive`, and `dropbox` allowlist, since this also tightens the existing Manager API; resolve any additional supported protocols explicitly.
   Keep Hub token refresh and locator writeback within the documented exceptions.
3. Remove the duplicated Core ORM mappings in Hub `backend.py`.
   Replace the live `Nickname` lookup in `_find_participant` with a small parameterized SQL read, preserving lookup behavior.
   Remove `Nickname` and the unused `Team`, `App`, and `TeamAppBerth` definitions; these mirrored models are directly within this issue's scope.
   `_resolve_berth` already uses explicit SQL for the different NoteToSelf and team berth schemas; retain validation of both against real initialized databases.
   Do not undertake general ORM cleanup or add schema-contract machinery for the removed mappings.
4. Reconcile `AGENTS.md`, `architecture.md`, and `Documentation/open-architecture-questions.md` §2.
   Replace contradictory exclusivity and never-writes claims with the default and enumerated exceptions.
   The known contradiction outside those three is `packages/small-sea-hub/spec.md:107`, "The Hub never writes to Manager's databases; it only reads them".
   Two further sites need a judgment call rather than an edit by default.
   `packages/small-sea-hub/README.md:10` describes read-only access to Small Sea metadata; if that is the client-facing API boundary it is still correct and should stay.
   `packages/small-sea-manager/spec.md:1036` says provisioning does not write registration state directly; confirm whether the new Manager account registration changes that.
   Update both the opening rationale and the settled decisions in the open architecture questions' database-contract section.
   Explain physical schema ownership and retain the ordinary-app API boundary.
   Check references to Manager-owned Core access, including Constitution basis and bootstrap descriptions, for consistency without redesigning those features.
5. Complete validation, then prepare the GitHub changes in `follow-up.md`.
   Update this plan's status briefly and draft `final-commit-message.md` at implementation completion.

Retain SQLite writer reservations, transactional row adoption, and existing conditional locator updates.
The Hub remains a co-writer, so removing its registration path does not justify file replacement or weaker publication coordination.
Do not add rollout or migration machinery for research artifacts.

## Validation: evidence for a skeptical reviewer

Use local temporary databases created by the real NoteToSelf initializers and Manager provisioning.
Use localfolder, mocked provider clients, or local MinIO; do not contact real providers.

- Exercise Hub participant lookup and session information for NoteToSelf and an ordinary team after Manager provisioning, without a running Manager service.
  Verify stable participant/team/app/berth IDs and existing rejection behavior for ambiguous names and unprovisioned berths.
  A successful lookup alone is insufficient: ensure an ordinary app still receives only its authorized session information.
- Demonstrate that Manager account registration and credential operations produce state the Hub can use.
  Verify registration does not silently create allocation decisions, and device-local credentials do not enter shared Core publication.
  Existing callers must no longer require Hub account registration.
  Add a Manager registration micro test showing that an unknown protocol is rejected without changing either shared account rows or device-local credential rows, and verify accepted protocols match the explicit registration contract.
- Exercise each write exception with before/after database assertions.
  Locator materialization must update only the intended existing allocation, and a replaced allocation must reject the stale result.
  Mocked token refresh must update the intended local credential fields without changing shared account or allocation state.
  Existing encryption/decryption witnesses must still persist runtime progress and round-trip payloads.
- Run the relevant existing Hub session/bootstrap, cloud API, OAuth, group crypto, and peer transport micro tests; Manager cloud credentials, NoteToSelf refresh/integration, and Core publication micro tests; and affected cross-package caller tests.
  Start with focused files and run the remaining affected suites once the edits pass.
  Record exact commands, results, and unavailable local prerequisites in `notes.md`; do not claim unrun checks passed.
- Review all remaining Hub persistence paths against the inventory and exceptions.
  Search for removed registration methods, the four removed Core ORM mappings, and contradictory mandate wording, then inspect matches rather than relying on a string search as proof of the architectural boundary.
  Review dependencies to confirm the change adds no service or circular package dependency and no new ORM usage.

Pre-existing concurrency or partial-failure problems discovered during the audit belong in focused follow-up issues unless the branch needs to fix them to preserve developer data, credentials, or research integrity.
In particular, retaining a Hub exception is not a claim that its existing concurrency handling has been verified.

## Status

Implemented.
The review's token-refresh validation gap is addressed with a mocked Hub persistence micro test for both OAuth providers.
All five steps are complete; see `notes.md` for the inventory, the two decisions taken with the user, and the validation commands and results.
Two exception-table judgments went back to the user rather than being grandfathered: the device-local crypto writes reaching `device_local.db` through imported Manager helpers broadened exception 3, and `mark_admission_event_notified` is classified as an ordinary Hub-local runtime record.
The protocol allowlist resolved to the Hub's four plus `localfolder`, which existing Manager callers depend on.
`final-commit-message.md` is drafted and the commit is left to a human.
