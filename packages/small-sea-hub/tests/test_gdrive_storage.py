import json

import httpx
import pytest
import respx
from small_sea_hub.adapters.gdrive import (DRIVE_API, DRIVE_UPLOAD,
                                           SmallSeaGDriveAdapter)
from small_sea_hub.cloud_errors import CloudValidatorMissingExn

TOKEN = "test-access-token"


def make_adapter(path_metadata=None):
    return SmallSeaGDriveAdapter(TOKEN, path_metadata=path_metadata)


# ---- Download ----


@respx.mock
def test_download_success():
    file_id = "abc123"
    adapter = make_adapter({"greeting.txt": file_id})

    respx.get(f"{DRIVE_API}/files/{file_id}", params={"alt": "media"}).mock(
        return_value=httpx.Response(
            200,
            content=b"hello world",
        )
    )
    respx.get(f"{DRIVE_API}/files/{file_id}", params={"fields": "version"}).mock(
        return_value=httpx.Response(200, json={"version": "11"})
    )

    ok, data, etag = adapter.download("greeting.txt")
    assert ok
    assert data == b"hello world"
    assert etag == "11"


@respx.mock
def test_download_not_found_no_cache():
    adapter = make_adapter()

    respx.get(f"{DRIVE_API}/files").mock(
        return_value=httpx.Response(200, json={"files": []})
    )

    ok, data, msg = adapter.download("missing.txt")
    assert not ok
    assert data is None


@respx.mock
def test_download_not_found_stale_cache():
    file_id = "stale-id"
    adapter = make_adapter({"missing.txt": file_id})

    respx.get(f"{DRIVE_API}/files/{file_id}").mock(return_value=httpx.Response(404))

    ok, data, msg = adapter.download("missing.txt")
    assert not ok
    # Should have cleared the cache entry
    assert "missing.txt" not in adapter.path_ids


# ---- Upload overwrite ----


@respx.mock
def test_upload_overwrite_create():
    adapter = make_adapter()

    # First, _find_file_id queries Drive and finds nothing
    respx.get(f"{DRIVE_API}/files").mock(
        return_value=httpx.Response(200, json={"files": []})
    )

    # Then creates via multipart upload
    respx.post(f"{DRIVE_UPLOAD}/files").mock(
        return_value=httpx.Response(
            200,
            json={"id": "new-file-id", "name": "data.bin", "version": "12"},
            headers={"ETag": '"etag-new"'},
        )
    )

    ok, etag, msg = adapter.upload_overwrite("data.bin", b"content")
    assert ok
    assert etag == "12"
    assert adapter.path_ids["data.bin"] == "new-file-id"


@respx.mock
def test_upload_overwrite_update():
    file_id = "existing-id"
    adapter = make_adapter({"data.bin": file_id})

    respx.patch(f"{DRIVE_UPLOAD}/files/{file_id}").mock(
        return_value=httpx.Response(
            200,
            json={"id": file_id, "name": "data.bin", "version": "12"},
        )
    )

    ok, etag, msg = adapter.upload_overwrite("data.bin", b"updated")
    assert ok
    assert etag == "12"


# ---- Upload fresh ----


@respx.mock
def test_upload_fresh_success():
    adapter = make_adapter()

    respx.get(f"{DRIVE_API}/files").mock(
        return_value=httpx.Response(200, json={"files": []})
    )
    respx.post(f"{DRIVE_UPLOAD}/files").mock(
        return_value=httpx.Response(
            200,
            json={"id": "fresh-id", "name": "new.txt", "version": "12"},
        )
    )

    ok, etag, msg = adapter.upload_fresh("new.txt", b"brand new")
    assert ok
    assert etag == "12"


@respx.mock
def test_upload_fresh_already_exists():
    file_id = "already-there"
    adapter = make_adapter({"existing.txt": file_id})

    ok, etag, msg = adapter.upload_fresh("existing.txt", b"nope")
    assert not ok
    assert msg.cas_conflict
    assert "already exists" in str(msg).lower()


# ---- Upload if-match ----


@respx.mock
def test_upload_if_match_success():
    file_id = "match-id"
    adapter = make_adapter({"file.txt": file_id})

    respx.patch(f"{DRIVE_UPLOAD}/files/{file_id}").mock(
        return_value=httpx.Response(
            200,
            json={"id": file_id, "name": "file.txt", "version": "13"},
        )
    )
    respx.get(f"{DRIVE_API}/files/{file_id}", params={"fields": "version"}).mock(
        return_value=httpx.Response(200, json={"version": "12"}, headers={"ETag": '"drive-http-etag"'})
    )

    ok, etag, msg = adapter.upload_if_match("file.txt", b"new data", "12")
    assert ok
    assert etag == "13"
    assert respx.calls.last.request.headers["If-Match"] == "drive-http-etag"


@respx.mock
def test_upload_if_match_stale_etag():
    file_id = "match-id"
    adapter = make_adapter({"file.txt": file_id})

    respx.patch(f"{DRIVE_UPLOAD}/files/{file_id}").mock(
        return_value=httpx.Response(412)
    )
    respx.get(f"{DRIVE_API}/files/{file_id}", params={"fields": "version"}).mock(
        return_value=httpx.Response(200, json={"version": "12"}, headers={"ETag": '"current"'})
    )

    ok, etag, msg = adapter.upload_if_match("file.txt", b"conflict", "old-etag")
    assert not ok
    assert msg.cas_conflict
    assert "mismatch" in str(msg).lower()


# ---- Path metadata persistence ----


def test_path_metadata_roundtrip():
    original = {"a.txt": "id-a", "b.txt": "id-b"}
    adapter = make_adapter(original)
    recovered = adapter.get_path_metadata()
    assert recovered == original
    # Mutations to the returned dict don't affect the adapter
    recovered["c.txt"] = "id-c"
    assert "c.txt" not in adapter.path_ids


@respx.mock
def test_gdrive_head_read_returns_nonempty_etag():
    adapter = make_adapter({"chains/latest-link.yaml": "head-id"})
    respx.get(f"{DRIVE_API}/files/head-id", params={"alt": "media"}).mock(
        return_value=httpx.Response(200, content=b"head")
    )
    respx.get(f"{DRIVE_API}/files/head-id", params={"fields": "version"}).mock(
        return_value=httpx.Response(200, json={"version": "42"})
    )
    ok, _, etag = adapter.download("chains/latest-link.yaml")
    assert ok and etag == "42"


@respx.mock
def test_gdrive_head_write_returns_nonempty_etag():
    adapter = make_adapter()
    respx.get(f"{DRIVE_API}/files").mock(
        return_value=httpx.Response(200, json={"files": []})
    )
    respx.post(f"{DRIVE_UPLOAD}/files").mock(
        return_value=httpx.Response(200, json={"id": "head-id", "version": "43"})
    )
    ok, etag, _ = adapter.upload_overwrite("chains/latest-link.yaml", b"head")
    assert ok and etag == "43"


@respx.mock
def test_gdrive_missing_validator_raises_instead_of_empty_etag():
    adapter = make_adapter({"chains/latest-link.yaml": "head-id"})
    respx.get(f"{DRIVE_API}/files/head-id", params={"alt": "media"}).mock(
        return_value=httpx.Response(200, content=b"head")
    )
    respx.get(f"{DRIVE_API}/files/head-id", params={"fields": "version"}).mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(CloudValidatorMissingExn):
        adapter.download("chains/latest-link.yaml")


@respx.mock
def test_gdrive_conditional_write_uses_returned_etag():
    adapter = make_adapter({"chains/latest-link.yaml": "head-id"})
    respx.get(f"{DRIVE_API}/files/head-id", params={"fields": "version"}).mock(
        return_value=httpx.Response(200, json={"version": "newer"}, headers={"ETag": '"drive-etag"'})
    )
    ok, _, msg = adapter.upload_if_match("chains/latest-link.yaml", b"head", "stale")
    assert not ok and msg.cas_conflict


@respx.mock
def test_gdrive_download_refuses_when_file_changes_mid_read():
    adapter = make_adapter({"chains/latest-link.yaml": "head-id"})
    respx.get(f"{DRIVE_API}/files/head-id", params={"alt": "media"}).mock(
        return_value=httpx.Response(200, content=b"old head")
    )
    respx.get(f"{DRIVE_API}/files/head-id", params={"fields": "version"}).mock(
        side_effect=[
            httpx.Response(200, json={"version": "42"}),
            httpx.Response(200, json={"version": "43"}),
        ]
    )
    ok, data, _outcome = adapter.download("chains/latest-link.yaml")
    assert not ok and data is None
