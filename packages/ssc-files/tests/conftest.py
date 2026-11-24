import dataclasses
import os
import pathlib
import shutil
import tempfile

import pytest

from test_support import minio_server_gen  # noqa: F401  (pytest fixture)
from test_support import local_files_signer


@pytest.fixture
def test_signer():
    return local_files_signer


@pytest.fixture(autouse=True)
def in_process_hub_signer(request, monkeypatch):
    """In-process Hub tests use local Git signing; the shim needs an HTTP port."""
    if request.module.__name__.split(".")[-1] not in {
        "test_sync", "test_hub_sync", "test_web_sync",
    }:
        return
    from small_sea_client import client as hub_client
    from ssc_files import files, sync

    async def signer(team_name, hub_port=11437, *, _http_client=None):
        if request.module.__name__.split(".")[-1] == "test_sync":
            return local_files_signer(request.module.TEAM)
        token = sync.cached_session_token(team_name)
        async with sync.hub_http(hub_port, _http_client) as http:
            info = await hub_client.SmallSeaSession(http, token).session_info()
        return dataclasses.replace(
            local_files_signer(files.materialization_context_from_session_info(info)),
            session_token=token,
        )

    monkeypatch.setattr(sync, "commit_signer", signer)


@pytest.fixture(autouse=True)
def safe_cwd():
    """Ensure each test starts and ends with a valid working directory."""
    safe = pathlib.Path(__file__).parent
    os.chdir(safe)
    yield
    try:
        os.chdir(safe)
    except OSError:
        pass


@pytest.fixture()
def playground_dir():
    dir_name = tempfile.mkdtemp()
    yield dir_name
    try:
        shutil.rmtree(dir_name)
    except FileNotFoundError:
        print(f"Temp directory disappeared ({dir_name})")
