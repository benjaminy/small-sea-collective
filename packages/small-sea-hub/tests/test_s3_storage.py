# Micro tests for the S3 storage adapter against a local MinIO server.
#
# No Hub backend or user database involved — just the adapter talking
# to MinIO to exercise upload_overwrite, upload_fresh, upload_if_match,
# and download.
#
# All tests share a single MinIO instance and use separate buckets
# for isolation.

import boto3
import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from botocore.config import Config
from small_sea_hub.adapters import SmallSeaS3Adapter

_minio_info = None


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    global _minio_info
    if _minio_info is None:
        _minio_info = minio_server_gen()
    return _minio_info


def make_adapter(minio_info, bucket):
    s3 = boto3.client(
        "s3",
        endpoint_url=minio_info["endpoint"],
        aws_access_key_id=minio_info["access_key"],
        aws_secret_access_key=minio_info["secret_key"],
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=bucket)
    return SmallSeaS3Adapter(s3, bucket)


def _raising_adapter(exc):
    class Client:
        def put_object(self, **kwargs):
            raise exc

    return SmallSeaS3Adapter(Client(), "bucket")


def _client_error(code, status):
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "PutObject",
    )


def test_s3_conditional_write_connection_refused_is_never_applied():
    adapter = _raising_adapter(EndpointConnectionError(endpoint_url="http://s3.invalid"))
    ok, _, failure = adapter.upload_if_match("latest-link.yaml", b"data", "e1")
    assert not ok and failure.never_applied


def test_s3_conditional_write_access_denied_is_never_applied():
    adapter = _raising_adapter(_client_error("AccessDenied", 403))
    ok, _, failure = adapter.upload_if_match("latest-link.yaml", b"data", "e1")
    assert not ok and failure.never_applied


def test_s3_conditional_write_read_timeout_is_outcome_unknown():
    adapter = _raising_adapter(ReadTimeoutError(endpoint_url="http://s3.invalid"))
    ok, _, failure = adapter.upload_if_match("latest-link.yaml", b"data", "e1")
    assert not ok and failure.outcome_unknown


def test_s3_conditional_write_5xx_is_outcome_unknown():
    adapter = _raising_adapter(_client_error("InternalError", 500))
    ok, _, failure = adapter.upload_if_match("latest-link.yaml", b"data", "e1")
    assert not ok and failure.outcome_unknown


def test_s3_precondition_failed_still_cas_conflict():
    adapter = _raising_adapter(_client_error("PreconditionFailed", 412))
    ok, _, failure = adapter.upload_if_match("latest-link.yaml", b"data", "e1")
    assert not ok and failure.cas_conflict


# ---- Tests ----


def test_upload_overwrite_and_download(minio):
    adapter = make_adapter(minio, "test-overwrite-dl")

    ok, etag, msg = adapter.upload_overwrite("greeting.txt", b"hello")
    assert ok
    assert etag is not None

    ok, data, dl_etag = adapter.download("greeting.txt")
    assert ok
    assert data == b"hello"
    assert dl_etag == etag


def test_upload_overwrite_replaces_content(minio):
    adapter = make_adapter(minio, "test-overwrite-replace")

    adapter.upload_overwrite("file.txt", b"version 1")
    ok, etag2, _ = adapter.upload_overwrite("file.txt", b"version 2")
    assert ok

    ok, data, _ = adapter.download("file.txt")
    assert data == b"version 2"


def test_upload_if_match_succeeds_with_correct_etag(minio):
    adapter = make_adapter(minio, "test-if-match-ok")

    ok, etag1, _ = adapter.upload_overwrite("file.txt", b"original")
    assert ok

    ok, etag2, _ = adapter.upload_if_match("file.txt", b"updated", etag1)
    assert ok
    assert etag2 != etag1

    ok, data, _ = adapter.download("file.txt")
    assert data == b"updated"


def test_upload_if_match_fails_with_stale_etag(minio):
    adapter = make_adapter(minio, "test-if-match-stale")

    ok, etag1, _ = adapter.upload_overwrite("file.txt", b"original")
    adapter.upload_overwrite("file.txt", b"someone else wrote this")

    # etag1 is now stale
    ok, etag_out, msg = adapter.upload_if_match("file.txt", b"conflict", etag1)
    assert not ok
    assert msg.cas_conflict
    assert "mismatch" in str(msg).lower()

    # Content should be unchanged
    ok, data, _ = adapter.download("file.txt")
    assert data == b"someone else wrote this"


def test_download_missing_key_reports_absence(minio):
    """Only a provider-confirmed missing object may be read as absence."""
    adapter = make_adapter(minio, "test-missing-key")

    ok, data, failure = adapter.download("does-not-exist.txt")
    assert not ok
    assert data is None
    assert failure.absent


def test_a_failed_download_is_not_absence(minio):
    """A bucket that is not there is a read that failed, not an empty object."""
    adapter = make_adapter(minio, "test-absent-vs-failed")
    adapter.bucket_name = "no-such-bucket-at-all"

    ok, data, failure = adapter.download("anything.txt")
    assert not ok
    assert data is None
    assert not failure.absent


def test_multiple_keys(minio):
    adapter = make_adapter(minio, "test-multi-keys")

    adapter.upload_overwrite("a.txt", b"aaa")
    adapter.upload_overwrite("b.txt", b"bbb")
    adapter.upload_overwrite("sub/c.txt", b"ccc")

    ok, data_a, _ = adapter.download("a.txt")
    ok, data_b, _ = adapter.download("b.txt")
    ok, data_c, _ = adapter.download("sub/c.txt")

    assert data_a == b"aaa"
    assert data_b == b"bbb"
    assert data_c == b"ccc"


def test_s3_prefixed_chain_head_write_is_classified():
    # Real chains live under a prefix, e.g. chains/<id>/latest-link.yaml.
    adapter = _raising_adapter(EndpointConnectionError(endpoint_url="http://s3.invalid"))
    ok, _, failure = adapter.upload_if_match("chains/abc/latest-link.yaml", b"data", "e1")
    assert not ok and failure.never_applied
