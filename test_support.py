"""Shared test-support helpers.

Lives at the repo root so it ships in zero runtime distributions and is
importable from any package's tests via the root `pyproject.toml`'s
`pythonpath = ["."]`.
"""

import contextlib
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from botocore.parsers import ResponseParserError
import pytest


def publish_storage_announcement_for_session(backend, session_hex) -> dict | None:
    """Publish this session's own-storage announcement.

    For NoteToSelf sessions this is a no-op (returns None) — that team
    has no shared storage to announce.

    `backend` must expose `.root_dir` and `._lookup_session(session_hex)`
    returning a `SmallSeaSession`.  Duck-typed so this module can stay
    free of hub/files imports.
    """
    import small_sea_manager.provisioning as provisioning

    ss_session = backend._lookup_session(session_hex)
    if ss_session.team_name == "NoteToSelf":
        return None
    allocation = provisioning.get_berth_cloud_allocation_for_berth(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
    )
    assert allocation is not None
    team_id, self_teammate_id = provisioning._team_row(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
    )
    assert team_id == ss_session.team_id
    return provisioning.publish_teammate_berth_storage_announcement(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
        self_teammate_id,
        ss_session.berth_id,
        allocation,
    )


def acceptance_record_from_courier(courier_b64: str) -> dict:
    """Decode the `admission_acceptance` record out of a courier token."""
    import base64
    import json

    import small_sea_manager.provisioning as provisioning

    payload = json.loads(base64.b64decode(courier_b64).decode())
    if payload.get("envelope") != provisioning.ACCEPTANCE_COURIER_ENVELOPE:
        return payload
    return json.loads(base64.b64decode(payload["admission_acceptance"]).decode())


def route_sidecar_from_courier(courier_b64: str) -> dict | None:
    """Decode the route attached beside the acceptance, if any."""
    import base64
    import json

    payload = json.loads(base64.b64decode(courier_b64).decode())
    return payload.get("route")


def accept_and_export(manager, token_b64: str) -> str:
    """Accept an invitation and export the courier token in one step.

    Asserts the route landed, so a test that means to exercise the happy path
    fails at the point the route went pending rather than later.
    """
    import base64
    import json

    team_name = json.loads(base64.b64decode(token_b64).decode())["team_name"]
    report = manager.accept_invitation(token_b64)
    assert report["route"] == "ready", report
    assert report["acceptance"] == "exportable", report
    exported = manager.export_admission_acceptance(team_name)
    assert exported["acceptance_token"] is not None, exported
    return exported["acceptance_token"]


# ---------------------------------------------------------------------------
# Local service ports and MinIO test servers
# ---------------------------------------------------------------------------

def reserve_ports(requested):
    """Bind the requested TCP ports at once and return the chosen numbers.

    Each entry is either an explicit port number or None, meaning "any free
    port".  All sockets stay bound until every port has been selected, so the
    returned ports cannot collide with each other.  They are released before
    the service binds them, so allocation against other processes is advisory:
    concurrent runs passing this check is evidence, not proof, of race freedom.

    An explicit port that is already occupied raises here, so a caller that
    insists on a fixed port fails loudly instead of silently talking to
    whatever else is listening there.
    """
    sockets = []
    try:
        for port in requested:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            try:
                sock.bind(("127.0.0.1", 0 if port is None else port))
            except OSError as exc:
                raise RuntimeError(f"Port {port} is not available: {exc}") from exc
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _wait_for_minio_ready(proc, endpoint, access_key, secret_key, startup_timeout=15.0):
    """Wait for S3 readiness using credentials unique to this launch.

    A health response cannot distinguish this child from another listener
    that took the port after reservation.  An authenticated bucket listing
    rejects other MinIO instances, including concurrent test servers.
    """
    deadline = time.monotonic() + startup_timeout
    with contextlib.closing(boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=BotoConfig(
            signature_version="s3v4",
            connect_timeout=0.25,
            read_timeout=0.25,
            retries={"total_max_attempts": 1},
            proxies={},
        ),
    )) as client:
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"MinIO exited early (code {proc.returncode})")
            with contextlib.suppress(BotoCoreError, ClientError, ResponseParserError):
                response = client.list_buckets()
                if "Buckets" in response:
                    return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"MinIO at {endpoint} did not become ready within {startup_timeout}s")
            time.sleep(0.05)


@pytest.fixture(scope="session")
def minio_server_gen():
    """Start real MinIO servers on dynamically allocated ports.

    Imported by each package's `conftest.py`; this is the only implementation.
    Pass `port` only when a test needs a specific one.  The console port is
    always allocated separately: deriving it as `port + 1` collides with the
    next server's API port.
    """
    servers = []

    def start_server(root_dir=None, port=None):
        root_dir_created = False
        if root_dir is None:
            root_dir = tempfile.mkdtemp()
            root_dir_created = True

        def discard_root_dir():
            if root_dir_created:
                shutil.rmtree(root_dir, ignore_errors=True)

        try:
            port, console_port = reserve_ports([port, None])
        except RuntimeError:
            discard_root_dir()
            raise

        env = os.environ.copy()
        access_key = secrets.token_hex(16)
        secret_key = secrets.token_hex(32)
        env["MINIO_ROOT_USER"] = access_key
        env["MINIO_ROOT_PASSWORD"] = secret_key
        proc = subprocess.Popen(
            [
                "minio",
                "server",
                root_dir,
                "--address",
                f"127.0.0.1:{port}",
                "--console-address",
                f"127.0.0.1:{console_port}",
            ],
            env=env,
        )
        endpoint = f"http://127.0.0.1:{port}"
        try:
            _wait_for_minio_ready(proc, endpoint, access_key, secret_key)
        except Exception:
            _shut_down(proc)
            discard_root_dir()
            raise

        servers.append({"proc": proc, "root_dir": root_dir, "root_created": root_dir_created})
        return {
            "port": port,
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
        }

    yield start_server

    for server in servers:
        _shut_down(server["proc"])
        if server["root_created"]:
            shutil.rmtree(server["root_dir"], ignore_errors=True)


def _shut_down(proc):
    """Terminate a test service subprocess, killing it if it will not exit."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
