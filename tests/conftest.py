# Top Matter

import os
import subprocess
import tempfile
import time
import pytest
import boto3
import shutil

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

    def start_server(
            root_dir=None,
            port=9000 ):
        root_dir_created = False
        if root_dir is None:
            root_dir = tempfile.mkdtemp()
            root_dir_created = True
        env = os.environ.copy()
        env["MINIO_ROOT_USER"] = "minioadmin"
        env["MINIO_ROOT_PASSWORD"] = "minioadmin"
        proc = subprocess.Popen([
            "minio", "server", root_dir, "--address", f":{port}", "--console-address", f":{port + 1}"
        ], env=env )
        servers.append({
            "proc": proc,
            "root_dir":root_dir,
            "root_created": root_dir_created,
        })
        time.sleep(2)
        if proc.poll() is not None:
            raise RuntimeError(f"MinIO exited early (code {proc.returncode})")

        return {
            "port"      : port,
            "endpoint"  : f"http://localhost:{port}",
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
def hub_server_gen():
    servers = []

    def start_server(
            root_dir=None,
            port=11437):
        root_dir_created = False
        if root_dir is None:
            root_dir = tempfile.mkdtemp()
            root_dir_created = True

        env = os.environ.copy()
        env["SMALL_SEA_ROOT_DIR"] = root_dir

        cmd = ["uv", "run", "fastapi", "dev", "packages/small-sea-hub/small_sea_hub/server.py", "--port", str(port)]
        proc = subprocess.Popen(cmd, env=env)
        servers.append({
            "proc": proc,
            "root_dir":root_dir,
            "root_created": root_dir_created,
        })
        # TODO: sleep seems like a hack. Better way to wait until it's ready?
        time.sleep(1)
        if proc.poll() is not None:
            raise RuntimeError(f"Small Sea Hub exited early (code {proc.returncode})")

        return {
            "port": port,
            "root_dir": root_dir,
            "endpoint": f"http://localhost:{port}",
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
