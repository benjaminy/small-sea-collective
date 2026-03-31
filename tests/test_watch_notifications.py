"""Tests for POST /notifications/watch notification behavior.

Uses httpx.AsyncClient with ASGITransport (in-process, no subprocess) and a
stub backend to exercise all notification edge cases without MinIO or real
provisioning. Each test wraps its async body in asyncio.run() to get a fresh
event loop and avoid event object cross-contamination between tests.
"""

import asyncio
import logging

import httpx
import pytest

from small_sea_hub.server import app, _pulse_station_event


# ---------------------------------------------------------------------------
# Stub backend
# ---------------------------------------------------------------------------

class _StubSession:
    def __init__(self, station_id_hex):
        self.station_id = bytes.fromhex(station_id_hex)
        self.team_name = "NoteToSelf"
        self.participant_path = None


class _StubBackend:
    logger = logging.getLogger("stub_backend")
    auto_approve_sessions = False

    def __init__(self):
        self._sessions = {}

    def register(self, token_hex, station_id_hex):
        self._sessions[token_hex] = _StubSession(station_id_hex)

    def _lookup_session(self, session_hex):
        if session_hex not in self._sessions:
            raise Exception(f"Unknown stub session: {session_hex}")
        return self._sessions[session_hex]

    def upload_to_cloud(self, session_hex, path, data, expected_etag=None):
        return True, "fake-etag", "ok"

    def _bump_signal(self, session_hex):
        pass  # no-op — signal file not needed for notification unit tests


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def stub_app():
    """Reset app state with a fresh stub backend before each test."""
    stub = _StubBackend()
    app.state.backend = stub
    app.state.watched_sessions = {}
    app.state.watched_peers = {}
    app.state.peer_counts = {}
    app.state.peer_signal_events = {}
    app.state.logger = logging.getLogger("test")
    yield app, stub
    for attr in (
        "backend", "watched_sessions",
        "watched_peers", "peer_counts", "peer_signal_events", "logger",
    ):
        try:
            delattr(app.state, attr)
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Test constants  (all 32-hex = 16-byte IDs matching the real system)
# ---------------------------------------------------------------------------

STATION_A = "aa" * 16
STATION_B = "bb" * 16
SESSION_1 = "11" * 16
SESSION_2 = "22" * 16
PEER_BOB  = "b0" * 16
PEER_EVE  = "e0" * 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(application):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    )


def _auth(token_hex):
    return {"Authorization": f"Bearer {token_hex}"}


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------

def test_returns_immediately_if_already_stale(stub_app):
    """Hub already knows about a higher count — returns without blocking."""
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    application.state.peer_counts[(STATION_A, PEER_BOB)] = 5

    async def _run():
        async with _client(application) as c:
            resp = await c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 3}, "timeout": 30},
                headers=_auth(SESSION_1),
            )
        assert resp.status_code == 200
        assert resp.json()["updated"] == {PEER_BOB: 5}

    asyncio.run(_run())


def test_blocks_then_wakes_on_count_increase(stub_app):
    """Waiter blocks until peer_counts is incremented and event is pulsed."""
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    application.state.peer_counts[(STATION_A, PEER_BOB)] = 3

    async def _run():
        async with _client(application) as c:
            watch = asyncio.create_task(c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 3}, "timeout": 10},
                headers=_auth(SESSION_1),
            ))
            await asyncio.sleep(0.05)  # let the watch start waiting

            application.state.peer_counts[(STATION_A, PEER_BOB)] = 4
            _pulse_station_event(application, STATION_A)

            resp = await asyncio.wait_for(watch, timeout=5)
        assert resp.json()["updated"] == {PEER_BOB: 4}

    asyncio.run(_run())


def test_timeout_returns_empty(stub_app):
    """No change within the timeout window → returns empty updated dict."""
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    application.state.peer_counts[(STATION_A, PEER_BOB)] = 3

    async def _run():
        async with _client(application) as c:
            resp = await c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 3}, "timeout": 1},
                headers=_auth(SESSION_1),
            )
        assert resp.status_code == 200
        assert resp.json()["updated"] == {}

    asyncio.run(_run())


def test_multiple_sessions_same_station_both_notified(stub_app):
    """Two sessions on the same station both wake when the event is pulsed."""
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    stub.register(SESSION_2, STATION_A)
    application.state.peer_counts[(STATION_A, PEER_BOB)] = 3

    async def _run():
        async with _client(application) as c:
            w1 = asyncio.create_task(c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 3}, "timeout": 10},
                headers=_auth(SESSION_1),
            ))
            w2 = asyncio.create_task(c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 3}, "timeout": 10},
                headers=_auth(SESSION_2),
            ))
            await asyncio.sleep(0.05)

            application.state.peer_counts[(STATION_A, PEER_BOB)] = 4
            _pulse_station_event(application, STATION_A)

            r1, r2 = await asyncio.wait_for(asyncio.gather(w1, w2), timeout=5)
        assert r1.json()["updated"] == {PEER_BOB: 4}
        assert r2.json()["updated"] == {PEER_BOB: 4}

    asyncio.run(_run())


def test_local_push_notifies_other_local_session(stub_app):
    """A notify=True upload pulses the station event, waking other local sessions.

    The woken session receives updated={} (no specific peers to report) — the
    correct signal to re-enumerate peers before the next watch call.
    """
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    stub.register(SESSION_2, STATION_A)

    async def _run():
        async with _client(application) as c:
            # Session 2 waits with empty known — no specific peers to watch
            watch = asyncio.create_task(c.post(
                "/notifications/watch",
                json={"known": {}, "timeout": 10},
                headers=_auth(SESSION_2),
            ))
            await asyncio.sleep(0.05)

            # Session 1 uploads with notify=True
            upload = await c.post(
                "/cloud_file",
                json={"path": "latest-link.yaml", "data": "dGVzdA==", "notify": True},
                headers=_auth(SESSION_1),
            )
            assert upload.status_code == 200

            # Session 2 should wake promptly, not time out
            resp = await asyncio.wait_for(watch, timeout=5)
        assert resp.status_code == 200
        assert resp.json()["updated"] == {}

    asyncio.run(_run())


def test_membership_change_causes_spurious_wakeup(stub_app):
    """Pulsing the event (e.g. on new peer discovery) wakes waiters with empty updated.

    This is the correct signal for the app to re-enumerate its peer list.
    """
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)

    async def _run():
        async with _client(application) as c:
            watch = asyncio.create_task(c.post(
                "/notifications/watch",
                json={"known": {}, "timeout": 10},
                headers=_auth(SESSION_1),
            ))
            await asyncio.sleep(0.05)

            # Simulate _refresh_session_peers discovering a new peer
            _pulse_station_event(application, STATION_A)

            resp = await asyncio.wait_for(watch, timeout=5)
        assert resp.status_code == 200
        assert resp.json()["updated"] == {}  # app should re-enumerate peers

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------

def test_no_crosstalk_between_stations(stub_app):
    """Pulsing station A does not wake a waiter on station B."""
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    stub.register(SESSION_2, STATION_B)
    application.state.peer_counts[(STATION_A, PEER_BOB)] = 3
    application.state.peer_counts[(STATION_B, PEER_BOB)] = 3

    async def _run():
        async with _client(application) as c:
            watch_b = asyncio.create_task(c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 3}, "timeout": 2},
                headers=_auth(SESSION_2),
            ))
            await asyncio.sleep(0.05)

            # Update and pulse station A only
            application.state.peer_counts[(STATION_A, PEER_BOB)] = 4
            _pulse_station_event(application, STATION_A)

            # Station B's waiter should time out, not wake
            resp = await watch_b
        assert resp.json()["updated"] == {}

    asyncio.run(_run())


def test_peer_with_higher_count_not_in_known_is_not_returned(stub_app):
    """A peer that pushed but is not in the known dict is not returned.

    This is the expected behaviour when the app doesn't yet know about a member
    (e.g. they joined after the last watch call). The spurious wakeup from
    membership change (test above) gives the app a chance to add them to known.
    """
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    application.state.peer_counts[(STATION_A, PEER_EVE)] = 5  # Eve pushed

    async def _run():
        async with _client(application) as c:
            # Only watching Bob — Eve is unknown to this call
            resp = await c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 0}, "timeout": 1},
                headers=_auth(SESSION_1),
            )
        assert resp.status_code == 200
        assert resp.json()["updated"] == {}
        assert PEER_EVE not in resp.json()["updated"]

    asyncio.run(_run())


def test_count_at_known_value_does_not_trigger(stub_app):
    """Hub count equal to known count is not stale — waiter blocks until timeout."""
    application, stub = stub_app
    stub.register(SESSION_1, STATION_A)
    application.state.peer_counts[(STATION_A, PEER_BOB)] = 7  # same as known

    async def _run():
        async with _client(application) as c:
            resp = await c.post(
                "/notifications/watch",
                json={"known": {PEER_BOB: 7}, "timeout": 1},
                headers=_auth(SESSION_1),
            )
        assert resp.json()["updated"] == {}

    asyncio.run(_run())
