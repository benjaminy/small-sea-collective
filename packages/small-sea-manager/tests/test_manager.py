import pathlib
from types import SimpleNamespace

from fastapi.testclient import TestClient

import small_sea_manager.provisioning as Provisioning
from small_sea_manager.manager import TeamManager, _CORE_APP
from small_sea_manager.web import create_app


def test_team_manager_session_cache_is_keyed_by_team_and_mode(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    manager = TeamManager(root, participant_hex)

    manager.set_pending("ProjectX", "pending-encrypted")
    manager.set_pending("ProjectX", "pending-passthrough", mode="passthrough")
    manager.set_pending("NoteToSelf", "pending-nts", mode="passthrough")

    assert manager.session_state("ProjectX") == "pending"
    assert manager.session_state("ProjectX", mode="passthrough") == "pending"
    assert manager.session_state("NoteToSelf", mode="passthrough") == "pending"

    manager.set_session("ProjectX", "token-encrypted")

    assert manager.session_state("ProjectX") == "active"
    assert manager.get_pending_id("ProjectX") is None
    assert manager.get_pending_id("ProjectX", mode="passthrough") == "pending-passthrough"
    assert manager.get_pending_id("NoteToSelf", mode="passthrough") == "pending-nts"
    assert manager.session_state("ProjectX", mode="passthrough") == "pending"
    assert manager.session_state("NoteToSelf", mode="passthrough") == "pending"


def test_manager_web_pin_flow_updates_cached_session_state(playground_dir, monkeypatch):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    app = create_app(playground_dir, participant_hex)
    client = TestClient(app)
    manager = app.state.manager

    captured = {}

    def fake_start_session(participant, app_name, team_name, client_name, mode=None):
        captured["start"] = (participant, app_name, team_name, client_name, mode)
        return None, "pending-123"

    def fake_confirm_session(pending_id, pin):
        captured["confirm"] = (pending_id, pin)
        return SimpleNamespace(token="session-abc")

    monkeypatch.setattr(manager.client, "start_session", fake_start_session)
    monkeypatch.setattr(manager.client, "confirm_session", fake_confirm_session)

    response = client.post("/session/request")
    assert response.status_code == 200
    assert manager.session_state("NoteToSelf", mode="passthrough") == "pending"
    assert manager.get_pending_id("NoteToSelf", mode="passthrough") == "pending-123"
    assert captured["start"] == (
        participant_hex,
        _CORE_APP,
        "NoteToSelf",
        "ManagerUI",
        "passthrough",
    )

    response = client.post("/session/confirm", data={"pin": "321"})
    assert response.status_code == 200
    assert manager.session_state("NoteToSelf", mode="passthrough") == "active"
    assert manager.get_pending_id("NoteToSelf", mode="passthrough") is None
    assert captured["confirm"] == ("pending-123", "321")
