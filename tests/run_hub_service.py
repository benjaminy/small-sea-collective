# Top Matter

import pytest

import tempfile
import pathlib

@pytest.fixture
def hub_launcher():
    hubs = []

    def make_hub(
            port=11437,
            root_path=None):
        """
        """
        sdf
        if root_path is None:
            root_path = pathlib.Path( tempfile.mkdtemp() )

        cmd = ["uv", "run", "fastapi", "dev", "../Source/small_sea_local_hub.py", "--port", str(port)]

    yield make_hub()

    for hub in hubs

def tester4(hub_launcher):
