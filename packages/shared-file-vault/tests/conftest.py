import os
import pathlib
import shutil
import tempfile

import pytest


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
