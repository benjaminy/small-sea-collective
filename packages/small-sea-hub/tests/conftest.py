# Top Matter

import os
import pathlib
import shutil
import subprocess
import tempfile
import time

import pytest

from test_support import reserve_ports
from test_support import minio_server_gen  # noqa: F401  (pytest fixture)


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


@pytest.fixture(scope="session")
def ntfy_server():
    import httpx

    (port,) = reserve_ports([None])
    container_name = f"ntfy-test-{os.getpid()}"

    # A container already holding this name is not ours to delete: the teardown
    # below removes the one we start, so a survivor came from an earlier run that
    # crashed and happened to share our pid.  Fail rather than destroy it.
    existing = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{container_name}$"],
        capture_output=True,
        text=True,
        check=True,
    )
    if existing.stdout.strip():
        raise RuntimeError(
            f"Container {container_name} already exists; remove it and rerun"
        )

    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "-p",
                f"127.0.0.1:{port}:80",
                "binwiederhier/ntfy",
                "serve",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        # A `docker run` that fails to start the container still leaves it created.
        subprocess.run(["docker", "rm", "-f", container_name])
        raise

    url = f"http://127.0.0.1:{port}"

    # Health check — wait up to 15 seconds
    for _ in range(30):
        time.sleep(0.5)
        try:
            resp = httpx.get(f"{url}/v1/health", timeout=2)
            if resp.status_code == 200:
                break
        except Exception:
            pass
    else:
        subprocess.run(["docker", "rm", "-f", container_name])
        raise RuntimeError("ntfy server failed to start")

    yield {"port": port, "url": url}

    subprocess.run(["docker", "rm", "-f", container_name])
