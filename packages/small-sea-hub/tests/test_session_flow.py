"""Tests for the two-step PIN-based session approval flow."""

from datetime import datetime, timedelta, timezone
import pathlib
import sqlite3

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
    mode="encrypted",
):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": participant,
            "app": app_name,
            "team": team,
            "client": client_name,
            "mode": mode,
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
    session_hex = _request_and_confirm(client, mode="passthrough")
    assert isinstance(session_hex, str)
    assert len(session_hex) == 64  # 32 bytes


def test_wrong_pin_rejected(test_env):
    """Confirming with the wrong PIN raises an error."""
    backend = test_env["backend"]
    pending_id_hex, correct_pin = backend.request_session(
        "alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests", mode="passthrough"
    )

    wrong_pin = str((int(correct_pin) + 1) % 10000).zfill(4)
    with pytest.raises(SmallSea.SmallSeaBackendExn, match="Invalid PIN"):
        backend.confirm_session(pending_id_hex, wrong_pin)


def test_expired_pin_rejected(test_env):
    """A pending session past its TTL is rejected."""
    backend = test_env["backend"]
    pending_id_hex, pin = backend.request_session(
        "alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests", mode="passthrough"
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
        "alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests", mode="passthrough"
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


def test_session_for_team_berth(playground_dir):
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
    """Cloud endpoints return 401 without an Authorization header."""
    client = test_env["client"]
    resp = client.get("/cloud_file", params={"path": "foo.txt"})
    assert resp.status_code == 401


def test_invalid_bearer_rejected(test_env):
    """A malformed Authorization header returns 401."""
    client = test_env["client"]
    resp = client.get(
        "/cloud_file",
        params={"path": "foo.txt"},
        headers={"Authorization": "NotBearer abc"},
    )
    assert resp.status_code == 401


def test_session_info(playground_dir):
    """GET /session/info returns identity fields without reading the DB directly."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    app.state.backend = backend
    client = TestClient(app)

    session_hex = _request_and_confirm(client, team="ProjectX")
    resp = client.get(
        "/session/info",
        headers={"Authorization": f"Bearer {session_hex}"},
    )
    assert resp.status_code == 200
    info = resp.json()
    assert info["participant_hex"] == alice_hex
    assert info["team_name"] == "ProjectX"
    assert len(info["team_id"]) == 32
    assert info["app_name"] == "SmallSeaCollectiveCore"
    assert len(info["berth_id"]) == 32  # 16 bytes hex
    assert info["client"] == "Smoke Tests"
    assert info["mode"] == "encrypted"


def test_session_mode_defaults_to_encrypted_without_request_field(playground_dir):
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
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    session_hex = resp.json()

    info = client.get(
        "/session/info", headers={"Authorization": f"Bearer {session_hex}"}
    ).json()
    assert info["mode"] == "encrypted"


def test_notetoself_has_no_special_default_mode(playground_dir):
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    client = TestClient(app)

    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "NoteToSelf",
            "client": "Smoke Tests",
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    session_hex = resp.json()

    info = client.get(
        "/session/info", headers={"Authorization": f"Bearer {session_hex}"}
    ).json()
    assert info["mode"] == "encrypted"


def test_request_confirm_preserves_passthrough_mode(playground_dir):
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir, sandbox_mode=True)
    Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    client = TestClient(app)

    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "NoteToSelf",
            "client": "TestClient",
            "mode": "passthrough",
        },
    )
    assert resp.status_code == 200
    pending_id = resp.json()["pending_id"]

    pending = client.get("/sessions/pending").json()
    assert pending[0]["mode"] == "passthrough"
    assert pending[0]["mode_warning"] == "[unsafe]"

    with SASession(create_engine(f"sqlite:///{backend.path_local_db}")) as sess:
        row = (
            sess.query(SmallSea.PendingSession)
            .filter(SmallSea.PendingSession.id == bytes.fromhex(pending_id))
            .first()
        )
        pin = row.pin

    resp = client.post("/sessions/confirm", json={"pending_id": pending_id, "pin": pin})
    session_hex = resp.json()
    info = client.get(
        "/session/info", headers={"Authorization": f"Bearer {session_hex}"}
    ).json()
    assert info["mode"] == "passthrough"


def test_pending_sessions_requires_sandbox_mode(test_env):
    """/sessions/pending returns 404 when not in sandbox mode."""
    client = test_env["client"]
    resp = client.get("/sessions/pending")
    assert resp.status_code == 404


def test_pending_sessions_lists_with_pin(playground_dir):
    """/sessions/pending returns pending sessions with PINs in sandbox mode."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir, sandbox_mode=True)
    Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    client = TestClient(app)

    # No pending sessions yet
    resp = client.get("/sessions/pending")
    assert resp.status_code == 200
    assert resp.json() == []

    # Request a session (creates a pending row)
    resp = client.post(
        "/sessions/request",
        json={"participant": "alice", "app": "SmallSeaCollectiveCore",
              "team": "NoteToSelf", "client": "TestClient", "mode": "passthrough"},
    )
    assert resp.status_code == 200
    pending_id = resp.json()["pending_id"]

    resp = client.get("/sessions/pending")
    assert resp.status_code == 200
    pending = resp.json()
    assert len(pending) == 1
    assert pending[0]["pending_id"] == pending_id
    assert pending[0]["client_name"] == "TestClient"
    assert pending[0]["team_name"] == "NoteToSelf"
    assert pending[0]["mode"] == "passthrough"
    assert pending[0]["mode_warning"] == "[unsafe]"
    assert len(pending[0]["pin"]) == 3  # current Hub implementation uses 3-digit PINs


def test_session_info_via_client(playground_dir):
    """SmallSeaSession.session_info() wraps GET /session/info."""
    from small_sea_client.client import SmallSeaClient, SmallSeaSession

    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    http = TestClient(app)

    session_hex = _request_and_confirm(http, mode="passthrough")
    sc = SmallSeaClient(_http_client=http)
    session = SmallSeaSession(sc, session_hex)
    info = session.session_info()

    assert info["participant_hex"] == alice_hex
    assert info["team_name"] == "NoteToSelf"
    assert len(info["berth_id"]) == 32
    assert info["mode"] == "passthrough"


def test_session_peers(playground_dir):
    """GET /session/peers returns peer metadata for a team session."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    bob_teammate_id = bytes.fromhex("11" * 16)
    team_db = (
        pathlib.Path(playground_dir)
        / "Participants"
        / alice_hex
        / "ProjectX"
        / "Sync"
        / "core.db"
    )
    conn = sqlite3.connect(str(team_db))
    try:
        conn.execute("INSERT INTO teammate (id, display_name) VALUES (?, ?)", (bob_teammate_id, "Bob"))
        conn.execute(
            "INSERT INTO team_device (device_key_id, teammate_id, public_key, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                bob_teammate_id,
                bob_teammate_id,
                bob_teammate_id,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    app.state.backend = backend
    client = TestClient(app)

    session_hex = _request_and_confirm(client, team="ProjectX")
    resp = client.get(
        "/session/peers",
        headers={"Authorization": f"Bearer {session_hex}"},
    )
    assert resp.status_code == 200
    peers = resp.json()["peers"]
    assert len(peers) == 1
    assert peers[0]["teammate_id"] == bob_teammate_id.hex()
    assert peers[0]["name"] == "Bob"
    assert peers[0]["label"] == "Bob"


def test_list_confirmed_sessions_via_note_to_self(playground_dir):
    """A participant's NoteToSelf session can list their confirmed sessions, without tokens."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    client = TestClient(app)

    nts_session_hex = _request_and_confirm(client, mode="passthrough")

    resp = client.get(
        "/sessions/confirmed",
        headers={"Authorization": f"Bearer {nts_session_hex}"},
    )
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 1
    row = sessions[0]
    assert set(row.keys()) == {
        "id", "team_name", "app_name", "client", "mode", "created_at", "duration_sec",
    }
    assert row["team_name"] == "NoteToSelf"
    assert nts_session_hex not in str(row)  # no token anywhere in the response


def test_delete_confirmed_session_makes_token_unknown(playground_dir):
    """Deleting a confirmed session makes its token behave as unknown afterward."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    app.state.backend = backend
    client = TestClient(app)

    nts_session_hex = _request_and_confirm(client, mode="passthrough")
    other_session_hex = _request_and_confirm(client, team="ProjectX", mode="passthrough")

    sessions = client.get(
        "/sessions/confirmed",
        headers={"Authorization": f"Bearer {nts_session_hex}"},
    ).json()
    target = next(s for s in sessions if s["team_name"] == "ProjectX")

    resp = client.delete(
        f"/sessions/confirmed/{target['id']}",
        headers={"Authorization": f"Bearer {nts_session_hex}"},
    )
    assert resp.status_code == 200

    resp = client.get(
        "/session/info",
        headers={"Authorization": f"Bearer {other_session_hex}"},
    )
    assert resp.status_code == 401


def test_confirmed_sessions_require_note_to_self_session(playground_dir):
    """A caller whose own session is not on NoteToSelf gets 403 on list and delete."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    app.state.backend = backend
    client = TestClient(app)

    project_session_hex = _request_and_confirm(client, team="ProjectX", mode="passthrough")

    resp = client.get(
        "/sessions/confirmed",
        headers={"Authorization": f"Bearer {project_session_hex}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "session_admin_not_allowed"

    resp = client.delete(
        f"/sessions/confirmed/{'00' * 16}",
        headers={"Authorization": f"Bearer {project_session_hex}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "session_admin_not_allowed"


def test_delete_unknown_confirmed_session_is_404(test_env):
    """Deleting an id that names no session gives 404 session_not_found."""
    client = test_env["client"]
    nts_session_hex = _request_and_confirm(client, mode="passthrough")

    resp = client.delete(
        f"/sessions/confirmed/{'00' * 16}",
        headers={"Authorization": f"Bearer {nts_session_hex}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "session_not_found"


def test_cannot_delete_another_participants_session(playground_dir):
    """A participant's NoteToSelf session cannot delete another participant's session."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_new_participant(playground_dir, "bob")
    app.state.backend = backend
    client = TestClient(app)

    alice_nts_hex = _request_and_confirm(client, participant="alice", mode="passthrough")
    bob_nts_hex = _request_and_confirm(client, participant="bob", mode="passthrough")

    bob_sessions = client.get(
        "/sessions/confirmed",
        headers={"Authorization": f"Bearer {bob_nts_hex}"},
    ).json()
    bob_session_id = bob_sessions[0]["id"]

    resp = client.delete(
        f"/sessions/confirmed/{bob_session_id}",
        headers={"Authorization": f"Bearer {alice_nts_hex}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "session_not_found"


def test_list_sessions_via_client(playground_dir):
    """SmallSeaSession.list_sessions()/delete_session() wrap the confirmed-sessions endpoints."""
    from small_sea_client.client import SmallSeaClient, SmallSeaSession

    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    http = TestClient(app)

    session_hex = _request_and_confirm(http, mode="passthrough")
    sc = SmallSeaClient(_http_client=http)
    session = SmallSeaSession(sc, session_hex)

    sessions = session.list_sessions()
    assert len(sessions) == 1
    session.delete_session(sessions[0]["id"])

    info_resp = http.get(
        "/session/info",
        headers={"Authorization": f"Bearer {session_hex}"},
    )
    assert info_resp.status_code == 401
