# Review note: Slice A — Berth Cloud Allocation and Hub Materialization

This branch implements Slice A of #134.
It replaces the Hub's `ss-{berth_id[:16]}` synthesis in the own-storage path
with explicit `berth_cloud_allocation` lookup, adds Manager helpers and
auto-allocation, and surfaces the materialization outcome family on
`POST /cloud/setup` and own-storage operations.

Peer-read routing (`member_berth_storage_announcement`) is **Slice B** and is
intentionally not in this branch.
`team_device(protocol, url, bucket)` column removal is **Slice C**.

## Where to look first

- `PLAN.md` — full plan with Branch Contract, Bootstrap Decision, and the
  Validation checklist a skeptical reviewer can run.
- `design-record-issue-136-berth-cloud-allocation.md` — boiled-down record of
  the non-obvious choices (adapter-owned materialization, adapter rebuild
  after locator writeback, descriptor sourcing from the allocation join,
  invitee no-inheritance, the two acceptable Slice border-crossings).
- `FOLLOW-UP.md` — the one unresolved item (Hub/Manager exception-class
  unification, deferred until the cloud-storage-required family settles).
- `packages/small-sea-hub/tests/test_cloud_api.py` — micro tests covering
  every reason in the `cloud_storage_required` family, including the
  `materialized_with_locator` rebuild path and the conditional-writeback
  race.

## Things easy to miss

- The Core peer-read path in `_download_peer_file` is updated to use
  `legacy_transport.bucket` (allocation-sourced via `team_device`) rather
  than the formula.
  Other app peer reads still use the legacy formula — Slice B will route
  them through storage announcements.
- The one remaining `_bucket_name_for_protocol` call site
  (`finalize_linked_device_bootstrap`) is an acknowledged Slice C remainder,
  not an oversight.
- PLAN.md's line-number references (`backend.py:1462`,
  `provisioning.py:4132`) have drifted as the files grew; the design record
  names the functions instead.

## Validation status at hand-off

- `uv run pytest packages/small-sea-hub/tests packages/small-sea-manager/tests
  packages/shared-file-vault/tests` — green (261 passed, 3 pre-existing
  skips).
- Hub spec is already aligned with the implementation.
  Manager spec was updated in this branch to describe Core auto-allocation
  at team creation and invitation acceptance.
