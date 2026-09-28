import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_background_git_maintenance():
    """Keep Git from writing objects after a test has finished.

    Automatic gc and maintenance can run in the background after a git
    command returns, and then race with fixtures deleting their temporary
    repositories ("Directory not empty" at teardown).
    """
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_COUNT",) + tuple(
        f"GIT_CONFIG_{part}_{i}" for i in range(2) for part in ("KEY", "VALUE"))}
    os.environ["GIT_CONFIG_COUNT"] = "2"
    os.environ["GIT_CONFIG_KEY_0"], os.environ["GIT_CONFIG_VALUE_0"] = "gc.auto", "0"
    os.environ["GIT_CONFIG_KEY_1"], os.environ["GIT_CONFIG_VALUE_1"] = "maintenance.auto", "false"
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
