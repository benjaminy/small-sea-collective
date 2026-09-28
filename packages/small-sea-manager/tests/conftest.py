# Top Matter

import os
import pathlib
import shutil
import tempfile

import pytest

from test_support import minio_server_gen  # noqa: F401  (pytest fixture)


@pytest.fixture(autouse=True)
def safe_cwd():
    """Ensure each test starts and ends with a valid working directory.

    Prevents cwd contamination when a test's temp dir is deleted while still
    the active cwd, which would cause os.getcwd() to fail in subsequent tests.
    """
    safe = pathlib.Path(__file__).parent
    os.chdir(safe)
    yield
    try:
        os.chdir(safe)
    except OSError:
        pass


@pytest.fixture()
def playground_dir(monkeypatch):
    # Git's automatic maintenance can keep writing pack files after a test
    # finishes, racing with removal of its temporary repositories.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "gc.auto")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "0")
    dir_name = tempfile.mkdtemp()

    yield dir_name

    try:
        shutil.rmtree(dir_name)
    except FileNotFoundError:
        print(f"Temp directory disappeared ({dir_name})")
