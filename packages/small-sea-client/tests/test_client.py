"""Tests for the SmallSeaSession methods the async client does not have.

Uses respx to mock HTTP calls — no live Hub or MinIO needed.
"""

import base64

import httpx
import pytest
import respx

from small_sea_client.client import (
    SmallSeaClient,
    SmallSeaConflict,
    SmallSeaNotFound,
    SmallSeaPublicationNotAuthentic,
    SmallSeaPublicationPending,
    SmallSeaSession,
)

BASE_URL = "http://127.0.0.1:11437"
FAKE_TOKEN = "ab" * 32  # 32 bytes as hex


@pytest.fixture
def client():
    return SmallSeaClient()


@pytest.fixture
def session(client):
    return SmallSeaSession(client, FAKE_TOKEN)


# ---- Session flow ----


@respx.mock
def test_session_sign_commit_returns_key(session):
    respx.post(f"{BASE_URL}/session/sign").mock(
        return_value=httpx.Response(200, json={"signature": "sig", "public_key": "ssh-ed25519 key"})
    )
    result = session.sign_commit(b"payload", "core")
    assert result.signature == "sig"
    assert result.public_key == "ssh-ed25519 key"


# ---- Upload ----


@respx.mock
def test_upload_returns_etag(session):
    respx.post(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(200, json={"ok": True, "etag": "etag-abc", "message": "ok"})
    )
    etag = session.upload("notes/hello.txt", b"hello world")
    assert etag == "etag-abc"


@respx.mock
def test_upload_sends_base64_data(session):
    route = respx.post(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(200, json={"ok": True, "etag": "e1", "message": "ok"})
    )
    content = b"some binary \x00 data"
    session.upload("file.bin", content)

    import json

    body = json.loads(route.calls[0].request.content)
    assert base64.b64decode(body["data"]) == content
    assert body["path"] == "file.bin"
    assert "expected_etag" not in body


@respx.mock
def test_upload_if_match_sends_etag(session):
    route = respx.post(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(200, json={"ok": True, "etag": "etag-new", "message": "ok"})
    )
    etag = session.upload_if_match("file.txt", b"updated", "etag-old")
    assert etag == "etag-new"

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["expected_etag"] == "etag-old"


@respx.mock
def test_upload_if_match_conflict(session):
    respx.post(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(
            409, json={"detail": "CAS conflict: file was modified concurrently"}
        )
    )
    with pytest.raises(SmallSeaConflict):
        session.upload_if_match("file.txt", b"updated", "stale-etag")


@respx.mock
def test_upload_if_match_conflict_with_other_error_string(session):
    respx.post(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "cas_conflict",
                "detail": "CAS conflict: file was modified concurrently",
            },
        )
    )
    with pytest.raises(SmallSeaConflict, match="CAS conflict"):
        session.upload_if_match("file.txt", b"updated", "stale-etag")


@respx.mock
def test_upload_if_match_conflict_with_non_json_body(session):
    respx.post(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(409, text="CAS conflict")
    )
    with pytest.raises(SmallSeaConflict, match="CAS conflict"):
        session.upload_if_match("file.txt", b"updated", "stale-etag")


def test_upload_create_only_not_implemented(session):
    with pytest.raises(NotImplementedError):
        session.upload_create_only("new.txt", b"content")


@respx.mock
def test_upload_sends_bearer_token(session):
    route = respx.post(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(200, json={"ok": True, "etag": "e1", "message": "ok"})
    )
    session.upload("f.txt", b"x")
    auth = route.calls[0].request.headers.get("authorization")
    assert auth == f"Bearer {FAKE_TOKEN}"


# ---- Download ----


@respx.mock
def test_download_returns_data_and_etag(session):
    content = b"file contents"
    respx.get(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(
            200,
            content=content,
            headers={"etag": "etag-xyz"},
        )
    )
    data, etag = session.download("notes/hello.txt")
    assert data == content
    assert etag == "etag-xyz"


@respx.mock
def test_download_not_found(session):
    respx.get(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    with pytest.raises(SmallSeaNotFound):
        session.download("missing.txt")


@respx.mock
def test_download_sends_path_param(session):
    route = respx.get(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(
            200, content=b"x", headers={"etag": "e"}
        )
    )
    session.download("sub/dir/file.txt")
    assert route.calls[0].request.url.params["path"] == "sub/dir/file.txt"


# ---- Notifications ----


@respx.mock
def test_send_notification_returns_id(session):
    respx.post(f"{BASE_URL}/notifications").mock(
        return_value=httpx.Response(200, json={"ok": True, "id": "msg-001"})
    )
    msg_id = session.send_notification("hello team")
    assert msg_id == "msg-001"


@respx.mock
def test_send_notification_with_title(session):
    route = respx.post(f"{BASE_URL}/notifications").mock(
        return_value=httpx.Response(200, json={"ok": True, "id": "msg-002"})
    )
    session.send_notification("data ready", title="Sync Update")

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["message"] == "data ready"
    assert body["title"] == "Sync Update"


@respx.mock
def test_send_notification_no_title_omits_field(session):
    route = respx.post(f"{BASE_URL}/notifications").mock(
        return_value=httpx.Response(200, json={"ok": True, "id": "msg-003"})
    )
    session.send_notification("ping")

    import json

    body = json.loads(route.calls[0].request.content)
    assert "title" not in body


@respx.mock
def test_poll_notifications_defaults_to_all(session):
    route = respx.get(f"{BASE_URL}/notifications").mock(
        return_value=httpx.Response(200, json={"ok": True, "messages": []})
    )
    session.poll_notifications()
    assert route.calls[0].request.url.params["since"] == "all"


@respx.mock
def test_poll_notifications_updates_cursor(session):
    messages = [{"id": "m1", "message": "a"}, {"id": "m2", "message": "b"}]
    respx.get(f"{BASE_URL}/notifications").mock(
        return_value=httpx.Response(200, json={"ok": True, "messages": messages})
    )
    session.poll_notifications()
    assert session.last_notification_id == "m2"


@respx.mock
def test_poll_notifications_uses_cursor_on_second_call(session):
    msgs_first = [{"id": "m1", "message": "a"}]
    msgs_second = [{"id": "m2", "message": "b"}]
    route = respx.get(f"{BASE_URL}/notifications").mock(
        side_effect=[
            httpx.Response(200, json={"ok": True, "messages": msgs_first}),
            httpx.Response(200, json={"ok": True, "messages": msgs_second}),
        ]
    )

    session.poll_notifications()
    session.poll_notifications()

    assert route.calls[1].request.url.params["since"] == "m1"


@respx.mock
def test_poll_notifications_explicit_since_overrides_cursor(session):
    route = respx.get(f"{BASE_URL}/notifications").mock(
        return_value=httpx.Response(200, json={"ok": True, "messages": []})
    )
    session._last_notification_id = "m5"
    session.poll_notifications(since="all")
    assert route.calls[0].request.url.params["since"] == "all"


@respx.mock
def test_poll_notifications_no_messages_leaves_cursor_unchanged(session):
    respx.get(f"{BASE_URL}/notifications").mock(
        return_value=httpx.Response(200, json={"ok": True, "messages": []})
    )
    session._last_notification_id = "m5"
    session.poll_notifications()
    assert session.last_notification_id == "m5"


# --- Publication failures never become success or absence ---


@respx.mock
@pytest.mark.parametrize(
    "error",
    [
        "sender_key_unavailable",
        "ownership_projection_absent",
        "device_ownership_unavailable",
        "device_ownership_ambiguous",
        "expected_publisher_unknown",
    ],
)
def test_download_pending_publication_is_not_absence(session, error):
    respx.get(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(
            409, json={"error": error, "detail": "waiting on evidence"}
        )
    )
    with pytest.raises(SmallSeaPublicationPending) as exc_info:
        session.download("file.txt")

    assert exc_info.value.reason == error
    # A caller that only knows about conflicts still sees one; a caller that
    # reads not-found as an empty chain never gets the chance.
    assert isinstance(exc_info.value, SmallSeaConflict)
    assert not isinstance(exc_info.value, SmallSeaNotFound)


@respx.mock
def test_download_of_an_inauthentic_publication_raises(session):
    respx.get(f"{BASE_URL}/cloud_file").mock(
        return_value=httpx.Response(
            502,
            json={
                "error": "publication_not_authentic",
                "reason": "context_mismatch",
                "detail": "Message authenticates a different object context",
            },
        )
    )
    with pytest.raises(SmallSeaPublicationNotAuthentic) as exc_info:
        session.download("file.txt")

    assert exc_info.value.reason == "context_mismatch"
    assert not isinstance(exc_info.value, SmallSeaNotFound)
