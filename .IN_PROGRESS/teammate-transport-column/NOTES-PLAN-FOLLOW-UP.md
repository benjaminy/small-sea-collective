# Notes

Branch for issue #210: remove the second teammate transport authority and make the delivered Core route visible in Manager.

## The bug, restated from the code

After issue #183 delivers Bob's signed `teammate_berth_storage_announcement` to Alice, Alice can hold a valid route to Bob's Core storage while the Teammates table reports `Transport missing`.
The visible status comes from `_effective_transports_by_teammate` (`provisioning.py:3273`), which selects signed rows from `teammate_transport_announcement` without a berth coordinate.
Hub peer routing instead selects `teammate_berth_storage_announcement` for the session's exact `(teammate_id, berth_id)` (`backend.py:1688`, `backend.py:1765`).

The two tables are not alternate encodings of one fact.
The teammate-wide table cannot express the architecture's per-berth allocation model, where the same teammate may place different berths in different providers or accounts.
Current specs omit it from the team-state inventory, no Hub path consults it, and its remaining callers are the Manager teammate listing and its manual announcement form.

## Working decision

Treat `teammate_transport_announcement` as a research remnant, not live protocol state.
Remove its fresh-schema table, signing and selection types, Manager publication path, web endpoint, form, and tests that preserve it.
Bump the team DB's current pre-alpha schema marker and retain the established reset boundary instead of adding an in-place migration or compatibility fallback.

The Teammates table should show the newest valid `teammate_berth_storage_announcement` for the team's `SmallSeaCollectiveCore` berth.
Use the existing signature and current-trust selection rules rather than adding a UI-specific validity rule.
Name the column `Core route` and report `announced` or `missing`.
Do not call an announcement `available`, `reachable`, or `routable`: it records the teammate's selected locator but does not prove that the provider object is currently accessible.

The UI may display protocol, URL, and location because those values already live in the synced Core DB and are used for peer routing.
It must not expose credentials, probe the provider, or perform network I/O while rendering the team view.
Manager remains the only package that reads the team Core DB directly, and Hub remains the sole gateway for later provider I/O.

The team view is a developer debugging surface at this stage, not an end-user UX surface.
When the Core berth coordinate cannot be resolved, a loud failure is better than a degraded status.
That failure reaches the browser as an unhandled 500 from `team_detail` (`web.py:379`) and from the fragments and admission-events long poll that also call `get_team` (`web.py:144`, `web.py:515`, `web.py:734`).
The teams index does not call `get_team`, so it keeps working.
Accept that for now: no error-banner degradation, no new `try`/`except`, and no polling backoff.
Failure-mode UX for the team view is deferred until the view has real users.

Manual editing does not move to the berth-scoped column, and removing the form removes no peer-routing capability.
The form writes only `teammate_transport_announcement`, which no Hub path consults, so it can change the misleading Manager status without changing a route.
`create_team` announces the creator's route automatically when a cloud allocation is available (`provisioning.py:4952`), while `prepare_team_route` is narrower: it is wired to the web UI (`web.py:646`) and CLI (`cli.py:236`) for an invitee whose current join still has an eligible acceptance artifact.
Neither path is a general interface for a creator who adds storage later or an established teammate who changes providers.
That route-management gap is real but separate, so this branch removes the obsolete form and records a focused follow-up rather than replacing it with another generic locator form.

## Scope boundaries

Issue #207 separately removes vestigial `invitation.acceptor_*` columns from the old first-contact channel.
This branch should not absorb that cleanup merely because both fossils have the same origin.
It may assume #207 can land before or after this branch without supplying compatibility code between them.

This is a research-stage cleanup of an authority model that already fails with two teammates, so it belongs in the current change.
Production migration tooling, route health checks, stale-route repair, and richer per-berth route management do not answer issue #210 and remain out of scope.

Three adjacent defects were found while checking this plan against the code.
All are real, none blocks issue #210, and all are recorded under Follow-up as issues to file rather than work to absorb here.

# Plan

1. Replace the teammate-wide status projection with a Core-berth route projection.
   Reuse the existing `_core_berth_id` (`provisioning.py:939`) rather than writing a second Core berth resolver.
   Raise a named error when it returns `None`, and let its `AmbiguousCoreBerthError` propagate.
   Both conditions mean the team view cannot identify the coordinate it claims to report; neither is equivalent to every teammate lacking a valid announcement.
   Both raise out of `list_teammates` to its callers: a 500 from the web team view and a traceback from `cli.py:130`.
   That is the intended developer-facing behavior for this branch, not an oversight for step 3 to soften.
   Keep the batched shape of the code being replaced: load all announcements for the Core berth in one query, group them by teammate, and load certificates and device keys once per call.
   Resolve trust once via `resolve_trusted_device_keys_by_teammate`, then pass each teammate's announcements and `trusted_public_keys` into `select_effective_teammate_berth_storage` rather than calling `selected_teammate_berth_storage_announcement` in a loop.
   Return narrowly named `core_route_status` and `effective_core_route` fields to the Manager view rather than preserving transport-shaped compatibility keys.
   Project an announced route as exactly `protocol`, `url`, and `location`, mapping the selector's internal `TransportEndpoint.bucket` value to the view's `location` key and exposing no `bucket` alias.
   Verify with micro tests that a valid delivered Core announcement reports `announced`, a missing or invalid announcement reports `missing`, a non-Core announcement does not satisfy the status, and removal of the signer's current trust path makes the route inert.
   Exercise the listing projection itself for both missing and ambiguous Core-berth failures, asserting that `list_teammates` raises rather than returning rows, so neither can silently become per-teammate `missing` status.

2. Preserve selector coverage before deleting the teammate-wide selector.
   `packages/wrasse-trust/tests/test_transport.py` currently proves three properties through `select_effective_teammate_transport`, and only one of them has a berth-scoped twin.
   Port `..._binds_signer_key_id_in_signature` and `..._rejects_other_teammates_signer` to `select_effective_teammate_berth_storage` first, and confirm they fail against a deliberately broken selector before relying on them.
   Without this the branch weakens the one selector it is promoting to sole routing authority, which is exactly what step 6 promises a reviewer it does not do.

3. Make the team detail UI report the actual Core route.
   Rename the column to `Core route`, show the selected protocol, URL, and location, and remove the self-only generic transport announcement form, its POST route, and the `needs_transport_announcement` field that gates it.
   Put nothing in the form's place, so the column becomes a pure status readout.
   A `not yet implemented` stub label there would read as a claim about this row's route, but a self row can report `missing` simply because this participant configured no cloud storage.
   The absent self-service route flow is tracked as a Follow-up issue instead.
   Extend the issue #183 invitation route-delivery scenario through Manager rendering after a finalized completion.
   Anchor the assertion to the invitee's row and exact couriered location, not a page-wide `announced` string that the creator's own automatically published route can already satisfy.
   Keep this assertion local-only with existing test clients, fixtures, and local storage or MinIO; rendering the view itself must not contact a provider.

4. Delete the second announcement protocol completely.
   Remove `teammate_transport_announcement` from the fresh team schema.
   Remove `TeammateTransportAnnouncement`, its canonicalization, its signature verification, `select_effective_teammate_transport`, exports, and the tests left dedicated to it after step 2.
   Keep `TransportEndpoint` and `EffectiveTransportSelection`: both are shared with the berth-scoped selector and with Hub.
   Remove Manager's `_load_teammate_transport_announcements`, `_effective_transports_by_teammate`, `announce_teammate_transport`, its facade method, and every remaining reference found by repository-wide search.
   Preserve `TeammateBerthStorageAnnouncement` and its selector unchanged, including its deliberate selection of the newest valid row rather than blindly accepting or rejecting the newest row.
   Do not rename `TransportEndpoint.bucket` to `location` in this branch even though the berth path now solely populates it from `announcement.location`; that name is load-bearing in Hub routing (`backend.py:1618`, `backend.py:1811`) and in the publish dedupe check (`provisioning.py:3462`), and the rename is recorded under Follow-up.

5. Mark the intentional pre-alpha schema break and align documentation.
   Increment `USER_SCHEMA_VERSION` (`provisioning.py:2107`), which despite its name is the marker `_init_team_db` stamps and `ensure_team_db_schema` checks; NoteToSelf carries its own separate versions in `small_sea_note_to_self/db.py`.
   Do not add an in-place migration: older research workspaces should receive the existing explicit delete-and-recreate error from `_migrate_team_db`.
   The spec edited here is `packages/small-sea-manager/spec.md`; every line number below refers to that file.
   Correct `spec.md:1504`, which states that transport metadata is not part of the team schema; berth storage announcements are in that schema, and the sentence predates them.
   Correct `spec.md:1110`, whose statement that berth selection mirrors a current transport-announcement rule will become false when that rule is deleted; state the berth-scoped newest-valid rule directly.
   Correct `spec.md:1084`, which says the berth table replaces teammate-scoped transport announcements; once they are deleted it should state the berth-scoped table as the only storage-routing authority rather than name what it superseded.
   B7 names the intended general post-admission transport-configuration flow, not the removed teammate-wide table and not the invitation-only `prepare_team_route` implementation.
   Reword `spec.md:741`, `spec.md:1271`, `spec.md:1504`, and `Documentation/open-architecture-questions.md:219` so they describe berth-scoped announcements, distinguish the implemented issue #183 first-contact sidecar from the still-unimplemented general B7 flow, and do not imply that established teammates can already change providers through Manager.
   `spec.md:868` describes trust-removal semantics without asserting teammate-wide scope; confirm that when editing, but otherwise leave it alone.
   `spec.md:871` already documents the berth-scoped `announced`/`missing` reading this branch implements, so it needs no edit: there the code, not the spec, is what is out of date.
   Do not broaden the edit into unrelated schema cleanup from issue #207.

6. Convince a skeptical reviewer that the duplicate authority is gone and the real route is visible.
   Run the focused `wrasse-trust` and Manager micro tests covering announcement selection, teammate listing, web rendering, and invitation route delivery.
   Run the Hub peer-routing micro tests to prove routing still accepts only valid berth-scoped announcements and has no teammate-wide or `team_device` fallback.
   Preserve the legitimate newest-valid behavior in which an invalid or untrusted newer berth-scoped row does not hide an older valid berth-scoped row.
   Run the schema/version micro tests to prove fresh DBs have the new shape and older versions still fail through the stated pre-alpha reset boundary.
   State that boundary precisely: `ensure_team_db_schema` raises for `0 < user_version < USER_SCHEMA_VERSION`, and `user_version == 0` is a pre-existing gap recorded under Follow-up, not a claim this branch should make.
   Search the repository for `teammate_transport_announcement`, `TeammateTransportAnnouncement`, `teammate-transport`, `transport-announcement`, `announce_teammate_transport`, `transport_status`, `effective_transport`, `needs_transport_announcement`, `select_effective_teammate_transport`, `B7`, the spaced prose form `transport announcement`, and the old `/transport` route; any survivor must be justified as historical prose or removed.
   Run the complete affected package test suites if the focused tests pass and the local environment provides their dependencies.
   Review the final diff for changes not traceable to issue #210, especially opportunistic invitation-schema cleanup, migration machinery, provider probes, a general route-management implementation, new cross-authority routing fallbacks, or the deferred `bucket` rename.

# Follow-up

Three issues to file, all found while validating this plan and all deliberately out of scope.

- `ensure_team_db_schema` silently accepts `user_version == 0`.
   A team DB reporting version 0 takes no branch: no migration error, no reset, no initialization (`provisioning.py:2526`).
   The pre-alpha reset boundary therefore holds only for versions strictly between 0 and the current marker.
   The same issue should note that `USER_SCHEMA_VERSION` is misnamed for what it now versions, and that `_migrate_user_db` (`provisioning.py:2497`) has no callers.

- `TransportEndpoint.bucket` carries a berth `location`.
   Once the teammate-wide announcement is gone the field is populated only from `TeammateBerthStorageAnnouncement.location` (`transport.py:227`), so the name misdescribes every remaining value.
   Renaming it spans wrasse-trust, Hub routing, Manager, and templates.

- General Core-route repair and provider changes have no Manager UI or CLI flow.
   `prepare_team_route` performs no provider I/O or publication without an eligible acceptance artifact (`manager.py:718`, `manager.py:727`), so it serves first-contact invitation completion rather than established teammates.
   `create_team` covers only the creator's initial route when storage is already available.
   A creator who adds storage later and an established teammate who changes providers therefore have no self-service path that materializes the final locator and publishes its berth-scoped announcement.
   The follow-up should design that path through Manager and Hub without restoring a generic caller-supplied locator form.

Before wrap-up, re-evaluate whether implementation exposed any other concrete route-repair or route-management gap that is not already tracked.
Do not file deployment-hardening work merely because a production implementation could be more elaborate.
