import json
from io import BytesIO

import httpx
import pytest
import respx

from small_sea_hub.adapters.dropbox import DROPBOX_CONTENT, SmallSeaDropboxAdapter
from small_sea_hub.adapters.gdrive import DRIVE_API, DRIVE_UPLOAD, SmallSeaGDriveAdapter
from small_sea_hub.adapters.s3 import SmallSeaS3Adapter
from small_sea_hub.cloud_errors import CloudValidatorMissingExn


class FakeS3:
    def __init__(self, response):
        self.response = response

    def get_object(self, **kwargs):
        return {"Body": BytesIO(b"head"), **self.response}

    def put_object(self, **kwargs):
        return self.response


@respx.mock
def test_dropbox_head_etag_behavior_unchanged():
    adapter = SmallSeaDropboxAdapter("token")
    respx.post(f"{DROPBOX_CONTENT}/files/download").mock(
        return_value=httpx.Response(
            200,
            content=b"head",
            headers={"Dropbox-API-Result": json.dumps({"rev": "rev-a"})},
        )
    )
    ok, _, etag = adapter.download("latest-link.yaml")
    assert ok and etag == "rev-a"

    respx.post(f"{DROPBOX_CONTENT}/files/upload").mock(
        return_value=httpx.Response(200, json={"rev": "rev-b"})
    )
    ok, etag, _ = adapter.upload_if_match("latest-link.yaml", b"new", "rev-a")
    assert ok and etag == "rev-b"


def test_s3_head_etag_behavior_unchanged():
    adapter = SmallSeaS3Adapter(FakeS3({"ETag": '"etag-a"'}), "bucket")
    ok, _, etag = adapter.download("latest-link.yaml")
    assert ok and etag == "etag-a"
    adapter.s3.response = {"ETag": '"etag-b"'}
    ok, etag, _ = adapter.upload_if_match("latest-link.yaml", b"new", "etag-a")
    assert ok and etag == "etag-b"


@pytest.mark.parametrize(
    ("adapter_kind", "operation"),
    [
        ("gdrive", "read"),
        ("gdrive", "write"),
        ("dropbox", "read"),
        ("dropbox", "write"),
        ("s3", "read"),
        ("s3", "write"),
    ],
)
@respx.mock
def test_no_adapter_substitutes_empty_etag(adapter_kind, operation):
    if adapter_kind == "gdrive":
        adapter = SmallSeaGDriveAdapter("token", path_metadata={"head": "id"})
        if operation == "read":
            respx.get(f"{DRIVE_API}/files/id", params={"alt": "media"}).mock(
                return_value=httpx.Response(200, content=b"head")
            )
            respx.get(f"{DRIVE_API}/files/id", params={"fields": "version"}).mock(
                return_value=httpx.Response(200, json={})
            )
            action = lambda: adapter.download("head")
        else:
            respx.get(f"{DRIVE_API}/files/id", params={"fields": "version"}).mock(
                return_value=httpx.Response(200, json={"version": "1"}, headers={"ETag": '"etag"'})
            )
            respx.patch(f"{DRIVE_UPLOAD}/files/id").mock(
                return_value=httpx.Response(200, json={"id": "id"})
            )
            action = lambda: adapter.upload_if_match("head", b"head", "1")
    elif adapter_kind == "dropbox":
        adapter = SmallSeaDropboxAdapter("token")
        if operation == "read":
            respx.post(f"{DROPBOX_CONTENT}/files/download").mock(
                return_value=httpx.Response(200, content=b"head", headers={"Dropbox-API-Result": "{}"})
            )
            action = lambda: adapter.download("head")
        else:
            respx.post(f"{DROPBOX_CONTENT}/files/upload").mock(
                return_value=httpx.Response(200, json={})
            )
            action = lambda: adapter.upload_overwrite("head", b"head")
    else:
        response = {} if operation == "read" else {}
        adapter = SmallSeaS3Adapter(FakeS3(response), "bucket")
        action = (
            lambda: adapter.download("head")
            if operation == "read"
            else adapter.upload_overwrite("head", b"head")
        )

    with pytest.raises(CloudValidatorMissingExn):
        action()
