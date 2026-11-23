"""Micro tests for cod_sync.aio.store.

The Hub stores get a real httpx.AsyncClient whose transport answers in
process, so the status classification and streaming code run as they would
against a Hub.
"""

import json
import pathlib

import httpx
import pytest

from cod_sync.aio.store import (
    CandidateInspectionStore,
    ExplicitProxyStore,
    LocalFolderStore,
    PeerSmallSeaStore,
    SmallSeaStore,
)
from cod_sync.store import (
    CasConflictError,
    DownloadTooLargeError,
    MalformedStoreResponseError,
    ObjectNotFoundError,
    PeerSenderKeyUnavailableError,
    PeerStorageUnknownError,
    PublicationOutcomeUnknownError,
    StoreAuthenticationError,
    StoreAuthorizationError,
    StoreProviderError,
    StoreTransportError,
    WriteNeverAppliedError,
    bundle_path,
    link_path,
)

pytestmark = pytest.mark.anyio

OCTETS = {"content-type": "application/octet-stream"}


def hub(respond):
    """An AsyncClient whose every request goes to respond(request), recorded in .requests."""
    requests = []

    def handler(request):
        requests.append(request)
        return respond(request)

    client = httpx.AsyncClient(base_url="http://hub", transport=httpx.MockTransport(handler))
    client.requests = requests
    return client


def answer(status, body=None, **kwargs):
    return lambda request: httpx.Response(status, json=body, **kwargs)


async def test_a_download_returns_bytes_and_etag():
    client = hub(lambda r: httpx.Response(200, headers={**OCTETS, "etag": "e1"}, content=b"link"))
    store = SmallSeaStore("session", client, path_prefix="cal/")
    assert await store.get_latest_link() == (b"link", "e1")
    assert client.requests[0].url.path == "/cloud_file"
    assert client.requests[0].url.params["path"] == "cal/latest-link.yaml"
    assert client.requests[0].headers["authorization"] == "Bearer session"


@pytest.mark.parametrize("status,expected", [
    (404, ObjectNotFoundError),
    (401, StoreAuthenticationError),
    (403, StoreAuthorizationError),
    (502, StoreProviderError),
    (500, StoreProviderError),
])
async def test_hub_status_becomes_a_distinct_error(status, expected):
    store = SmallSeaStore("session", hub(answer(status, {"detail": "nope"})))
    with pytest.raises(expected):
        await store.get_latest_link()


async def test_a_failed_request_is_not_absence():
    def refuse(request):
        raise httpx.ConnectError("connection refused")
    with pytest.raises(StoreTransportError):
        await SmallSeaStore("session", hub(refuse)).get_latest_link()


async def test_an_unreadable_success_is_not_absence():
    store = SmallSeaStore("session", hub(answer(200, {"nothing": "useful"})))
    with pytest.raises(MalformedStoreResponseError):
        await store.get_latest_link()


async def test_download_refuses_object_over_ceiling_by_content_length():
    client = hub(lambda r: httpx.Response(200, headers={**OCTETS, "content-length": "9"}, content=b"123456789"))
    store = SmallSeaStore("session", client, max_download_bytes=8)
    with pytest.raises(DownloadTooLargeError) as exc:
        await store.get_link("large")
    assert exc.value.path == link_path("large")


async def test_download_refuses_stream_that_exceeds_ceiling_without_content_length():
    async def chunks():
        for chunk in (b"1234", b"5678", b"9"):
            yield chunk
    store = SmallSeaStore("session", hub(lambda r: httpx.Response(200, headers=OCTETS, content=chunks())),
                          max_download_bytes=8)
    with pytest.raises(DownloadTooLargeError):
        await store.get_link("large")


async def test_truncated_stream_is_transport_error_not_absence():
    async def chunks():
        yield b"short"
    client = hub(lambda r: httpx.Response(200, headers={**OCTETS, "content-length": "10"}, content=chunks()))
    with pytest.raises(StoreTransportError):
        await SmallSeaStore("session", client).get_link("truncated")


async def test_download_bundle_streams_to_local_path(tmp_path):
    async def chunks():
        for chunk in (b"first", b" second"):
            yield chunk
    store = SmallSeaStore("session", hub(lambda r: httpx.Response(200, headers=OCTETS, content=chunks())))
    await store.download_bundle("B1", tmp_path / "bundle")
    assert (tmp_path / "bundle").read_bytes() == b"first second"


async def test_hub_writes_are_create_only_except_the_head_cas(tmp_path):
    client = hub(answer(200, {"etag": "e1"}))
    store = SmallSeaStore("session", client)
    (tmp_path / "b.bundle").write_bytes(b"BUNDLE")
    await store.put_link("L1", b"bytes")
    await store.put_bundle("B1", tmp_path / "b.bundle")
    assert await store.put_latest_link(b"bytes", expected_etag=None) == "e1"
    await store.put_latest_link(b"more", expected_etag="e1")

    sent = [json.loads(r.content) for r in client.requests]
    assert [s["path"] for s in sent[:2]] == [link_path("L1"), bundle_path("B1")]
    assert [s["expected_etag"] for s in sent] == ["*", "*", "*", "e1"]
    assert [s.get("notify", False) for s in sent] == [False, False, True, True]


async def test_a_lost_head_response_is_reported_as_unknown():
    def drop(request):
        raise httpx.ReadError("connection reset by peer")
    with pytest.raises(PublicationOutcomeUnknownError) as exc:
        await SmallSeaStore("session", hub(drop)).put_latest_link(b"bytes", expected_etag="e1", link_uid="L9")
    assert (exc.value.expected_etag, exc.value.link_uid) == ("e1", "L9")


async def test_a_head_write_conflict_is_a_conflict_not_an_unknown_outcome():
    store = SmallSeaStore("session", hub(answer(409, {"error": "cas_conflict", "detail": "CAS conflict"})))
    with pytest.raises(CasConflictError):
        await store.put_latest_link(b"bytes", expected_etag="e1")


async def test_a_never_applied_write_is_closed():
    store = SmallSeaStore("session", hub(answer(502, {"error": "write_never_applied", "detail": "offline"})))
    with pytest.raises(WriteNeverAppliedError) as exc:
        await store.put_latest_link(b"bytes", expected_etag="e1")
    assert exc.value.write_closed is True


@pytest.mark.parametrize("error_code,expected", [
    ("peer_storage_unknown", PeerStorageUnknownError),
    ("sender_key_unavailable", PeerSenderKeyUnavailableError),
])
async def test_a_peer_prerequisite_409_is_not_a_provider_failure(error_code, expected):
    store = PeerSmallSeaStore("session", "aa" * 16, hub(answer(409, {"error": error_code, "detail": "later"})))
    with pytest.raises(expected) as exc:
        await store.get_latest_link()
    assert not isinstance(exc.value, StoreProviderError)


async def test_peer_reads_go_to_the_peer_endpoint():
    client = hub(answer(404, {"detail": "nope"}))
    with pytest.raises(ObjectNotFoundError):
        await PeerSmallSeaStore("session", "aa" * 16, client).get_latest_link()
    assert client.requests[0].url.path == "/peer_cloud_file"
    assert client.requests[0].url.params["teammate_id"] == "aa" * 16


async def test_candidate_reads_carry_the_candidate_key():
    client = hub(answer(404, {"detail": "nope"}))
    with pytest.raises(ObjectNotFoundError):
        await CandidateInspectionStore("session", "candidate", client).get_latest_link()
    assert client.requests[0].url.path == "/berth_source/inspect"
    assert client.requests[0].url.params["candidate_key"] == "candidate"
    assert client.requests[0].url.params["path"] == "latest-link.yaml"


async def test_explicit_proxy_reads_transform_links_and_bundles(tmp_path):
    client = hub(lambda r: httpx.Response(200, headers=OCTETS, content=b"sealed"))
    store = ExplicitProxyStore(
        "session", "s3", "http://minio", "bucket", client,
        download_transform=lambda data: data.upper(),
    )
    assert (await store.get_latest_link())[0] == b"SEALED"
    await store.download_bundle("b1", tmp_path / "bundle")
    assert (tmp_path / "bundle").read_bytes() == b"SEALED"
    first = client.requests[0]
    assert first.url.path == "/cloud_proxy"
    assert dict(first.url.params) == {
        "protocol": "s3", "url": "http://minio", "bucket": "bucket",
        "path": "latest-link.yaml",
    }
    assert client.requests[1].url.params["path"] == bundle_path("b1")


async def test_local_folder_store_keeps_the_sync_contract(tmp_path):
    store = LocalFolderStore(tmp_path)
    with pytest.raises(ObjectNotFoundError):
        await store.get_latest_link()
    etag = await store.put_latest_link(b"one", expected_etag=None)
    with pytest.raises(CasConflictError):
        await store.put_latest_link(b"two", expected_etag=None)
    await store.put_latest_link(b"two", expected_etag=etag)
    assert (await store.get_latest_link())[0] == b"two"
    assert pathlib.Path(store.path) == tmp_path
