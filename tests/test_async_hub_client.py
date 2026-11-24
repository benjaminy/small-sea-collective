"""Micro tests for async_hub_client in test_support.py."""

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from test_support import async_hub_client


def test_async_requests_reach_the_test_clients_configured_hub():
    app = FastAPI()

    @app.get("/host")
    def host(request: Request):
        return {"host": request.headers["host"]}

    test_client = TestClient(app, base_url="http://alternate-hub")

    async def ask():
        return (await async_hub_client(test_client).get("/host")).json()

    assert test_client.get("/host").json() == {"host": "alternate-hub"}
    assert anyio.run(ask) == {"host": "alternate-hub"}


def test_a_transport_mounted_for_the_configured_hub_is_used():
    seen = []
    mock = httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(200))
    sync_client = httpx.Client(
        base_url="http://alternate-hub", mounts={"http://alternate-hub": mock}
    )

    async def ask():
        await async_hub_client(sync_client).get("/x")

    anyio.run(ask)
    assert seen == ["http://alternate-hub/x"]


def test_a_client_without_a_base_url_reaches_the_default_test_hub():
    seen = []
    mock = httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(200))
    sync_client = httpx.Client(transport=mock)

    async def ask():
        await async_hub_client(sync_client).get("/x")

    anyio.run(ask)
    assert seen == ["http://testserver/x"]
