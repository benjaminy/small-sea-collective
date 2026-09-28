"""Micro tests for the Manager's per-team known app list."""

from fastapi.testclient import TestClient
import sqlite3

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import TeamManager
from small_sea_manager.web import create_app


_CORE = "SmallSeaCollectiveCore"
_FILES = "SmallSeaCollectiveFiles"
_TEAM = "ProjectX"


def _setup(root):
    backend = SmallSea.SmallSeaBackend(root_dir=root)
    participant_hex = Provisioning.create_new_participant(root, "alice")
    Provisioning.create_team(root, participant_hex, _TEAM)
    Provisioning.register_app_for_participant(root, participant_hex, _FILES)
    Provisioning.activate_app_for_team(root, participant_hex, _TEAM, _FILES)
    hub_app.state.backend = backend
    hub = TestClient(hub_app)
    manager = TeamManager(root, participant_hex, _http_client=hub)
    web_app = create_app(root, participant_hex)
    web_app.state.manager = manager
    return backend, participant_hex, hub, manager, TestClient(web_app)


def _confirm(hub, app_name, team_name):
    response = hub.post("/sessions/request", json={
        "participant": "alice", "app": app_name, "team": team_name,
        "client": "Known apps test", "mode": "passthrough",
    })
    assert response.status_code == 200, response.text
    pending = response.json()
    pin = next(
        row["pin"] for row in hub_app.state.backend.list_pending_sessions()
        if row["pending_id"] == pending["pending_id"]
    )
    confirmed = hub.post("/sessions/confirm", json={
        "pending_id": pending["pending_id"], "pin": pin,
    })
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _activate_note_to_self(manager, hub):
    token = _confirm(hub, _CORE, "NoteToSelf")
    manager.set_session("NoteToSelf", token, mode="passthrough")


def _seed_grant(backend, participant_hex, app_name):
    with sqlite3.connect(backend.path_local_db) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO granted_app
            (participant_id, team_name, app_name, first_granted_at)
            VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00')
        """, (bytes.fromhex(participant_hex), _TEAM, app_name))


def test_known_apps_merges_registered_and_granted(playground_dir):
    backend, participant_hex, hub, manager, _web = _setup(playground_dir)
    _activate_note_to_self(manager, hub)
    _confirm(hub, _FILES, _TEAM)
    _seed_grant(backend, participant_hex, "SmallSeaCollectiveFi1es")

    apps = manager.known_apps(_TEAM)

    assert [app["app_name"] for app in apps] == sorted(
        app["app_name"] for app in apps
    )
    sources = {app["app_name"]: app["sources"] for app in apps}
    assert sources[_CORE] == ["registered"]
    assert sources[_FILES] == ["registered", "granted"]
    assert sources["SmallSeaCollectiveFi1es"] == ["granted"]


def test_known_apps_without_hub_shows_registered_only(playground_dir):
    _backend, _participant_hex, _hub, manager, _web = _setup(playground_dir)

    apps = manager.known_apps(_TEAM)

    assert {app["app_name"] for app in apps} == {_CORE, _FILES}
    assert all(app["sources"] == ["registered"] for app in apps)


def test_team_detail_page_lists_known_apps(playground_dir):
    backend, participant_hex, hub, manager, web = _setup(playground_dir)
    _activate_note_to_self(manager, hub)
    _confirm(hub, _FILES, _TEAM)
    _seed_grant(backend, participant_hex, "SmallSeaCollectiveFi1es")

    response = web.get(f"/teams/{_TEAM}")

    assert response.status_code == 200
    assert _CORE in response.text
    assert _FILES in response.text
    assert "SmallSeaCollectiveFi1es" in response.text
    assert "registered, granted" in response.text
