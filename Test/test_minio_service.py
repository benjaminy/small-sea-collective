# Top Matter

import os
import subprocess
import tempfile
import time
import pytest
import boto3


def test_minio_bucket_creation(minio_server_gen):
    minio_server = minio_server_gen()
    s3 = boto3.client(
        "s3",
        endpoint_url=minio_server["endpoint"],
        aws_access_key_id=minio_server["access_key"],
        aws_secret_access_key=minio_server["secret_key"]
    )

    print(s3.list_buckets())
    bucket_name = "pytest-bucket"
    s3.create_bucket(Bucket=bucket_name)
    buckets = s3.list_buckets()["Buckets"]
    assert any(b["Name"] == bucket_name for b in buckets)
