"""Micro tests for the store contract.

Two properties carry the weight here. Absence must be exact, because it is the
only failure Cod Sync reads as "empty store" or "missing predecessor" — a
provider outage reported as absence would look like a chain that never
existed. And archived objects must be write-once, because a link's predecessor
pointer is only meaningful if the bytes it names cannot change afterwards.
"""

import pathlib

import pytest

from cod_sync.store import (
    LATEST_LINK_PATH,
    CasConflictError,
    LocalFolderStore,
    MalformedStoreResponseError,
    ObjectNotFoundError,
    SmallSeaStore,
    StoreAuthenticationError,
    StoreAuthorizationError,
    StoreError,
    StoreProviderError,
    StoreTransportError,
    bundle_path,
    link_path,
)


@pytest.fixture
def store(scratch_dir):
    return LocalFolderStore(scratch_dir)


def write_bundle(scratch_dir, name="b.bundle", content=b"BUNDLEBYTES"):
    path = pathlib.Path(scratch_dir) / name
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------- absence #


def test_missing_head_is_absence(store):
    with pytest.raises(ObjectNotFoundError) as exc:
        store.get_latest_link()
    assert exc.value.path == LATEST_LINK_PATH


def test_missing_archived_link_is_absence(store):
    with pytest.raises(ObjectNotFoundError):
        store.get_link("nope")


def test_missing_bundle_is_absence(store, scratch_dir):
    with pytest.raises(ObjectNotFoundError):
        store.download_bundle("nope", pathlib.Path(scratch_dir) / "out.bundle")


def test_a_directory_that_is_not_a_store_is_not_absence(scratch_dir):
    with pytest.raises(StoreError):
        LocalFolderStore(pathlib.Path(scratch_dir) / "does-not-exist")


# -------------------------------------------------------------- write-once #


def test_archived_link_is_write_once(store):
    store.put_link("L1", b"first")
    with pytest.raises(CasConflictError):
        store.put_link("L1", b"second")
    assert store.get_link("L1") == b"first"


def test_bundle_is_write_once(store, scratch_dir):
    original = write_bundle(scratch_dir, "a.bundle", b"ORIGINAL")
    replacement = write_bundle(scratch_dir, "b.bundle", b"REPLACEMENT")
    store.put_bundle("B1", original)
    with pytest.raises(CasConflictError):
        store.put_bundle("B1", replacement)

    out = pathlib.Path(scratch_dir) / "out.bundle"
    store.download_bundle("B1", out)
    assert out.read_bytes() == b"ORIGINAL"


# --------------------------------------------------------------------- CAS #


def test_first_head_write_is_create_only(store):
    store.put_latest_link(b"one", expected_etag=None)
    with pytest.raises(CasConflictError):
        store.put_latest_link(b"two", expected_etag=None)
    assert store.get_latest_link()[0] == b"one"


def test_matching_etag_advances_the_head(store):
    store.put_latest_link(b"one", expected_etag=None)
    _data, etag = store.get_latest_link()
    store.put_latest_link(b"two", expected_etag=etag)
    data, new_etag = store.get_latest_link()
    assert data == b"two"
    assert new_etag != etag


def test_stale_etag_is_a_conflict(store):
    store.put_latest_link(b"one", expected_etag=None)
    _data, stale = store.get_latest_link()
    store.put_latest_link(b"two", expected_etag=stale)
    with pytest.raises(CasConflictError):
        store.put_latest_link(b"three", expected_etag=stale)
    assert store.get_latest_link()[0] == b"two"


def test_etag_write_without_an_existing_head_is_a_conflict(store):
    with pytest.raises(CasConflictError):
        store.put_latest_link(b"one", expected_etag="deadbeef")


def test_head_replacement_leaves_no_partial_file(store):
    store.put_latest_link(b"one", expected_etag=None)
    _data, etag = store.get_latest_link()
    store.put_latest_link(b"a much longer set of link bytes", expected_etag=etag)
    assert store.get_latest_link()[0] == b"a much longer set of link bytes"
    # The replacement is a rename, so no temporary file survives it.
    leftovers = [p.name for p in pathlib.Path(store.path).glob(".latest-link-*")]
    assert leftovers == []


# ------------------------------------------------------- Hub status mapping #


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


class FakeHubClient:
    """Stands in for a Hub, answering every request with one canned response."""

    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.response

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.response


@pytest.mark.parametrize(
    "status,expected",
    [
        (404, ObjectNotFoundError),
        (401, StoreAuthenticationError),
        (403, StoreAuthorizationError),
        (502, StoreProviderError),
        (500, StoreProviderError),
    ],
)
def test_hub_status_becomes_a_distinct_error(status, expected):
    client = FakeHubClient(FakeResponse(status, {"detail": "nope"}))
    store = SmallSeaStore("session", client=client)
    with pytest.raises(expected):
        store.get_latest_link()


def test_a_failed_request_is_not_absence():
    client = FakeHubClient(raises=OSError("connection reset"))
    store = SmallSeaStore("session", client=client)
    with pytest.raises(StoreTransportError):
        store.get_latest_link()


def test_an_unreadable_success_is_not_absence():
    client = FakeHubClient(FakeResponse(200, {"nothing": "useful"}))
    store = SmallSeaStore("session", client=client)
    with pytest.raises(MalformedStoreResponseError):
        store.get_latest_link()


def test_hub_writes_are_create_only_except_the_head_cas(scratch_dir):
    client = FakeHubClient(FakeResponse(200, {"etag": "e1"}))
    store = SmallSeaStore("session", client=client)
    store.put_link("L1", b"bytes")
    store.put_bundle("B1", write_bundle(scratch_dir))
    store.put_latest_link(b"bytes", expected_etag=None)
    store.put_latest_link(b"more", expected_etag="e1")

    sent = [call[2]["json"] for call in client.calls]
    assert sent[0]["path"] == link_path("L1")
    assert sent[0]["expected_etag"] == "*"
    assert sent[1]["path"] == bundle_path("B1")
    assert sent[1]["expected_etag"] == "*"
    assert sent[2]["expected_etag"] == "*"  # first head: create-only
    assert sent[2]["notify"] is True
    assert sent[3]["expected_etag"] == "e1"


def test_a_lost_head_response_is_reported_as_unknown():
    from cod_sync.store import PublicationOutcomeUnknownError

    client = FakeHubClient(raises=OSError("connection reset by peer"))
    store = SmallSeaStore("session", client=client)
    with pytest.raises(PublicationOutcomeUnknownError) as exc:
        store.put_latest_link(b"bytes", expected_etag="e1", link_uid="L9")
    assert exc.value.expected_etag == "e1"
    assert exc.value.link_uid == "L9"


def test_a_head_write_conflict_is_a_conflict_not_an_unknown_outcome():
    client = FakeHubClient(
        FakeResponse(409, {"error": "cas_conflict", "detail": "CAS conflict"})
    )
    store = SmallSeaStore("session", client=client)
    with pytest.raises(CasConflictError):
        store.put_latest_link(b"bytes", expected_etag="e1")


def test_a_non_cas_409_is_a_provider_failure():
    client = FakeHubClient(
        FakeResponse(409, {"error": "peer_storage_unknown", "detail": "no route"})
    )
    store = SmallSeaStore("session", client=client)
    with pytest.raises(StoreProviderError):
        store.get_latest_link()


def test_path_prefix_namespaces_every_object():
    client = FakeHubClient(FakeResponse(404, {"detail": "nope"}))
    store = SmallSeaStore("session", client=client, path_prefix="files/registry/")
    with pytest.raises(ObjectNotFoundError):
        store.get_link("L1")
    assert client.calls[0][2]["params"]["path"] == "files/registry/" + link_path("L1")


# ------------------------------------------- testing stores, same contract #


MINIO_PORT_STORE = 9420


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=MINIO_PORT_STORE)


def s3_store(minio, bucket):
    from cod_sync.testing import S3Store

    return S3Store(minio["endpoint"], bucket, minio["access_key"], minio["secret_key"])


def test_s3_store_enforces_write_once(minio, scratch_dir):
    """A testing store that accepted an etag without enforcing it would make
    every test that uses it prove nothing."""
    store = s3_store(minio, "cod-sync-write-once")

    store.put_link("L1", b"first")
    with pytest.raises(CasConflictError):
        store.put_link("L1", b"second")
    assert store.get_link("L1") == b"first"

    original = write_bundle(scratch_dir, "orig.bundle", b"ORIGINAL")
    replacement = write_bundle(scratch_dir, "repl.bundle", b"REPLACEMENT")
    store.put_bundle("B1", original)
    with pytest.raises(CasConflictError):
        store.put_bundle("B1", replacement)

    out = pathlib.Path(scratch_dir) / "out.bundle"
    store.download_bundle("B1", out)
    assert out.read_bytes() == b"ORIGINAL"


def test_s3_store_head_is_create_only_then_cas(minio):
    store = s3_store(minio, "cod-sync-head-cas")

    with pytest.raises(ObjectNotFoundError):
        store.get_latest_link()

    store.put_latest_link(b"one", expected_etag=None)
    with pytest.raises(CasConflictError):
        store.put_latest_link(b"two", expected_etag=None)

    _data, etag = store.get_latest_link()
    store.put_latest_link(b"two", expected_etag=etag)
    assert store.get_latest_link()[0] == b"two"

    with pytest.raises(CasConflictError):
        store.put_latest_link(b"three", expected_etag=etag)
