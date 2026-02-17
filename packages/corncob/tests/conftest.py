import shutil
import tempfile

import pytest


@pytest.fixture
def scratch_dir():
    d = tempfile.mkdtemp(prefix="corncob-test-")
    yield d
    shutil.rmtree(d, ignore_errors=True)
