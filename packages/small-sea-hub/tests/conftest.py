# Top Matter

import os
import pathlib
import shutil
import subprocess
import tempfile
import time

import pytest

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

    port = 9090
    container_name = f"ntfy-test-{os.getpid()}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            f"{port}:80",
            "binwiederhier/ntfy",
            "serve",
        ],
        check=True,
    )
    url = f"http://localhost:{port}"

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
