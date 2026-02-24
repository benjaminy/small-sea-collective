import json

import httpx
import pytest
import respx

from small_sea_hub.adapters.dropbox import SmallSeaDropboxAdapter, DROPBOX_CONTENT


TOKEN = "test-access-token"


def make_adapter():
    return SmallSeaDropboxAdapter(TOKEN)


# ---- Download ----

@respx.mock
def test_download_success():
    adapter = make_adapter()

    respx.post(f"{DROPBOX_CONTENT}/files/download").mock(
        return_value=httpx.Response(
            200,
            content=b"hello dropbox",
            headers={"Dropbox-API-Result": json.dumps({"rev": "rev001"})},
        )
    )

    ok, data, rev = adapter.download("greeting.txt")
    assert ok
    assert data == b"hello dropbox"
    assert rev == "rev001"


@respx.mock
def test_download_not_found():
    adapter = make_adapter()

    respx.post(f"{DROPBOX_CONTENT}/files/download").mock(
        return_value=httpx.Response(409, json={
            "error_summary": "path/not_found/...",
            "error": {".tag": "path", "path": {".tag": "not_found"}},
        })
    )

    ok, data, msg = adapter.download("missing.txt")
    assert not ok
    assert data is None


# ---- Upload overwrite ----

@respx.mock
def test_upload_overwrite():
    adapter = make_adapter()

    respx.post(f"{DROPBOX_CONTENT}/files/upload").mock(
        return_value=httpx.Response(200, json={
            "name": "file.txt",
            "rev": "rev002",
        })
    )

    ok, rev, msg = adapter.upload_overwrite("file.txt", b"data")
    assert ok
    assert rev == "rev002"

    # Verify the mode was set to overwrite
    req = respx.calls.last.request
    api_arg = json.loads(req.headers["Dropbox-API-Arg"])
    assert api_arg["mode"] == {".tag": "overwrite"}


# ---- Upload fresh ----

@respx.mock
def test_upload_fresh_success():
    adapter = make_adapter()

    respx.post(f"{DROPBOX_CONTENT}/files/upload").mock(
        return_value=httpx.Response(200, json={
            "name": "new.txt",
            "rev": "rev003",
        })
    )

    ok, rev, msg = adapter.upload_fresh("new.txt", b"brand new")
    assert ok
    assert rev == "rev003"

    req = respx.calls.last.request
    api_arg = json.loads(req.headers["Dropbox-API-Arg"])
    assert api_arg["mode"] == {".tag": "add"}


@respx.mock
def test_upload_fresh_already_exists():
    adapter = make_adapter()

    respx.post(f"{DROPBOX_CONTENT}/files/upload").mock(
        return_value=httpx.Response(409, json={
            "error_summary": "path/conflict/file/...",
            "error": {".tag": "path", "reason": {".tag": "conflict"}},
        })
    )

    ok, rev, msg = adapter.upload_fresh("existing.txt", b"nope")
    assert not ok
    assert "already exists" in msg.lower()


# ---- Upload if-match (rev-based conditional write) ----

@respx.mock
def test_upload_if_match_success():
    adapter = make_adapter()

    respx.post(f"{DROPBOX_CONTENT}/files/upload").mock(
        return_value=httpx.Response(200, json={
            "name": "file.txt",
            "rev": "rev004",
        })
    )

    ok, rev, msg = adapter.upload_if_match("file.txt", b"updated", "rev003")
    assert ok
    assert rev == "rev004"

    req = respx.calls.last.request
    api_arg = json.loads(req.headers["Dropbox-API-Arg"])
    assert api_arg["mode"] == {".tag": "update", "update": "rev003"}


@respx.mock
def test_upload_if_match_conflict():
    adapter = make_adapter()

    respx.post(f"{DROPBOX_CONTENT}/files/upload").mock(
        return_value=httpx.Response(409, json={
            "error_summary": "path/conflict/...",
            "error": {".tag": "path", "reason": {".tag": "conflict"}},
        })
    )

    ok, rev, msg = adapter.upload_if_match("file.txt", b"conflict", "old-rev")
    assert not ok
    assert "mismatch" in msg.lower()
