# Follow-Up

## Implementation Slices

1. Own-storage allocation.
   Add `berth_cloud_allocation` schema and migrations, Manager provisioning helpers, Hub own-storage allocation lookup, structured missing-location and missing-credential errors, and idempotent materialization for `/cloud/setup` and first-use storage operations.

2. Member-berth storage announcements.
   Add signed `member_berth_storage_announcement` types, canonical bytes, newest-valid selection for `(member_id, berth_id)`, and peer reads that use valid announcements before legacy fallback.

3. Legacy cleanup.
   Stop writing transport fields onto `team_device`, remove legacy transport fallback, and revisit #123 and #102 under the new member-berth storage model.

4. Manager UX.
   Let humans choose which cloud account backs a berth, generate provider-facing locations, surface missing-location and missing-credential repair actions, and call materialization as a validation step where useful.

5. Provider cleanup.
   Design cleanup for orphaned provider locations created by cross-device first-use races.

## Related Issues

- #123 should remain blocked until member-berth storage announcements exist or the issue is rewritten.
- #114 is subsumed by the `berth_cloud_allocation` slice.
- #10 should build the Manager UI on top of explicit berth allocations rather than account-only storage configuration.
- #9 should use the new allocation and announcement vocabulary when cleaning up storage adapters.
- #16 should use the new cloud-storage error family and Manager-triggered Hub operation authorization shape.
