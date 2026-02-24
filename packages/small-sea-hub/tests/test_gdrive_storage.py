import json

import httpx
import pytest
import respx

from small_sea_hub.adapters.gdrive import SmallSeaGDriveAdapter, DRIVE_API, DRIVE_UPLOAD


TOKEN = "test-access-token"


def make_adapter(path_metadata=None):
    return SmallSeaGDriveAdapter(TOKEN, path_metadata=path_metadata)


# ---- Download ----

@respx.mock
def test_download_success():
    file_id = "abc123"
    adapter = make_adapter({"greeting.txt": file_id})

    respx.get(f"{DRIVE_API}/files/{file_id}").mock(
        return_value=httpx.Response(
            200,
            content=b"hello world",
            headers={"ETag": '"etag1"'},
        )
    )

    ok, data, etag = adapter.download("greeting.txt")
    assert ok
    assert data == b"hello world"
    assert etag == "etag1"


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

    respx.get(f"{DRIVE_API}/files/{file_id}").mock(
        return_value=httpx.Response(404)
    )

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
            json={"id": "new-file-id", "name": "data.bin"},
            headers={"ETag": '"etag-new"'},
        )
    )

    ok, etag, msg = adapter.upload_overwrite("data.bin", b"content")
    assert ok
    assert etag == "etag-new"
    assert adapter.path_ids["data.bin"] == "new-file-id"


@respx.mock
def test_upload_overwrite_update():
    file_id = "existing-id"
    adapter = make_adapter({"data.bin": file_id})

    respx.patch(f"{DRIVE_UPLOAD}/files/{file_id}").mock(
        return_value=httpx.Response(
            200,
            json={"id": file_id, "name": "data.bin"},
            headers={"ETag": '"etag-v2"'},
        )
    )

    ok, etag, msg = adapter.upload_overwrite("data.bin", b"updated")
    assert ok
    assert etag == "etag-v2"


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
            json={"id": "fresh-id", "name": "new.txt"},
            headers={"ETag": '"etag-fresh"'},
        )
    )

    ok, etag, msg = adapter.upload_fresh("new.txt", b"brand new")
    assert ok
    assert etag == "etag-fresh"


@respx.mock
def test_upload_fresh_already_exists():
    file_id = "already-there"
    adapter = make_adapter({"existing.txt": file_id})

    ok, etag, msg = adapter.upload_fresh("existing.txt", b"nope")
    assert not ok
    assert "already exists" in msg.lower()


# ---- Upload if-match ----

@respx.mock
def test_upload_if_match_success():
    file_id = "match-id"
    adapter = make_adapter({"file.txt": file_id})

    respx.patch(f"{DRIVE_UPLOAD}/files/{file_id}").mock(
        return_value=httpx.Response(
            200,
            json={"id": file_id, "name": "file.txt"},
            headers={"ETag": '"etag-v3"'},
        )
    )

    ok, etag, msg = adapter.upload_if_match("file.txt", b"new data", "etag-v2")
    assert ok
    assert etag == "etag-v3"


@respx.mock
def test_upload_if_match_stale_etag():
    file_id = "match-id"
    adapter = make_adapter({"file.txt": file_id})

    respx.patch(f"{DRIVE_UPLOAD}/files/{file_id}").mock(
        return_value=httpx.Response(412)
    )

    ok, etag, msg = adapter.upload_if_match("file.txt", b"conflict", "old-etag")
    assert not ok
    assert "mismatch" in msg.lower()


# ---- Path metadata persistence ----

def test_path_metadata_roundtrip():
    original = {"a.txt": "id-a", "b.txt": "id-b"}
    adapter = make_adapter(original)
    recovered = adapter.get_path_metadata()
    assert recovered == original
    # Mutations to the returned dict don't affect the adapter
    recovered["c.txt"] = "id-c"
    assert "c.txt" not in adapter.path_ids
