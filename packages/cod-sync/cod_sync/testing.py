"""Test-only stores that talk to S3 directly. Not for production use.

Production Small Sea traffic goes through the Hub; these exist so tests can
stand up a store without one, and so an acceptor's view of a public bucket can
be modeled without credentials.

S3Store: authenticated S3/MinIO access, used where a test needs to write.
PublicS3Store: anonymous read-only access to a publicly-readable bucket, which
is how the inviter's bucket looks to an acceptor. Content privacy comes from
end-to-end encryption, not bucket ACLs.

Both obey the same create-only and compare-and-swap contract as the Hub-backed
stores. A testing store that accepted an etag without enforcing it would make
the tests that use it prove nothing.
"""

from typing import Optional, Tuple

from cod_sync.store import (
    CREATE_ONLY,
    LATEST_LINK_PATH,
    CasConflictError,
    ObjectNotFoundError,
    StoreProviderError,
    bundle_path,
    link_path,
)


def _is_absence(exn) -> bool:
    """True when a botocore error means this exact key does not exist."""
    from botocore.exceptions import ClientError

    if not isinstance(exn, ClientError):
        return False
    code = exn.response["Error"]["Code"]
    return code in ("NoSuchKey", "404", "NotFound")


class _S3StoreBase:
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name
        self.s3 = None  # set by subclasses

    def _get(self, key: str) -> Tuple[bytes, Optional[str]]:
        from botocore.exceptions import ClientError

        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exn:
            if _is_absence(exn):
                raise ObjectNotFoundError(key) from exn
            raise StoreProviderError(f"reading {key} failed: {exn}") from exn
        return response["Body"].read(), response.get("ETag", "").strip('"') or None

    def get_latest_link(self) -> Tuple[bytes, Optional[str]]:
        return self._get(LATEST_LINK_PATH)

    def get_link(self, link_uid: str) -> bytes:
        return self._get(link_path(link_uid))[0]

    def download_bundle(self, bundle_uid: str, local_path) -> None:
        data, _etag = self._get(bundle_path(bundle_uid))
        with open(local_path, "wb") as handle:
            handle.write(data)


class S3Store(_S3StoreBase):
    """S3-backed store (works with MinIO or AWS S3). Test-only."""

    def __init__(self, endpoint_url, bucket_name, access_key, secret_key):
        import boto3
        from botocore.config import Config

        super().__init__(bucket_name)
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        try:
            self.s3.head_bucket(Bucket=bucket_name)
        except Exception:
            self.s3.create_bucket(Bucket=bucket_name)

    def _put(self, key: str, data: bytes, expected_etag: Optional[str]) -> Optional[str]:
        from botocore.exceptions import ClientError

        kwargs = {"Bucket": self.bucket_name, "Key": key, "Body": data}
        if expected_etag == CREATE_ONLY:
            kwargs["IfNoneMatch"] = CREATE_ONLY
        elif expected_etag is not None:
            kwargs["IfMatch"] = expected_etag
        try:
            response = self.s3.put_object(**kwargs)
        except ClientError as exn:
            code = exn.response["Error"]["Code"]
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise CasConflictError(f"{key}: {code}") from exn
            raise StoreProviderError(f"writing {key} failed: {exn}") from exn
        return response.get("ETag", "").strip('"') or None

    def put_bundle(self, bundle_uid: str, local_path) -> None:
        with open(local_path, "rb") as handle:
            self._put(bundle_path(bundle_uid), handle.read(), CREATE_ONLY)

    def put_link(self, link_uid: str, data: bytes) -> None:
        self._put(link_path(link_uid), data, CREATE_ONLY)

    def put_latest_link(
        self, data: bytes, expected_etag: Optional[str], link_uid: Optional[str] = None
    ) -> Optional[str]:
        return self._put(
            LATEST_LINK_PATH, data, CREATE_ONLY if expected_etag is None else expected_etag
        )


class PublicS3Store(_S3StoreBase):
    """Anonymous read-only store for a publicly-readable S3/MinIO bucket.

    To make a MinIO bucket publicly readable:
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": "*",
                           "Action": ["s3:GetObject"],
                           "Resource": [f"arn:aws:s3:::{bucket}/*"]}]
        }))
    """

    def __init__(self, endpoint_url, bucket_name):
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config

        super().__init__(bucket_name)
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            config=Config(signature_version=UNSIGNED),
            region_name="us-east-1",
        )
