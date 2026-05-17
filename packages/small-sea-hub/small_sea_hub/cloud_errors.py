from dataclasses import dataclass


@dataclass(frozen=True)
class MaterializationOutcome:
    status: str
    final_location: str | None = None


class CloudStorageRequiredExn(Exception):
    reason = "cloud_storage_required"

    def __init__(self, message: str | None = None):
        super().__init__(message or self.reason)


class CloudLocationMissingExn(CloudStorageRequiredExn):
    reason = "cloud_location_missing"


class CloudAnnouncementMissingExn(CloudStorageRequiredExn):
    reason = "announcement_missing"


class CloudCredentialsMissingExn(CloudStorageRequiredExn):
    reason = "cloud_credentials_missing"


class CloudMaterializationFailedExn(CloudStorageRequiredExn):
    reason = "cloud_materialization_failed"


class CloudUserActionRequiredExn(CloudStorageRequiredExn):
    reason = "cloud_user_action_required"


class CloudAllocationConflictExn(CloudStorageRequiredExn):
    reason = "cloud_allocation_conflict"
