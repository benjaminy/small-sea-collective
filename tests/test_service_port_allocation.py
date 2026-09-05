"""Micro tests for the shared test-service port allocation in test_support.py."""

import pathlib
import socket

import boto3
import pytest
from botocore.config import Config as BotoConfig

from test_support import reserve_ports


def _is_bindable(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def test_multiple_dynamic_ports_are_distinct_and_bindable():
    ports = reserve_ports([None, None, None])

    assert len(set(ports)) == 3
    for port in ports:
        assert _is_bindable(port), f"port {port} was not actually free"


def test_explicit_free_port_is_returned_unchanged():
    (chosen,) = reserve_ports([None])

    assert reserve_ports([chosen]) == [chosen]


def test_occupied_explicit_port_is_reported():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        occupied = squatter.getsockname()[1]

        with pytest.raises(RuntimeError, match=f"Port {occupied} is not available"):
            reserve_ports([None, occupied])


def _bucket_names(server):
    client = boto3.client(
        "s3",
        endpoint_url=server["endpoint"],
        aws_access_key_id=server["access_key"],
        aws_secret_access_key=server["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
    )
    return client, {b["Name"] for b in client.list_buckets()["Buckets"]}


def test_default_servers_are_independent_stores(minio_server_gen):
    first = minio_server_gen()
    second = minio_server_gen()

    assert first["port"] != second["port"]

    first_client, _ = _bucket_names(first)
    first_client.create_bucket(Bucket="only-on-the-first-server")

    _, second_buckets = _bucket_names(second)
    assert "only-on-the-first-server" not in second_buckets


def test_startup_fails_when_the_requested_port_is_taken(minio_server_gen):
    """A listener already on the port must not be mistaken for a healthy child."""
    running = minio_server_gen()

    with pytest.raises(RuntimeError, match="not available"):
        minio_server_gen(port=running["port"])


def test_listener_taking_port_after_reservation_is_not_ready(monkeypatch, tmp_path):
    """A healthy HTTP squatter must not stand in for the launched MinIO."""
    import http.server
    import threading

    import test_support

    class HealthyListener(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    listeners = []
    children = []
    roots = []
    original_popen = test_support.subprocess.Popen
    original_mkdtemp = test_support.tempfile.mkdtemp
    original_wait = test_support._wait_for_minio_ready

    def reserve_then_occupy(requested):
        ports = reserve_ports(requested)
        listener = http.server.ThreadingHTTPServer(("127.0.0.1", ports[0]), HealthyListener)
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        listeners.append((listener, thread))
        return ports

    def record_child(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    def record_root():
        root = original_mkdtemp(dir=tmp_path)
        roots.append(root)
        return root

    monkeypatch.setattr(test_support, "reserve_ports", reserve_then_occupy)
    monkeypatch.setattr(test_support.subprocess, "Popen", record_child)
    monkeypatch.setattr(test_support.tempfile, "mkdtemp", record_root)
    monkeypatch.setattr(
        test_support, "_wait_for_minio_ready",
        lambda *args: original_wait(*args, startup_timeout=0.3),
    )
    fixture = test_support.minio_server_gen.__wrapped__()
    start = next(fixture)
    try:
        with pytest.raises(RuntimeError, match="exited early|did not become ready"):
            start()
        assert children and all(child.poll() is not None for child in children)
        assert roots and all(not pathlib.Path(root).exists() for root in roots)
    finally:
        next(fixture, None)
        for child in children:
            test_support._shut_down(child)
        for listener, thread in listeners:
            listener.shutdown()
            listener.server_close()
            thread.join()


def test_readiness_rejects_another_minio_instance(minio_server_gen):
    from unittest.mock import Mock

    from test_support import _wait_for_minio_ready

    first = minio_server_gen()
    second = minio_server_gen()
    proc = Mock()
    proc.poll.return_value = None
    with pytest.raises(RuntimeError, match="did not become ready"):
        _wait_for_minio_ready(
            proc, first["endpoint"], second["access_key"], second["secret_key"],
            startup_timeout=0,
        )


def test_hub_occupied_port_creates_no_temporary_root(hub_server_gen, monkeypatch, tmp_path):
    import tempfile

    original_mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda: original_mkdtemp(dir=tmp_path))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        with pytest.raises(RuntimeError, match="not available"):
            hub_server_gen(port=squatter.getsockname()[1])
    assert list(tmp_path.iterdir()) == []
