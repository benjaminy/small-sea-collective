import json
from typing import Optional

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from .base import SmallSeaStorageAdapter
from small_sea_hub.cloud_errors import (
    MaterializationOutcome,
    CloudValidatorMissingExn,
    absent,
    cas_conflict,
    never_applied,
    outcome_unknown,
    provider_failure,
)


class SmallSeaS3Adapter(SmallSeaStorageAdapter):
    def __init__(self, s3, bucket_name):
        super().__init__(bucket_name)
        self.s3 = s3

    def ensure_bucket_public(self):
        """Create the bucket if absent and apply a public-read policy."""
        try:
            self.s3.create_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                raise

        policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "PublicRead",
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{self.bucket_name}/*"],
            }]
        })
        self.s3.put_bucket_policy(Bucket=self.bucket_name, Policy=policy)

    def materialize(self) -> MaterializationOutcome:
        try:
            self.ensure_bucket_public()
        except ClientError:
            return MaterializationOutcome("failed", self.bucket_name)
        return MaterializationOutcome("materialized", self.bucket_name)

    #: Codes S3 uses for "this exact key is not there".
    ABSENT_CODES = ("NoSuchKey", "404", "NotFound")

    def download(self, path: str):
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=path)
            etag_header = response.get("ETag")
            etag = etag_header.strip('"') if etag_header else None
            if not etag:
                raise CloudValidatorMissingExn("S3 returned no ETag")
            return True, response["Body"].read(), etag
        except ClientError as exn:
            error_code = exn.response["Error"]["Code"]
            detail = f"Download failed: {error_code}"
            if error_code in self.ABSENT_CODES:
                return False, None, absent(detail)
            return False, None, provider_failure(detail)

    def _upload(
        self,
        path: str,
        data: bytes,
        expected_etag: Optional[str],
        content_type: str = "application/octet-stream",
    ):
        # Only conditional writes are compare-and-swap; classify their outcome.
        classify_head_outcome = expected_etag is not None
        try:
            if expected_etag is None:
                response = self.s3.put_object(
                    Bucket=self.bucket_name,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                )
            elif "*" == expected_etag:
                response = self.s3.put_object(
                    Bucket=self.bucket_name,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                    IfNoneMatch=expected_etag,
                )
            else:
                response = self.s3.put_object(
                    Bucket=self.bucket_name,
                    Key=path,
                    Body=data,
                    ContentType=content_type,
                    IfMatch=expected_etag,
                )
            etag_header = response.get("ETag")
            new_etag = etag_header.strip('"') if etag_header else None
            if not new_etag:
                raise CloudValidatorMissingExn("S3 returned no ETag")
            return True, new_etag, "Object updated successfully"
        except ClientError as exn:
            error_code = exn.response["Error"]["Code"]
            status = exn.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if (
                error_code in ("PreconditionFailed", "ConditionalRequestConflict")
                or status in (409, 412)
            ):
                return False, None, cas_conflict(
                    "Object already exists"
                    if expected_etag == "*"
                    else "ETag mismatch - object was modified"
                )
            if not classify_head_outcome:
                return False, None, f"Operation failed: {exn}"
            if status is not None and 400 <= status < 500:
                return False, None, never_applied(f"Conditional upload rejected (HTTP {status})")
            return False, None, outcome_unknown(f"Conditional upload failed (HTTP {status})")
        except (
            EndpointConnectionError,
            ConnectTimeoutError,
            NoCredentialsError,
            PartialCredentialsError,
        ) as exn:
            if classify_head_outcome:
                return False, None, never_applied(
                    f"Conditional upload could not be sent: {type(exn).__name__}"
                )
            return False, None, f"Operation failed: {exn}"
        except (ReadTimeoutError, ConnectionClosedError) as exn:
            if classify_head_outcome:
                return False, None, outcome_unknown(
                    f"Conditional upload response was lost: {type(exn).__name__}"
                )
            return False, None, f"Operation failed: {exn}"
        except Exception as exn:
            if classify_head_outcome:
                return False, None, outcome_unknown(
                    f"Conditional upload failed: {type(exn).__name__}"
                )
            raise
