"""Keep disposable provisioning repos independent of personal Git settings."""

import os
from pathlib import Path
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Publication experiment")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "experiment@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Publication experiment")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "experiment@example.invalid")
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
