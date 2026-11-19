import shutil
import tempfile

import pytest

from test_support import minio_server_gen  # noqa: F401  (pytest fixture)


@pytest.fixture
def scratch_dir():
    d = tempfile.mkdtemp(prefix="cod-sync-test-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def playground_dir():
    dir_name = tempfile.mkdtemp()
    yield dir_name
    try:
        shutil.rmtree(dir_name)
    except FileNotFoundError:
        print(f"Temp directory disappeared ({dir_name})")


@pytest.fixture(params=["sync", "aio"])
def both_cod_syncs(request, monkeypatch):
    """Run a test once with the sync CodSync and once with the async one."""
    import cod_sync_test_helpers

    monkeypatch.setattr(cod_sync_test_helpers, "COD_SYNC_FLAVOR", request.param)
    return request.param


@pytest.fixture
def anyio_backend():
    """Async tests run on asyncio, which Cod Sync's async modules use directly."""
    return "asyncio"
