"""Micro tests for the Manager's Hub-sessions UI: list and delete."""

from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import TeamManager
from small_sea_manager.web import create_app

_CORE_APP = "SmallSeaCollectiveCore"
_FILES_APP = "SmallSeaCollectiveFiles"
_TEAM = "ProjectX"


def _fresh_env(root):
    backend = SmallSea.SmallSeaBackend(root_dir=root)
    participant_hex = Provisioning.create_new_participant(root, "alice")
    Provisioning.create_team(root, participant_hex, _TEAM)
    Provisioning.register_app_for_participant(root, participant_hex, _FILES_APP)
    Provisioning.activate_app_for_team(root, participant_hex, _TEAM, _FILES_APP)
    hub_app.state.backend = backend
    return backend, participant_hex, TestClient(hub_app)


def _open_session(hub_client, app_name, team_name, client_name="Smoke Tests", mode="passthrough"):
    resp = hub_client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": app_name,
            "team": team_name,
            "client": client_name,
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    pending_id = resp.json()["pending_id"]
    pin = next(
        p["pin"]
        for p in hub_app.state.backend.list_pending_sessions()
        if p["pending_id"] == pending_id
    )
    resp = hub_client.post(
        "/sessions/confirm",
        json={"pending_id": pending_id, "pin": pin},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _manager_web(root, participant_hex, hub_client):
    app = create_app(root, participant_hex)
    manager = TeamManager(root, participant_hex, _http_client=hub_client)
    token = _open_session(hub_client, _CORE_APP, "NoteToSelf", client_name="ManagerUI")
    manager.set_session("NoteToSelf", token, mode="passthrough")
    app.state.manager = manager
    return TestClient(app), manager


def test_card_lists_other_confirmed_sessions_but_not_the_managers_own(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    manager_client, _manager = _manager_web(backend.root_dir, participant_hex, hub_client)

    _open_session(hub_client, _FILES_APP, _TEAM, client_name="Phone")
    # A session left by an earlier Manager run looks just like this one's.
    _open_session(hub_client, _CORE_APP, "NoteToSelf", client_name="ManagerUI")

    resp = manager_client.get("/")

    assert resp.status_code == 200
    assert _FILES_APP in resp.text
    assert "Phone" in resp.text
    # The Manager's current session is hidden so it can't delete itself here;
    # the older Manager session and the Files session are listed.
    assert resp.text.count("Delete") == 2


def test_delete_route_removes_session_and_rerenders_card(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    manager_client, manager = _manager_web(backend.root_dir, participant_hex, hub_client)

    _open_session(hub_client, _FILES_APP, _TEAM, client_name="Phone")
    nts_session = manager.note_to_self_session_if_active()
    [other] = [
        s for s in nts_session.list_sessions() if s["client"] == "Phone"
    ]

    resp = manager_client.post(f"/session/other/{other['id']}/delete")

    assert resp.status_code == 200
    assert "<html" not in resp.text.lower()
    assert "Phone" not in resp.text
    assert [s["client"] for s in nts_session.list_sessions()] == ["ManagerUI"]


def test_delete_route_shows_hub_error_instead_of_500(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    manager_client, _manager = _manager_web(backend.root_dir, participant_hex, hub_client)

    resp = manager_client.post("/session/other/deadbeef/delete")

    assert resp.status_code == 200
    assert "<html" not in resp.text.lower()
    assert "notice-err" in resp.text
