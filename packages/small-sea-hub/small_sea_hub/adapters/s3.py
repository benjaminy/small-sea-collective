import json
from typing import Optional

from botocore.exceptions import ClientError

from .base import SmallSeaStorageAdapter


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

    def download(self, path: str):
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=path)
            return True, response["Body"].read(), response["ETag"].strip('"')
        except ClientError as exn:
            error_code = exn.response["Error"]["Code"]
            return False, None, f"Download failed: {error_code}"

    def _upload(
        self,
        path: str,
        data: bytes,
        expected_etag: Optional[str],
        content_type: str = "application/octet-stream",
    ):
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
            new_etag = response["ETag"].strip('"')
            return True, new_etag, "Object updated successfully"
        except ClientError as exn:
            error_code = exn.response["Error"]["Code"]
            if error_code == "PreconditionFailed":
                if expected_etag is None:
                    return False, None, "Object already exists"
                else:
                    return False, None, "ETag mismatch - object was modified"
            return False, None, f"Operation failed: {exn}"
