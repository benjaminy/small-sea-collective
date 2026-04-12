"""Micro tests for _wait_for_hub_ready in tests/conftest.py."""

import subprocess
import time
import unittest.mock as mock

import httpx
import pytest

from conftest import _wait_for_hub_ready


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(poll_returns):
    """Return a mock Popen-like object whose .poll() cycles through the given
    values and whose .terminate()/.wait()/.kill() are no-ops."""
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.poll.side_effect = poll_returns
    return proc


def _ok_response():
    resp = mock.MagicMock(spec=httpx.Response)
    resp.is_success = True
    resp.is_redirect = False
    return resp


def _error_response(status_code):
    resp = mock.MagicMock(spec=httpx.Response)
    resp.is_success = False
    resp.is_redirect = False
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# Delayed readiness: connection refused a few times, then 200
# ---------------------------------------------------------------------------

def test_returns_after_delayed_readiness(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    proc = _make_proc([None, None, None])  # process stays alive throughout

    call_count = 0

    def fake_get(url, timeout):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("refused")
        return _ok_response()

    monkeypatch.setattr(httpx, "get", fake_get)

    _wait_for_hub_ready(proc, "http://localhost:11437/", startup_timeout=5.0)
    assert call_count == 3


# ---------------------------------------------------------------------------
# Early subprocess exit is detected immediately
# ---------------------------------------------------------------------------

def test_raises_on_early_process_exit(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    proc = _make_proc([42])  # first poll() call returns non-None exit code
    proc.returncode = 42

    # httpx.get should never be called
    monkeypatch.setattr(httpx, "get", mock.MagicMock(side_effect=AssertionError("should not be called")))

    with pytest.raises(RuntimeError, match="Small Sea Hub exited early \\(code 42\\)"):
        _wait_for_hub_ready(proc, "http://localhost:11437/")


def test_raises_on_process_exit_mid_poll(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    # First poll returns None (alive), second returns 1 (dead)
    proc = _make_proc([None, 1])
    proc.returncode = 1

    call_count = 0

    def fake_get(url, timeout):
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(RuntimeError, match="Small Sea Hub exited early \\(code 1\\)"):
        _wait_for_hub_ready(proc, "http://localhost:11437/")

    assert call_count == 1  # one HTTP attempt before death was detected


# ---------------------------------------------------------------------------
# Unexpected HTTP status raises immediately (not retried)
# ---------------------------------------------------------------------------

def test_raises_on_unexpected_http_status(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    proc = _make_proc([None])  # process alive

    monkeypatch.setattr(httpx, "get", lambda url, timeout: _error_response(500))

    with pytest.raises(RuntimeError, match="unexpected status 500"):
        _wait_for_hub_ready(proc, "http://localhost:11437/")


# ---------------------------------------------------------------------------
# Timeout: terminate() is called, wait() attempted, kill() used as fallback
# ---------------------------------------------------------------------------

def test_timeout_cleanup_terminate_and_wait(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    # Process stays alive indefinitely; make the deadline expire immediately
    proc = _make_proc(iter(lambda: None, object()))  # always returns None

    monkeypatch.setattr(
        time, "monotonic",
        mock.MagicMock(side_effect=[0.0, 0.0, 10.0])  # start, loop check, deadline expired
    )
    monkeypatch.setattr(httpx, "get", mock.MagicMock(side_effect=httpx.ConnectError("refused")))

    with pytest.raises(RuntimeError, match="did not become ready"):
        _wait_for_hub_ready(proc, "http://localhost:11437/", startup_timeout=5.0)

    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()


def test_timeout_cleanup_kill_fallback(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    proc = _make_proc(iter(lambda: None, object()))
    proc.wait.side_effect = subprocess.TimeoutExpired(cmd="hub", timeout=3)

    monkeypatch.setattr(
        time, "monotonic",
        mock.MagicMock(side_effect=[0.0, 0.0, 10.0])
    )
    monkeypatch.setattr(httpx, "get", mock.MagicMock(side_effect=httpx.ConnectError("refused")))

    with pytest.raises(RuntimeError, match="did not become ready"):
        _wait_for_hub_ready(proc, "http://localhost:11437/", startup_timeout=5.0)

    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
