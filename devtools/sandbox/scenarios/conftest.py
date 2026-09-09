"""Local service and temporary workspace fixtures for sandbox scenarios."""

import pytest

from test_support import minio_server_gen  # noqa: F401


@pytest.fixture
def playground_dir(tmp_path):
    return tmp_path
