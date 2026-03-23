"""Tests for the two-step PIN-based session approval flow."""

from datetime import datetime, timedelta, timezone

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession


@pytest.fixture()
def test_env(playground_dir):
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    client = TestClient(app)
    return {"backend": backend, "client": client}


def _request_and_confirm(
    client,
    participant="alice",
    app_name="SmallSeaCollectiveCore",
    team="NoteToSelf",
    client_name="Smoke Tests",
):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": participant,
            "app": app_name,
            "team": team,
            "client": client_name,
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    pending_id = result["pending_id"]
    pin = result["pin"]

    resp = client.post("/sessions/confirm", json={"pending_id": pending_id, "pin": pin})
    assert resp.status_code == 200
    return resp.json()  # session hex


def test_two_step_flow(test_env):
    """Happy path: request then confirm yields a usable session token."""
    client = test_env["client"]
    session_hex = _request_and_confirm(client)
    assert isinstance(session_hex, str)
    assert len(session_hex) == 64  # 32 bytes


def test_wrong_pin_rejected(test_env):
    """Confirming with the wrong PIN raises an error."""
    backend = test_env["backend"]
    pending_id_hex, correct_pin = backend.request_session(
        "alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests"
    )

    wrong_pin = str((int(correct_pin) + 1) % 10000).zfill(4)
    with pytest.raises(SmallSea.SmallSeaBackendExn, match="Invalid PIN"):
        backend.confirm_session(pending_id_hex, wrong_pin)


def test_expired_pin_rejected(test_env):
    """A pending session past its TTL is rejected."""
    backend = test_env["backend"]
    pending_id_hex, pin = backend.request_session(
        "alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests"
    )

    # Manually backdate the expires_at in the DB
    pending_id = bytes.fromhex(pending_id_hex)
    engine_local = create_engine(f"sqlite:///{backend.path_local_db}")
    with SASession(engine_local) as sess:
        pending = (
            sess.query(SmallSea.PendingSession)
            .filter(SmallSea.PendingSession.id == pending_id)
            .first()
        )
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        pending.expires_at = past
        sess.commit()

    with pytest.raises(SmallSea.SmallSeaBackendExn, match="PIN expired"):
        backend.confirm_session(pending_id_hex, pin)


def test_pending_row_deleted_after_confirm(test_env):
    """The pending_session row is cleaned up after successful confirmation."""
    backend = test_env["backend"]
    pending_id_hex, pin = backend.request_session(
        "alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests"
    )
    backend.confirm_session(pending_id_hex, pin)

    pending_id = bytes.fromhex(pending_id_hex)
    engine_local = create_engine(f"sqlite:///{backend.path_local_db}")
    with SASession(engine_local) as sess:
        row = (
            sess.query(SmallSea.PendingSession)
            .filter(SmallSea.PendingSession.id == pending_id)
            .first()
        )
    assert row is None


def test_session_for_team_station(playground_dir):
    """Sessions can be opened for non-NoteToSelf teams."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    app.state.backend = backend
    client = TestClient(app)

    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "ProjectX",
            "client": "Smoke Tests",
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    pending_id = result["pending_id"]
    pin = result["pin"]

    resp = client.post("/sessions/confirm", json={"pending_id": pending_id, "pin": pin})
    assert resp.status_code == 200
    session_hex = resp.json()
    assert isinstance(session_hex, str)
    assert len(session_hex) == 64


def test_unknown_team_rejected(playground_dir):
    """Requesting a session for a team that doesn't exist raises 422 or 404."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    client = TestClient(app)

    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "NoSuchTeam",
            "client": "Smoke Tests",
        },
    )
    assert resp.status_code == 404


def test_session_requires_bearer_header(test_env):
    """Cloud endpoints return 422 without an Authorization header."""
    client = test_env["client"]
    resp = client.get("/cloud_file", params={"path": "foo.txt"})
    assert resp.status_code == 422


def test_invalid_bearer_rejected(test_env):
    """A malformed Authorization header returns 401."""
    client = test_env["client"]
    resp = client.get(
        "/cloud_file",
        params={"path": "foo.txt"},
        headers={"Authorization": "NotBearer abc"},
    )
    assert resp.status_code == 401
