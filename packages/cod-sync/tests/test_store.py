"""Micro tests for the store contract.

Two properties carry the weight here. Absence must be exact, because it is the
only failure Cod Sync reads as "empty store" or "missing predecessor" — a
provider outage reported as absence would look like a chain that never
existed. And archived objects must be write-once, because a link's predecessor
pointer is only meaningful if the bytes it names cannot change afterwards.
"""

import pathlib

import pytest

from cod_sync.aio.store import LocalFolderStore
from cod_sync.store import (
    LATEST_LINK_PATH,
    CasConflictError,
    ObjectNotFoundError,
    StoreError,
    StoreProviderError,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def store(scratch_dir):
    return LocalFolderStore(scratch_dir)


def write_bundle(scratch_dir, name="b.bundle", content=b"BUNDLEBYTES"):
    path = pathlib.Path(scratch_dir) / name
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------- absence #


async def test_missing_head_is_absence(store):
    with pytest.raises(ObjectNotFoundError) as exc:
        await store.get_latest_link()
    assert exc.value.path == LATEST_LINK_PATH


async def test_missing_archived_link_is_absence(store):
    with pytest.raises(ObjectNotFoundError):
        await store.get_link("nope")


async def test_missing_bundle_is_absence(store, scratch_dir):
    with pytest.raises(ObjectNotFoundError):
        await store.download_bundle("nope", pathlib.Path(scratch_dir) / "out.bundle")


async def test_a_directory_that_is_not_a_store_is_not_absence(scratch_dir):
    with pytest.raises(StoreError):
        LocalFolderStore(pathlib.Path(scratch_dir) / "does-not-exist")


# -------------------------------------------------------------- write-once #


async def test_archived_link_is_write_once(store):
    await store.put_link("L1", b"first")
    with pytest.raises(CasConflictError):
        await store.put_link("L1", b"second")
    assert await store.get_link("L1") == b"first"


async def test_bundle_is_write_once(store, scratch_dir):
    original = write_bundle(scratch_dir, "a.bundle", b"ORIGINAL")
    replacement = write_bundle(scratch_dir, "b.bundle", b"REPLACEMENT")
    await store.put_bundle("B1", original)
    with pytest.raises(CasConflictError):
        await store.put_bundle("B1", replacement)

    out = pathlib.Path(scratch_dir) / "out.bundle"
    await store.download_bundle("B1", out)
    assert out.read_bytes() == b"ORIGINAL"


# --------------------------------------------------------------------- CAS #


async def test_first_head_write_is_create_only(store):
    await store.put_latest_link(b"one", expected_etag=None)
    with pytest.raises(CasConflictError):
        await store.put_latest_link(b"two", expected_etag=None)
    assert (await store.get_latest_link())[0] == b"one"


async def test_matching_etag_advances_the_head(store):
    await store.put_latest_link(b"one", expected_etag=None)
    _data, etag = await store.get_latest_link()
    await store.put_latest_link(b"two", expected_etag=etag)
    data, new_etag = await store.get_latest_link()
    assert data == b"two"
    assert new_etag != etag


async def test_stale_etag_is_a_conflict(store):
    await store.put_latest_link(b"one", expected_etag=None)
    _data, stale = await store.get_latest_link()
    await store.put_latest_link(b"two", expected_etag=stale)
    with pytest.raises(CasConflictError):
        await store.put_latest_link(b"three", expected_etag=stale)
    assert (await store.get_latest_link())[0] == b"two"


async def test_etag_write_without_an_existing_head_is_a_conflict(store):
    with pytest.raises(CasConflictError):
        await store.put_latest_link(b"one", expected_etag="deadbeef")


async def test_head_replacement_leaves_no_partial_file(store):
    await store.put_latest_link(b"one", expected_etag=None)
    _data, etag = await store.get_latest_link()
    await store.put_latest_link(b"a much longer set of link bytes", expected_etag=etag)
    assert (await store.get_latest_link())[0] == b"a much longer set of link bytes"
    # The replacement is a rename, so no temporary file survives it.
    leftovers = [p.name for p in pathlib.Path(store.path).glob(".latest-link-*")]
    assert leftovers == []


async def test_initial_head_is_never_visible_half_written(store, monkeypatch):
    """A reader sees exact absence or a whole head, never staging bytes.

    provisioning builds this store directly for the identity bootstrap, so a
    partially visible NoteToSelf head is developer data at risk rather than
    test-fixture untidiness.
    """
    import os

    seen = []
    real_link = os.link

    def watch(source, target):
        # At the instant before the name exists, the store must hold nothing.
        seen.append(pathlib.Path(target).exists())
        return real_link(source, target)

    monkeypatch.setattr(os, "link", watch)
    await store.put_latest_link(b"a complete first head", expected_etag=None)

    assert seen == [False]
    assert (await store.get_latest_link())[0] == b"a complete first head"
    assert [p.name for p in pathlib.Path(store.path).glob(".latest-link-*")] == []


async def test_a_failed_initial_head_write_leaves_exact_absence(store, monkeypatch):
    import os

    def fail(source, target):
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(StoreProviderError) as exc:
        await store.put_latest_link(b"one", expected_etag=None)

    assert exc.value.write_closed is True
    with pytest.raises(ObjectNotFoundError):
        await store.get_latest_link()
    assert [p.name for p in pathlib.Path(store.path).glob(".latest-link-*")] == []


async def test_initial_head_setup_failure_is_closed(store, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("cannot create a staging file")

    monkeypatch.setattr("cod_sync.store.tempfile.mkstemp", fail)
    with pytest.raises(StoreProviderError) as exc:
        await store.put_latest_link(b"one", expected_etag=None)

    assert exc.value.write_closed is True
    with pytest.raises(ObjectNotFoundError):
        await store.get_latest_link()


async def test_staging_cleanup_failure_does_not_hide_an_installed_head(
    store, monkeypatch
):
    import os

    def fail(path):
        raise OSError("cannot remove staging name")

    monkeypatch.setattr(os, "unlink", fail)
    etag = await store.put_latest_link(b"one", expected_etag=None)

    assert etag
    assert (await store.get_latest_link())[0] == b"one"


async def test_a_returned_local_head_write_failure_is_closed(store, monkeypatch):
    """A call that has returned leaves no request that can take effect later.

    Cod Sync reasons against the store protocol, so conclusiveness has to
    arrive as a typed marker on the failure rather than as a store identity it
    is not allowed to inspect.
    """
    import os

    await store.put_latest_link(b"one", expected_etag=None)
    _data, etag = await store.get_latest_link()

    def fail(source, target):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(StoreProviderError) as exc:
        await store.put_latest_link(b"two", expected_etag=etag)

    assert exc.value.write_closed is True
    assert (await store.get_latest_link())[0] == b"one"
    assert CasConflictError.write_closed is True
    assert StoreProviderError("plain").write_closed is False


# ------------------------------------------- testing stores, same contract #


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen()


def s3_store(minio, bucket):
    from cod_sync.testing import S3Store

    return S3Store(minio["endpoint"], bucket, minio["access_key"], minio["secret_key"])


async def test_s3_store_enforces_write_once(minio, scratch_dir):
    """A testing store that accepted an etag without enforcing it would make
    every test that uses it prove nothing."""
    store = s3_store(minio, "cod-sync-write-once")

    await store.put_link("L1", b"first")
    with pytest.raises(CasConflictError):
        await store.put_link("L1", b"second")
    assert await store.get_link("L1") == b"first"

    original = write_bundle(scratch_dir, "orig.bundle", b"ORIGINAL")
    replacement = write_bundle(scratch_dir, "repl.bundle", b"REPLACEMENT")
    await store.put_bundle("B1", original)
    with pytest.raises(CasConflictError):
        await store.put_bundle("B1", replacement)

    out = pathlib.Path(scratch_dir) / "out.bundle"
    await store.download_bundle("B1", out)
    assert out.read_bytes() == b"ORIGINAL"


async def test_s3_store_head_is_create_only_then_cas(minio):
    store = s3_store(minio, "cod-sync-head-cas")

    with pytest.raises(ObjectNotFoundError):
        await store.get_latest_link()

    await store.put_latest_link(b"one", expected_etag=None)
    with pytest.raises(CasConflictError):
        await store.put_latest_link(b"two", expected_etag=None)

    _data, etag = await store.get_latest_link()
    await store.put_latest_link(b"two", expected_etag=etag)
    assert (await store.get_latest_link())[0] == b"two"

    with pytest.raises(CasConflictError):
        await store.put_latest_link(b"three", expected_etag=etag)
