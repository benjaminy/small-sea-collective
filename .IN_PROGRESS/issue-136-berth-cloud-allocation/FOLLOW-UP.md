# Follow-Up

## Cloud storage error class unification

The Hub now has `small_sea_hub.cloud_errors.CloudLocationMissingExn` and the Manager has `small_sea_manager.provisioning.CloudLocationMissingError`.
They intentionally live on opposite sides of the Manager/Hub boundary in this branch, but they share the same stable reason string.
When the cloud-storage-required family settles, decide whether to keep these as boundary-local exception types or move the reason constants / base shape into a shared package.
