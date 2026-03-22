"""Test-only infrastructure for Cod Sync. Not for production use.

S3Remote is a CodSyncRemote implementation that directly accesses S3/MinIO,
bypassing the Hub. Production code should use SmallSeaRemote (Hub-backed)
instead. This module exists so that test suites across the workspace can
import S3Remote for integration tests against MinIO.
"""

import io

import yaml

from cod_sync.protocol import CodSyncRemote


class S3Remote(CodSyncRemote):
    """S3-backed cloud storage remote (works with MinIO or AWS S3).

    Test-only. Production cloud access should go through the Hub.
    """

    def __init__(self, endpoint_url, bucket_name, access_key, secret_key):
        import boto3
        from botocore.config import Config

        self.bucket_name = bucket_name
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        # Ensure bucket exists
        try:
            self.s3.head_bucket(Bucket=bucket_name)
        except Exception:
            self.s3.create_bucket(Bucket=bucket_name)

    def upload_latest_link(
        self, link_uid, blob, bundle_uid, local_bundle_path, expected_etag=None
    ):
        # expected_etag is accepted for interface compatibility but not enforced.
        # 1. Upload bundle
        self.s3.upload_file(
            local_bundle_path, self.bucket_name, f"B-{bundle_uid}.bundle"
        )

        # 2. Serialize link YAML
        link_yaml = yaml.dump(blob, default_flow_style=False).encode("utf-8")

        # 3. Upload latest-link.yaml and L-{link_uid}.yaml
        self.s3.put_object(
            Bucket=self.bucket_name, Key="latest-link.yaml", Body=link_yaml
        )
        self.s3.put_object(
            Bucket=self.bucket_name, Key=f"L-{link_uid}.yaml", Body=link_yaml
        )

    def get_link(self, uid):
        if uid == "latest-link":
            key = "latest-link.yaml"
        else:
            key = f"L-{uid}.yaml"

        try:
            resp = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            link = self.read_link_blob(io.BytesIO(resp["Body"].read()))
            if uid == "latest-link":
                etag = resp.get("ETag")
                return (link, etag)
            return link
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception as e:
            if "NoSuchKey" in str(e) or "Not Found" in str(e):
                return None
            raise

    def get_latest_link(self):
        return self.get_link("latest-link")

    def download_bundle(self, bundle_uid, local_bundle_path):
        self.s3.download_file(
            self.bucket_name, f"B-{bundle_uid}.bundle", local_bundle_path
        )
