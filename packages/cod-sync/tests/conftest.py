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
