# Top Matter
#
# Minimal fixture support for the #238 research probes. These probes are not
# micro tests: they run against current code, assert a defect, and fix nothing.
# The repository's pytest `pythonpath = ["."]` makes `test_support` importable
# from the repo root.

import shutil
import tempfile

import pytest

from test_support import minio_server_gen  # noqa: F401  (pytest fixture)


@pytest.fixture()
def playground_dir():
    dir_name = tempfile.mkdtemp()
    yield dir_name
    shutil.rmtree(dir_name, ignore_errors=True)
