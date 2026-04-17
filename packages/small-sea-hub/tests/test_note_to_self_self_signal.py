"""Micro tests for the NoteToSelf self-update signal axis on /notifications/watch.

Covers:
- watch with known_self_count returns self_updated_count when berth counter is higher
- watch without known_self_count never returns self_updated_count (opt-in)
- watch with known_self_count already current returns no self_updated_count
- NoteToSelf session registers with watch_self_only; non-NoteToSelf sessions are unaffected
"""
import pathlib

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from fastapi.testclient import TestClient
import small_sea_hub.server as Server
from small_sea_hub.server import app
from wrasse_trust.keys import ProtectionLevel, generate_key_pair


def _open_session(http, nickname, team, mode="encrypted"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _setup_backend_and_session(root):
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    app.state.logger = backend.logger
    # Initialize watcher state that the lifespan would normally set up
    app.state.self_signal_counts = {}
    if not hasattr(app.state, "peer_signal_events"):
        import asyncio
        app.state.peer_signal_events = {}
    else:
        app.state.peer_signal_events = {}
    app.state.peer_counts = {}
    app.state.watched_sessions = {}
    app.state.watched_peers = {}
    http = TestClient(app)
    return backend, http


def test_watch_returns_self_updated_count_when_known_self_count_provided(playground_dir):
    """watch with known_self_count < current count returns self_updated_count."""
    root = pathlib.Path(playground_dir)
    Provisioning.create_new_participant(root, "Alice")
    backend, http = _setup_backend_and_session(root)

    token = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    auth = {"Authorization": f"Bearer {token}"}

    ss_session = backend._lookup_session(token)
    berth_id_hex = ss_session.berth_id.hex()

    # Simulate a remote NoteToSelf push having bumped the counter
    app.state.self_signal_counts[berth_id_hex] = 5

    resp = http.post(
        "/notifications/watch",
        json={"known": {}, "known_self_count": 0, "timeout": 0},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("self_updated_count") == 5


def test_watch_no_self_update_when_already_current(playground_dir):
    """watch with known_self_count matching current count returns no self_updated_count."""
    root = pathlib.Path(playground_dir)
    Provisioning.create_new_participant(root, "Alice")
    backend, http = _setup_backend_and_session(root)

    token = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    auth = {"Authorization": f"Bearer {token}"}

    ss_session = backend._lookup_session(token)
    berth_id_hex = ss_session.berth_id.hex()
    app.state.self_signal_counts[berth_id_hex] = 3

    resp = http.post(
        "/notifications/watch",
        json={"known": {}, "known_self_count": 3, "timeout": 0},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "self_updated_count" not in data


def test_watch_without_known_self_count_never_returns_self_updated(playground_dir):
    """watch without known_self_count does not return self_updated_count (opt-in)."""
    root = pathlib.Path(playground_dir)
    Provisioning.create_new_participant(root, "Alice")
    backend, http = _setup_backend_and_session(root)

    token = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    auth = {"Authorization": f"Bearer {token}"}

    ss_session = backend._lookup_session(token)
    berth_id_hex = ss_session.berth_id.hex()
    app.state.self_signal_counts[berth_id_hex] = 10

    resp = http.post(
        "/notifications/watch",
        json={"known": {}, "timeout": 0},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "self_updated_count" not in data


def test_existing_peer_watch_behavior_unaffected(playground_dir):
    """Adding self-update axis does not break peer-watch for non-NoteToSelf sessions."""
    root = pathlib.Path(playground_dir)
    alice_hex = Provisioning.create_new_participant(root, "Alice")
    team_result = Provisioning.create_team(root, alice_hex, "CoolTeam")
    backend, http = _setup_backend_and_session(root)

    token = _open_session(http, "Alice", "CoolTeam")
    auth = {"Authorization": f"Bearer {token}"}

    # No known peers — watch should return empty updated dict immediately
    resp = http.post(
        "/notifications/watch",
        json={"known": {}, "timeout": 0},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "updated" in data
    assert "self_updated_count" not in data


def test_team_db_revision_pulses_watchers_for_device_link_only_changes(playground_dir, monkeypatch):
    """A device_link-only team DB change wakes berth waiters even without member-list changes."""
    root = pathlib.Path(playground_dir)
    cloud_dir = root / "cloud"
    cloud_dir.mkdir()
    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(cloud_dir))
    Provisioning.create_team(root, alice_hex, "ProjectX")
    backend, http = _setup_backend_and_session(root)

    token = _open_session(http, "Alice", "ProjectX")
    berth_id_hex = backend._lookup_session(token).berth_id.hex()

    pulses: list[str] = []

    def capture_pulse(_app, candidate_berth_id_hex):
        pulses.append(candidate_berth_id_hex)

    monkeypatch.setattr(Server, "_pulse_berth_event", capture_pulse)
    monkeypatch.setattr(Server, "_refresh_local_runtime_signal", lambda *_args, **_kwargs: None)

    Server._watcher_pass(app)
    pulses.clear()

    linked_public_key = generate_key_pair(ProtectionLevel.DAILY)[0].public_key
    Provisioning.issue_device_link_for_member(root, alice_hex, "ProjectX", linked_public_key)

    Server._watcher_pass(app)

    assert berth_id_hex in pulses
