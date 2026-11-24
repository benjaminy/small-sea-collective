"""Micro tests for the Hub client in small_sea_client.client."""

import json

import httpx
import pytest

from small_sea_client.client import (
    SmallSeaAppBootstrapRequired,
    SmallSeaConflict,
    SmallSeaError,
    SmallSeaHubUnavailable,
    SmallSeaNotFound,
    SmallSeaSession,
    async_transport_for,
    confirm_session,
    open_session,
    resend_notification,
    start_session,
)

pytestmark = pytest.mark.anyio

TOKEN = "ab" * 32


@pytest.fixture
def anyio_backend():
    return "asyncio"


def session(respond):
    requests = []

    def handler(request):
        requests.append(request)
        return respond(request)

    http = httpx.AsyncClient(base_url="http://hub", transport=httpx.MockTransport(handler))
    return SmallSeaSession(http, TOKEN), requests


async def test_watch_sends_known_counts_and_returns_updates():
    s, requests = session(lambda r: httpx.Response(200, json={"updated": {"t1": 3}}))
    assert await s.watch_notifications({"t1": 2}, timeout=7) == {"updated": {"t1": 3}}
    assert requests[0].url.path == "/notifications/watch"
    assert json.loads(requests[0].content) == {"known": {"t1": 2}, "timeout": 7}
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"


async def test_session_peers():
    s, requests = session(lambda r: httpx.Response(200, json={"peers": [{"teammate_id": "t1"}]}))
    assert await s.session_peers() == [{"teammate_id": "t1"}]
    assert requests[0].method == "GET" and requests[0].url.path == "/session/peers"


async def test_verify_history_decodes_view(tmp_path):
    s, requests = session(lambda r: httpx.Response(200, json={"view_identifier": "AH8", "commits": []}))
    result = await s.verify_history(tmp_path / ".git", "abc123")
    assert result == {"view_identifier": b"\x00\x7f", "commits": []}
    assert json.loads(requests[0].content) == {"git_dir": str(tmp_path / ".git"), "head": "abc123"}


async def test_authority_view_decodes_view():
    s, requests = session(lambda r: httpx.Response(200, json={"authority_view": "AH8"}))
    assert await s.authority_view() == b"\x00\x7f"
    assert requests[0].method == "GET" and requests[0].url.path == "/session/authority_view"


async def test_session_info_and_signing_key():
    def respond(request):
        return httpx.Response(200, json={
            "/session/info": {"team_id": "t", "berth_id": "b"},
            "/session/signing_key": {"public_key": "ssh-ed25519 AAAA"},
        }[request.url.path])
    s, requests = session(respond)
    assert await s.session_info() == {"team_id": "t", "berth_id": "b"}
    assert await s.signing_public_key() == "ssh-ed25519 AAAA"
    assert [r.method for r in requests] == ["GET", "GET"]


async def test_hub_errors_raise_the_client_exceptions():
    s, _ = session(lambda r: httpx.Response(404, json={"detail": "no such session"}))
    with pytest.raises(SmallSeaNotFound):
        await s.session_peers()

    def refuse(request):
        raise httpx.ConnectError("connection refused")
    s, _ = session(refuse)
    with pytest.raises(SmallSeaHubUnavailable):
        await s.session_peers()


async def test_open_session_in_auto_approve_mode():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"token": TOKEN})

    http = httpx.AsyncClient(base_url="http://hub", transport=httpx.MockTransport(handler))
    s = await open_session(http, "p1", "App", "Team", "Client", mode="passthrough")
    assert s.token == TOKEN
    assert requests[0].url.path == "/sessions/request"
    assert json.loads(requests[0].content) == {
        "participant": "p1", "app": "App", "team": "Team", "client": "Client", "mode": "passthrough",
    }


async def test_open_session_refuses_a_pin_flow():
    http = httpx.AsyncClient(
        base_url="http://hub",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"pending_id": "x"})),
    )
    with pytest.raises(SmallSeaError, match="auto-approve"):
        await open_session(http, "p1", "App", "Team", "Client")


async def test_ensure_cloud_ready():
    s, requests = session(lambda r: httpx.Response(200, json={}))
    await s.ensure_cloud_ready()
    assert requests[0].method == "POST" and requests[0].url.path == "/cloud/setup"
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"


def hub(respond):
    requests = []

    def handler(request):
        requests.append(request)
        return respond(request)

    return httpx.AsyncClient(base_url="http://hub", transport=httpx.MockTransport(handler)), requests


async def test_start_session_in_auto_approve_mode_returns_a_session():
    http, requests = hub(lambda r: httpx.Response(200, json={"token": TOKEN}))
    s, pending_id = await start_session(http, "p1", "App", "Team", "Client")
    assert (s.token, pending_id) == (TOKEN, None)
    assert json.loads(requests[0].content) == {
        "participant": "p1", "app": "App", "team": "Team", "client": "Client", "mode": "encrypted",
    }


async def test_start_session_in_pin_mode_returns_the_pending_id():
    http, _ = hub(lambda r: httpx.Response(200, json={"pending_id": "pend-1"}))
    assert await start_session(http, "p1", "App", "Team", "Client") == (None, "pend-1")


async def test_confirm_session_sends_the_pin_and_returns_a_session():
    http, requests = hub(lambda r: httpx.Response(200, json=TOKEN))
    s = await confirm_session(http, "pend-1", "1234")
    assert s.token == TOKEN
    assert requests[0].url.path == "/sessions/confirm"
    assert json.loads(requests[0].content) == {"pending_id": "pend-1", "pin": "1234"}


async def test_resend_notification_names_the_pending_session():
    http, requests = hub(lambda r: httpx.Response(200, json={}))
    await resend_notification(http, "pend-1")
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/sessions/pend-1/resend-notification"

    http, _ = hub(lambda r: httpx.Response(404, json={"detail": "expired"}))
    with pytest.raises(SmallSeaNotFound):
        await resend_notification(http, "pend-1")


async def test_start_session_can_override_mode():
    http, requests = hub(lambda r: httpx.Response(200, json={"pending_id": "p2"}))
    await start_session(http, "alice", "MyApp", "MyTeam", "TestClient", mode="passthrough")
    assert json.loads(requests[0].content)["mode"] == "passthrough"


def bootstrap_required(reason="app_unknown", team="ProjectX"):
    return lambda r: httpx.Response(409, json={
        "error": "app_bootstrap_required",
        "reason": reason,
        "app": "SmallSeaCollectiveFiles",
        "team": team,
    })


@pytest.mark.parametrize("reason", [
    "app_unknown",
    "participant_berth_missing",
    "team_berth_missing",
    "app_friendly_name_ambiguous",
    "future_more_specific_reason",
])
async def test_app_bootstrap_required_preserves_reason_app_and_team(reason):
    http, _ = hub(bootstrap_required(reason))
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        await start_session(http, "alice", "SmallSeaCollectiveFiles", "ProjectX", "TestClient")
    assert (exc_info.value.reason, exc_info.value.app, exc_info.value.team) == (
        reason, "SmallSeaCollectiveFiles", "ProjectX",
    )


async def test_app_bootstrap_required_is_an_error_but_not_a_conflict():
    http, _ = hub(bootstrap_required())
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        await start_session(http, "alice", "SmallSeaCollectiveFiles", "ProjectX", "TestClient")
    assert isinstance(exc_info.value, SmallSeaError)
    assert not isinstance(exc_info.value, SmallSeaConflict)
    expected = (
        "SmallSeaCollectiveFiles isn't set up yet. "
        "Open Manager to register it for team ProjectX."
    )
    assert exc_info.value.user_message == expected
    assert str(exc_info.value) == expected


async def test_app_bootstrap_required_allows_no_team():
    http, _ = hub(bootstrap_required(team=None))
    with pytest.raises(SmallSeaAppBootstrapRequired) as exc_info:
        await start_session(http, "alice", "SmallSeaCollectiveFiles", "ProjectX", "TestClient")
    assert exc_info.value.team is None
    assert (
        exc_info.value.user_message
        == "SmallSeaCollectiveFiles isn't set up yet. Open Manager to register it."
    )


async def test_confirm_session_with_a_wrong_pin_raises():
    http, _ = hub(lambda r: httpx.Response(400, json={"detail": "Invalid PIN"}))
    with pytest.raises(SmallSeaError, match="Invalid PIN"):
        await confirm_session(http, "pending123", "0000")


async def test_start_session_reports_an_unreachable_hub():
    def refuse(request):
        raise httpx.ConnectError("refused")
    http, _ = hub(refuse)
    with pytest.raises(SmallSeaHubUnavailable):
        await start_session(http, "alice", "MyApp", "MyTeam", "TestClient")


@pytest.mark.parametrize("response", [
    httpx.Response(409, json={"detail": "CAS conflict: file was modified concurrently"}),
    httpx.Response(409, json={"error": "cas_conflict", "detail": "CAS conflict: modified"}),
    httpx.Response(409, text="CAS conflict"),
])
async def test_a_plain_conflict_raises_a_conflict_with_the_detail(response):
    http, _ = hub(lambda r: response)
    with pytest.raises(SmallSeaConflict, match="CAS conflict"):
        await start_session(http, "alice", "MyApp", "MyTeam", "TestClient")


async def test_a_server_error_without_json_uses_the_response_text():
    http, _ = hub(lambda r: httpx.Response(500, text="server sad"))
    with pytest.raises(SmallSeaError, match="HTTP 500: server sad"):
        await start_session(http, "alice", "MyApp", "MyTeam", "TestClient")


async def test_clear_app_sighting_sends_the_tuple_verbatim():
    """The Hub compares last_seen_at as a string, so it must not be reformatted."""
    s, requests = session(lambda r: httpx.Response(200, json={"deleted_count": 1}))
    deleted = await s.clear_app_sighting(
        app_name="SmallSeaCollectiveFiles",
        team_name="ProjectX",
        client_name="ssc-files:default",
        last_seen_at="2026-05-01T12:00:00.000000+00:00",
    )
    assert deleted == 1
    assert requests[0].url.path == "/sightings/clear"
    assert json.loads(requests[0].content) == {
        "app_name": "SmallSeaCollectiveFiles",
        "team_name": "ProjectX",
        "client_name": "ssc-files:default",
        "last_seen_at": "2026-05-01T12:00:00.000000+00:00",
    }
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"


async def test_clear_app_sighting_returns_zero_when_nothing_matched():
    s, _ = session(lambda r: httpx.Response(200, json={"deleted_count": 0}))
    assert await s.clear_app_sighting(
        app_name="SmallSeaCollectiveFiles",
        team_name="ProjectX",
        client_name="cli",
        last_seen_at="2026-05-01T12:00:00.000000+00:00",
    ) == 0


async def test_prune_stale_app_sightings_returns_the_count():
    s, requests = session(lambda r: httpx.Response(200, json={"pruned_count": 3}))
    assert await s.prune_stale_app_sightings() == 3
    assert requests[0].url.path == "/sightings/prune-stale"
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"


async def test_async_transport_for_an_injected_client():
    seen = []
    mock = httpx.MockTransport(lambda r: seen.append(r.url.path) or httpx.Response(200))
    async with httpx.AsyncClient(
        base_url="http://hub", transport=async_transport_for(httpx.Client(transport=mock), "http://hub")
    ) as http:
        await http.get("/x")
    assert seen == ["/x"]
    plain = httpx.Client(base_url="http://127.0.0.1:54001")
    assert async_transport_for(plain, "http://127.0.0.1:54001") is None
    assert async_transport_for(None, "http://hub") is None

    class _TestClientLike:
        app = object()

    assert isinstance(async_transport_for(_TestClientLike(), "http://hub"), httpx.ASGITransport)


async def test_closing_the_async_client_leaves_the_injected_transport_open():
    class _Stateful(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True

    mock = _Stateful(lambda r: httpx.Response(200))
    injected = httpx.Client(base_url="http://hub", transport=mock)
    async with httpx.AsyncClient(
        base_url="http://hub", transport=async_transport_for(injected, "http://hub")
    ) as http:
        assert (await http.get("/x")).status_code == 200
    assert not mock.closed


async def test_async_transport_for_uses_the_transport_mounted_for_the_hub():
    seen = []
    mock = httpx.MockTransport(lambda r: seen.append(r.url.path) or httpx.Response(200))
    injected = httpx.Client(mounts={"http://hub": mock})
    async with httpx.AsyncClient(
        base_url="http://hub", transport=async_transport_for(injected, "http://hub")
    ) as http:
        await http.get("/x")
    assert seen == ["/x"]
