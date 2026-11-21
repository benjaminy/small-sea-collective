"""Micro tests for the async Hub session in small_sea_client.aio."""

import json

import httpx
import pytest

from small_sea_client.aio import SmallSeaSession, open_session
from small_sea_client.client import SmallSeaError, SmallSeaHubUnavailable, SmallSeaNotFound

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


async def test_errors_are_the_sync_clients():
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
