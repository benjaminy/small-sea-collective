# Top Matter

import os
import pathlib
import shutil
import subprocess
import tempfile
import time

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


@pytest.fixture(autouse=True)
def reset_hub_app_state():
    """Reset Hub app global state after each test to prevent cross-test contamination."""
    from small_sea_hub.server import app
    yield
    app.state.auto_approve_sessions = False


@pytest.fixture()
def playground_dir():
    dir_name = tempfile.mkdtemp()

    yield dir_name

    try:
        shutil.rmtree(dir_name)
    except FileNotFoundError:
        print(f"Temp directory disappeared ({dir_name})")


@pytest.fixture(scope="session")
def minio_server_gen():
    servers = []

    def start_server(root_dir=None, port=9000):
        root_dir_created = False
        if root_dir is None:
            root_dir = tempfile.mkdtemp()
            root_dir_created = True
        env = os.environ.copy()
        env["MINIO_ROOT_USER"] = "minioadmin"
        env["MINIO_ROOT_PASSWORD"] = "minioadmin"
        proc = subprocess.Popen(
            [
                "minio",
                "server",
                root_dir,
                "--address",
                f":{port}",
                "--console-address",
                f":{port + 1}",
            ],
            env=env,
        )
        servers.append(
            {
                "proc": proc,
                "root_dir": root_dir,
                "root_created": root_dir_created,
            }
        )
        time.sleep(2)
        if proc.poll() is not None:
            raise RuntimeError(f"MinIO exited early (code {proc.returncode})")

        return {
            "port": port,
            "endpoint": f"http://localhost:{port}",
            "access_key": "minioadmin",
            "secret_key": "minioadmin",
        }

    yield start_server

    for server in servers:
        server["proc"].terminate()
        server["proc"].wait()
        if server["root_created"]:
            try:
                shutil.rmtree(server["root_dir"])
            except FileNotFoundError:
                print(f"Temp directory disappeared ({server['root_dir']})")


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
