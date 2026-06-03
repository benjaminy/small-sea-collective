# Issue 138 Plan: Remove Legacy `team_device` Transport Fallback

GitHub issue: https://github.com/benjaminy/small-sea-collective/issues/138

Branch: `codex-issue-138-remove-team-device-transport-fallback`

## Goal

Stop using `team_device(protocol, url, bucket)` as peer storage routing data.
After this branch, peer-readable storage for a berth should be discovered through signed `member_berth_storage_announcement` rows scoped to `(member_id, berth_id)`.
`team_device` should remain about device identity and any still-valid device-key responsibilities, not cloud storage placement.

## Current Understanding

Issue #134 settled the Manager-owned berth cloud-location model.
Issues #136 and #137 implemented berth allocation/materialization and member-berth storage announcements.
Issues #144 and #145 hardened and cleaned up test support around that announcement path.

The remaining transitional path appears to live in three places:

- `small_sea_hub.backend` can load `team_device(protocol, url, bucket)` through `_legacy_transport_for_member`.
- `wrasse_trust.transport` lets both member transport and member-berth storage selection return status `legacy-fallback`.
- `small_sea_manager.provisioning` still has helpers and admission/bootstrap flows that can write or read transport fields on `team_device`.

Specs also still describe legacy fallback as temporary.
Those docs need to be brought back into agreement with the code.

## Non-Goals

- Do not redesign Manager cloud-location provisioning.
  That belongs to #139 and nearby follow-up work.
- Do not add compatibility shims for old pre-alpha databases unless a specific local test fixture requires a narrow cleanup.
- Do not remove team-device identity rows, team-device keys, or certificate trust checks.
  Only remove storage-routing semantics from `team_device`.
- Do not implement orphaned provider-location cleanup.
  That belongs to #140.

## Implementation Plan

1. Map the fallback surface.

   Search for `legacy-fallback`, `member_transport_announcement`, `team_device(protocol`, direct `team_device` selects of `protocol`, `url`, or `bucket`, and `_upsert_team_device_row` calls that pass cloud fields.
   Classify each occurrence as one of:

   - peer storage resolution behavior to remove,
   - admission/bootstrap writing behavior to stop,
   - schema/documentation cleanup,
   - test setup that should be rewritten around member-berth storage announcements,
   - unrelated identity use that should stay.

2. Remove fallback from Hub peer cloud-file resolution.

   Update berth-scoped peer resolution so `member_berth_storage_announcement` is the only accepted peer storage source.
   Delete or retire `_legacy_transport_for_member` if no non-berth call path still requires it.
   Make missing or invalid announcements produce the existing missing-storage behavior rather than silently routing through `team_device`.

3. Remove fallback from shared transport selection helpers.

   Remove the `legacy_fallback` parameter and `legacy-fallback` status from `select_effective_member_berth_storage`.
   Decide whether `select_effective_member_transport` and `MemberTransportAnnouncement` still serve a real non-berth transport role.
   If no current production path uses member-level transport announcements for valid non-storage behavior, remove that fallback there too.

4. Stop writing transport fields onto `team_device`.

   Simplify `_upsert_team_device_row` so callers cannot accidentally store `protocol`, `url`, or `bucket`.
   Remove cloud-storage lookups in device-link or bootstrap flows whose only purpose is to populate those fields.
   Verify admission and bootstrap flows publish or preserve member-berth storage announcements through the replacement path.

5. Reconcile schema and migrations.

   Because the project is pre-alpha, prefer the clean schema if tests and current DB initialization allow it.
   Remove `protocol`, `url`, and `bucket` from new `team_device` table definitions if they are no longer used.
   If migration cleanup would create noisy or risky churn, leave old columns tolerated but prove no code reads or writes them for routing.
   Record the final choice in the design record.

6. Update tests and docs.

   Rewrite tests that expected `team_device` fallback routing so they expect missing storage unless a valid member-berth storage announcement exists.
   Update admission/bootstrap tests to prove peers can still discover storage after the replacement announcement flow.
   Update `packages/small-sea-hub/spec.md`, `packages/small-sea-manager/spec.md`, and any related prose so there is no documented storage-routing authority on `team_device`.

7. Revisit #123.

   Issue #123 asks whether `member_transport_announcement.bucket` should be authoritative for S3 berth routing.
   This branch should either close it as superseded by berth-scoped storage announcements or rewrite it into any remaining non-berth transport question.
   Add that recommendation to `FOLLOW-UP.md` if the GitHub issue itself is not updated during the branch.

## Validation Plan

The skeptical-reviewer standard for this branch is that tests fail if legacy `team_device` storage fields can still affect peer storage routing.

### Targeted Micro Tests

- Add or update a Hub peer cloud-file micro test that inserts only `team_device(protocol, url, bucket)` for a peer and no valid `member_berth_storage_announcement`.
  The test should prove the Hub refuses or reports missing peer storage instead of fetching from the legacy endpoint.
- Add or update a Hub peer cloud-file micro test with both stale `team_device` fields and a valid member-berth storage announcement.
  The test should prove the announcement wins because it is the only routing source, not merely because it has precedence.
- Add or update a Manager provisioning micro test proving admission/bootstrap no longer writes `protocol`, `url`, or `bucket` to new `team_device` rows.
- Add or update an admission/bootstrap integration micro test proving a newly accepted peer can still discover readable berth storage when the proper member-berth storage announcement is present.
- Add or update a `wrasse_trust` micro test proving `select_effective_member_berth_storage` returns `missing` when no valid berth-scoped announcement exists, even if a caller tries to supply old fallback-shaped data.

### Regression Test Runs

Run the focused tests first:

```sh
uv run pytest packages/wrasse-trust/tests/test_transport.py
uv run pytest packages/small-sea-hub/tests/test_peer_transport.py
uv run pytest packages/small-sea-hub/tests/test_cloud_api.py
uv run pytest packages/small-sea-manager/tests/test_member_transport.py
uv run pytest packages/small-sea-manager/tests/test_invitation.py
uv run pytest packages/small-sea-manager/tests/test_hub_invitation_flow.py
```

Then run broader package tests if the focused suite passes:

```sh
uv run pytest packages/wrasse-trust/tests
uv run pytest packages/small-sea-hub/tests
uv run pytest packages/small-sea-manager/tests
uv run pytest packages/shared-file-vault/tests
```

### Static Checks

- `rg -n "legacy-fallback|_legacy_transport|team_device\\(protocol|protocol, url, bucket|SELECT .*protocol.*url.*bucket.*FROM team_device" packages`
  should show no production storage-routing dependency.
- `rg -n "team_device" packages/small-sea-manager/spec.md packages/small-sea-hub/spec.md`
  should show identity-focused language only, with no storage-routing fallback claim.
- Any remaining `member_transport_announcement` references should be justified as a current non-berth transport concept or removed.

### Integrity Argument

This change should reduce coupling between identity and storage placement.
The Hub remains the gateway for all Small Sea internet traffic, but it no longer invents or recovers peer storage routing from identity rows.
The Manager remains the owner of berth cloud-location provisioning and announcement publishing.
Other packages consume berth-scoped announcements rather than reaching into Manager-only storage state for routing.

## Risks And Watch Points

- Some tests may still be using `team_device` fields as convenient setup.
  Prefer rewriting them through allocation plus `publish_member_berth_storage_announcement`, not preserving the fallback.
- `member_transport_announcement` may have become a mostly obsolete transitional table.
  Remove only what the branch can prove is dead; record any remaining uncertainty in `FOLLOW-UP.md`.
- Schema cleanup can snowball.
  Keep the branch focused on behavior first, then schema/docs once behavior is proven.
- Peer routing errors should stay legible.
  Removing fallback should not turn missing storage into a vague internal failure.

## Completion Artifacts

At wrap-up, create:

- `.IN_PROGRESS/issue-138-remove-team-device-transport-fallback/FOLLOW-UP.md` if #123 or related cleanup cannot be fully resolved in this branch.
- `.IN_PROGRESS/issue-138-remove-team-device-transport-fallback/design-record-issue-138-remove-team-device-transport-fallback.md`
- `.IN_PROGRESS/issue-138-remove-team-device-transport-fallback/review-note.md`
