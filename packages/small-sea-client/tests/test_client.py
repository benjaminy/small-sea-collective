"""Tests for SmallSeaClient and SmallSeaSession.

Uses respx to mock HTTP calls — no live Hub or MinIO needed.
"""

import base64

import httpx
import pytest
import respx

from small_sea_client.client import (
    SmallSeaAppBootstrapRequired,
    SmallSeaClient,
    SmallSeaConflict,
    SmallSeaError,
    SmallSeaHubUnavailable,
    SmallSeaNotFound,
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
def test_request_session(client):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(200, json={"pending_id": "pending123"})
    )
    pending_id = client.request_session("alice", "MyApp", "MyTeam", "TestClient")
    assert pending_id == "pending123"


@respx.mock
def test_request_session_sends_correct_body(client):
    route = respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(200, json={"pending_id": "p1"})
    )
    client.request_session("alice", "MyApp", "MyTeam", "TestClient")
    assert route.called
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body == {
        "participant": "alice",
        "app": "MyApp",
        "team": "MyTeam",
        "client": "TestClient",
        "mode": "encrypted",
    }


@respx.mock
def test_request_session_can_override_mode(client):
    route = respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(200, json={"pending_id": "p2"})
    )
    client.request_session("alice", "MyApp", "MyTeam", "TestClient", mode="passthrough")

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["mode"] == "passthrough"


@pytest.mark.parametrize(
    "reason",
    [
        "app_unknown",
        "participant_berth_missing",
        "team_berth_missing",
        "app_friendly_name_ambiguous",
    ],
)
@respx.mock
def test_request_session_app_bootstrap_required_preserves_reason(client, reason):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "app_bootstrap_required",
                "reason": reason,
                "app": "SharedFileVault",
                "team": "ProjectX",
            },
        )
    )
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        client.request_session("alice", "SharedFileVault", "ProjectX", "TestClient")

    exc = exc_info.value
    assert exc.reason == reason
    assert exc.app == "SharedFileVault"
    assert exc.team == "ProjectX"


@respx.mock
def test_request_session_app_bootstrap_required_is_not_conflict(client):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "app_bootstrap_required",
                "reason": "app_unknown",
                "app": "SharedFileVault",
                "team": "ProjectX",
            },
        )
    )
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        client.request_session("alice", "SharedFileVault", "ProjectX", "TestClient")

    assert isinstance(exc_info.value, SmallSeaError)
    assert not isinstance(exc_info.value, SmallSeaConflict)


@respx.mock
def test_request_session_app_bootstrap_required_user_message(client):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "app_bootstrap_required",
                "reason": "app_unknown",
                "app": "SharedFileVault",
                "team": "ProjectX",
            },
        )
    )
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        client.request_session("alice", "SharedFileVault", "ProjectX", "TestClient")

    expected = (
        "SharedFileVault isn't set up yet. "
        "Open Manager to register it for team ProjectX."
    )
    assert exc_info.value.user_message == expected
    assert str(exc_info.value) == expected


@respx.mock
def test_request_session_app_bootstrap_required_allows_no_team(client):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "app_bootstrap_required",
                "reason": "app_unknown",
                "app": "SharedFileVault",
                "team": None,
            },
        )
    )
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        client.request_session("alice", "SharedFileVault", "ProjectX", "TestClient")

    assert exc_info.value.team is None
    assert (
        exc_info.value.user_message
        == "SharedFileVault isn't set up yet. Open Manager to register it."
    )


@respx.mock
def test_request_session_app_bootstrap_required_preserves_unknown_reason(client):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "app_bootstrap_required",
                "reason": "future_more_specific_reason",
                "app": "SharedFileVault",
                "team": "ProjectX",
            },
        )
    )
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        client.request_session("alice", "SharedFileVault", "ProjectX", "TestClient")

    assert exc_info.value.reason == "future_more_specific_reason"


@respx.mock
def test_request_session_app_bootstrap_required_reads_top_level_body(client):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "app_bootstrap_required",
                "reason": "app_unknown",
                "app": "SharedFileVault",
                "team": "ProjectX",
            },
        )
    )
    with pytest.raises(SmallSeaAppBootstrapRequired):
        client.request_session("alice", "SharedFileVault", "ProjectX", "TestClient")


@respx.mock
def test_confirm_session_returns_session(client):
    respx.post(f"{BASE_URL}/sessions/confirm").mock(
        return_value=httpx.Response(200, json=FAKE_TOKEN)
    )
    session = client.confirm_session("pending123", "1234")
    assert isinstance(session, SmallSeaSession)
    assert session.token == FAKE_TOKEN


@respx.mock
def test_confirm_session_wrong_pin(client):
    respx.post(f"{BASE_URL}/sessions/confirm").mock(
        return_value=httpx.Response(400, json={"detail": "Invalid PIN"})
    )
    with pytest.raises(SmallSeaError, match="Invalid PIN"):
        client.confirm_session("pending123", "0000")


def test_hub_unavailable(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/sessions/request").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(SmallSeaHubUnavailable):
            client.request_session("alice", "MyApp", "MyTeam", "TestClient")


@respx.mock
def test_server_error_non_json_body_uses_response_text(client):
    respx.post(f"{BASE_URL}/sessions/request").mock(
        return_value=httpx.Response(500, text="server sad")
    )
    with pytest.raises(SmallSeaError, match="HTTP 500: server sad"):
        client.request_session("alice", "MyApp", "MyTeam", "TestClient")


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
            json={
                "ok": True,
                "data": base64.b64encode(content).decode(),
                "etag": "etag-xyz",
            },
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
            200,
            json={"ok": True, "data": base64.b64encode(b"x").decode(), "etag": "e"},
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
