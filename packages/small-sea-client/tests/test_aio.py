"""Micro tests for the async Hub session in small_sea_client.aio."""

import json

import httpx
import pytest

from small_sea_client.aio import SmallSeaSession
from small_sea_client.client import SmallSeaHubUnavailable, SmallSeaNotFound

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


async def test_errors_are_the_sync_clients():
    s, _ = session(lambda r: httpx.Response(404, json={"detail": "no such session"}))
    with pytest.raises(SmallSeaNotFound):
        await s.session_peers()

    def refuse(request):
        raise httpx.ConnectError("connection refused")
    s, _ = session(refuse)
    with pytest.raises(SmallSeaHubUnavailable):
        await s.session_peers()
