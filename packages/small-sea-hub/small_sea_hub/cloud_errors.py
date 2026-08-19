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


#: A download that reached the provider and got a definite "no such object".
DOWNLOAD_ABSENT = "absent"

#: A download that failed for any other reason. The object's existence is
#: unknown, so nothing above may read this as "not there".
DOWNLOAD_PROVIDER_FAILURE = "provider_failure"

#: A conditional upload definitely lost its compare-and-swap race.
UPLOAD_CAS_CONFLICT = "cas_conflict"


@dataclass(frozen=True)
class CloudDownloadFailure:
    """Why a download failed, as the third element of a (ok, data, _) result.

    Cod Sync's emptiness and missing-predecessor logic rests on telling an
    absent object from a failed read, and only the adapter that made the
    request knows which one happened. Carrying the distinction in a value keeps
    the endpoint from guessing it back out of an error string.
    """

    kind: str
    detail: str

    @property
    def absent(self) -> bool:
        return self.kind == DOWNLOAD_ABSENT

    def __str__(self) -> str:
        return self.detail


def absent(detail: str) -> CloudDownloadFailure:
    return CloudDownloadFailure(DOWNLOAD_ABSENT, detail)


def provider_failure(detail: str) -> CloudDownloadFailure:
    return CloudDownloadFailure(DOWNLOAD_PROVIDER_FAILURE, detail)


@dataclass(frozen=True)
class CloudUploadFailure:
    """A structured failure from a provider upload."""

    kind: str
    detail: str

    @property
    def cas_conflict(self) -> bool:
        return self.kind == UPLOAD_CAS_CONFLICT

    def __str__(self) -> str:
        return self.detail


def cas_conflict(detail: str) -> CloudUploadFailure:
    return CloudUploadFailure(UPLOAD_CAS_CONFLICT, detail)
