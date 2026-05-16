# Follow-Up

## Implementation Slices

- Slice A: Own-storage allocation (#136).
  Add `berth_cloud_allocation` schema and migrations, Manager provisioning helpers, Hub own-storage allocation lookup, structured missing-location and missing-credential errors, and idempotent materialization for `/cloud/setup` and first-use storage operations.

- Slice B: Member-berth storage announcements (#137).
  Add signed `member_berth_storage_announcement` types, canonical bytes, newest-valid selection for `(member_id, berth_id)`, and peer reads that use valid announcements before legacy fallback.

- Slice C: Legacy cleanup (#138).
  Stop writing transport fields onto `team_device`, remove legacy transport fallback, and revisit #123 and #102 under the new member-berth storage model.

- Slice D: Manager UX (#139).
  Let humans choose which cloud account backs a berth, generate provider-facing locations, surface missing-location and missing-credential repair actions, and call materialization as a validation step where useful.

- Slice E: Provider cleanup (#140).
  Design cleanup for orphaned provider locations created by cross-device first-use races.

## Related Issues

- #123 should remain blocked until member-berth storage announcements exist or the issue is rewritten.
- #114 is subsumed by the `berth_cloud_allocation` slice.
- #10 should build the Manager UI on top of explicit berth allocations rather than account-only storage configuration.
- #9 should use the new allocation and announcement vocabulary when cleaning up storage adapters.
- #16 should use the new cloud-storage error family and Manager-triggered Hub operation authorization shape.
