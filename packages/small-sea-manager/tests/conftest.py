# Top Matter

import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time

import pytest


@pytest.fixture(autouse=True)
def safe_cwd():
    """Ensure each test starts and ends with a valid working directory.

    Prevents cwd contamination when a test's temp dir is deleted while still
    the active cwd, which would cause os.getcwd() to fail in subsequent tests.
    """
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


def _free_ports(count):
    """Reserve distinct ephemeral TCP ports and return their numbers.

    All sockets stay bound until every port has been selected, so the returned
    ports cannot collide with each other. They are closed before MinIO binds,
    so allocation against other processes remains advisory.
    """
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


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
        if port is None:
            # Two distinctly allocated ports: deriving the console port as
            # port + 1 collides with the next server's API port.
            port, console_port = _free_ports(2)
        else:
            console_port = port + 1
        proc = subprocess.Popen(
            [
                "minio",
                "server",
                root_dir,
                "--address",
                f":{port}",
                "--console-address",
                f":{console_port}",
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
