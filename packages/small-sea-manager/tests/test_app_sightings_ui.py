"""Micro tests for the Manager app-bootstrap sightings UI."""

import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import TeamManager
from small_sea_manager.web import create_app


_APP = "SharedFileVault"
_CORE_APP = "SmallSeaCollectiveCore"
_TEAM = "ProjectX"
_CLIENT = "SharedFileVaultTest"


class _ButtonParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_button = False
        self._current = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self._in_button = True
            self._current = []

    def handle_data(self, data):
        if self._in_button:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag == "button" and self._in_button:
            self.buttons.append(" ".join("".join(self._current).split()))
            self._in_button = False


def _button_labels(html):
    parser = _ButtonParser()
    parser.feed(html)
    return [label for label in parser.buttons if label != "Refresh"]


def _assert_fragment_response(response):
    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert '<div id="app-sightings">' in response.text


def _fresh_env(root):
    backend = SmallSea.SmallSeaBackend(root_dir=root)
    participant_hex = Provisioning.create_new_participant(root, "alice")
    Provisioning.create_team(root, participant_hex, _TEAM)
    hub_app.state.backend = backend
    return backend, participant_hex, TestClient(hub_app)


def _request_app_session(client, app_name=_APP, team_name=_TEAM):
    return client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": app_name,
            "team": team_name,
            "client": _CLIENT,
        },
    )


def _open_session(client, app_name, team_name):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": app_name,
            "team": team_name,
            "client": "Smoke Tests",
            "mode": "passthrough",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": body["pending_id"], "pin": body["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _open_core_session(client):
    return _open_session(client, _CORE_APP, "NoteToSelf")


def _manager_web(root, participant_hex, hub_client):
    app = create_app(root, participant_hex)
    manager = TeamManager(root, participant_hex, _http_client=hub_client)
    manager.set_session("NoteToSelf", _open_core_session(hub_client), mode="passthrough")
    app.state.manager = manager
    return TestClient(app), manager


def _note_to_self_db(root, participant_hex):
    return Path(root) / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"


def _team_db(root, participant_hex, team_name=_TEAM):
    return Path(root) / "Participants" / participant_hex / team_name / "Sync" / "core.db"


def _team_id(db_path, team_name):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT id FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()[0]


def _insert_app(db_path, app_id, app_name=_APP):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO app (id, name) VALUES (?, ?)", (app_id, app_name))
        conn.commit()


def _insert_note_to_self_berth(db_path, berth_id, app_id):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO team_app_berth (id, team_id, app_id) VALUES (?, ?, ?)",
            (berth_id, _team_id(db_path, "NoteToSelf"), app_id),
        )
        conn.commit()


def _insert_team_berth(db_path, berth_id, app_id):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id) VALUES (?, ?)",
            (berth_id, app_id),
        )
        conn.commit()


def _app_row_count(db_path, app_name=_APP):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM app WHERE name = ?",
            (app_name,),
        ).fetchone()[0]


def _berth_count_for_app(db_path, app_name=_APP):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            """
            SELECT COUNT(*)
            FROM team_app_berth
            WHERE app_id IN (SELECT id FROM app WHERE name = ?)
            """,
            (app_name,),
        ).fetchone()[0]


def _sighting(reason, team_name=_TEAM):
    return {
        "app_name": _APP,
        "team_name": team_name,
        "client_name": _CLIENT,
        "reason": reason,
        "last_seen_at": "2026-04-27T00:00:00+00:00",
        "seen_count": 1,
    }


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (
            "app_unknown",
            [
                "Register participant app",
                "Activate for team",
                "Dismiss participant prompt",
                "Dismiss team prompt",
            ],
        ),
        (
            "participant_berth_missing",
            [
                "Register participant app",
                "Dismiss participant prompt",
                "Dismiss team prompt",
            ],
        ),
        (
            "team_berth_missing",
            [
                "Activate for team",
                "Dismiss participant prompt",
                "Dismiss team prompt",
            ],
        ),
        (
            "app_friendly_name_ambiguous",
            [
                "Dismiss participant prompt",
                "Dismiss team prompt",
            ],
        ),
    ],
)
def test_app_sightings_fragment_renders_exact_actions_by_reason(
    playground_dir,
    monkeypatch,
    reason,
    expected,
):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    app = create_app(playground_dir, participant_hex)
    app.state.manager.set_session("NoteToSelf", "token", mode="passthrough")
    monkeypatch.setattr(app.state.manager, "refresh_app_sightings", lambda: [_sighting(reason)])
    client = TestClient(app)

    response = client.post("/app-sightings/refresh")

    _assert_fragment_response(response)
    assert _button_labels(response.text) == expected


def test_app_sightings_fragment_hides_team_actions_for_edge_rows(playground_dir, monkeypatch):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    app = create_app(playground_dir, participant_hex)
    app.state.manager.set_session("NoteToSelf", "token", mode="passthrough")
    monkeypatch.setattr(
        app.state.manager,
        "refresh_app_sightings",
        lambda: [
            {**_sighting("app_unknown", team_name="PhoneTeam"), "team_unavailable": True, "team_available": False},
            _sighting("app_unknown", team_name=None),
        ],
    )
    client = TestClient(app)

    response = client.post("/app-sightings/refresh")

    _assert_fragment_response(response)
    assert "Team not available on this device yet" in response.text
    assert response.text.count("Register participant app") == 2
    assert "Activate for team" not in response.text
    assert "Dismiss team prompt" not in response.text


def test_vault_bootstrap_loop_via_manager_ui(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    manager_client, _manager = _manager_web(backend.root_dir, participant_hex, hub_client)

    resp = _request_app_session(hub_client)
    assert resp.status_code == 409
    assert resp.json()["reason"] == "app_unknown"

    resp = manager_client.post("/app-sightings/refresh")
    _assert_fragment_response(resp)
    assert "Register participant app" in resp.text
    assert "Activate for team" in resp.text

    resp = manager_client.post("/app-sightings/register", data={"app_name": _APP})
    _assert_fragment_response(resp)
    assert "team_berth_missing" in resp.text
    assert "Activate for team" in resp.text

    resp = manager_client.post(
        "/app-sightings/activate",
        data={"team_name": _TEAM, "app_name": _APP},
    )
    _assert_fragment_response(resp)
    assert "No app-bootstrap prompts." in resp.text

    resp = _request_app_session(hub_client)
    assert resp.status_code == 200
    assert "pending_id" in resp.json()


@pytest.mark.parametrize(
    ("setup", "expected_reason"),
    [
        ("none", "app_unknown"),
        ("participant_only", "team_berth_missing"),
        ("team_only", "participant_berth_missing"),
        ("both", None),
        ("duplicate_participant", "app_friendly_name_ambiguous"),
        ("duplicate_team", "app_friendly_name_ambiguous"),
    ],
)
def test_manager_current_prompt_recheck_matches_hub_predicates(
    playground_dir,
    setup,
    expected_reason,
):
    root = Path(playground_dir) / setup
    root.mkdir()
    backend, participant_hex, hub_client = _fresh_env(root)
    nts_db = _note_to_self_db(root, participant_hex)
    team_db = _team_db(root, participant_hex)

    if setup in ("participant_only", "both"):
        Provisioning.register_app_for_participant(root, participant_hex, _APP)
    if setup in ("team_only", "both"):
        Provisioning.activate_app_for_team(root, participant_hex, _TEAM, _APP)
    if setup == "duplicate_participant":
        for index in range(2):
            app_id = bytes([0x10 + index]) * 16
            _insert_app(nts_db, app_id)
            _insert_note_to_self_berth(nts_db, bytes([0x20 + index]) * 16, app_id)
    if setup == "duplicate_team":
        for index in range(2):
            app_id = bytes([0x30 + index]) * 16
            _insert_app(team_db, app_id)
            _insert_team_berth(team_db, bytes([0x40 + index]) * 16, app_id)

    backend.record_unknown_app_sighting(
        participant_hex,
        _APP,
        _TEAM,
        _CLIENT,
        "app_unknown",
    )

    hub_resp = _request_app_session(hub_client)
    token = _open_core_session(hub_client)
    manager = TeamManager(root, participant_hex, _http_client=hub_client)
    manager.set_session("NoteToSelf", token, mode="passthrough")
    prompts = manager.refresh_app_sightings()

    if expected_reason is None:
        assert hub_resp.status_code == 200
        assert prompts == []
    else:
        assert hub_resp.status_code == 409
        assert hub_resp.json()["reason"] == expected_reason
        assert [prompt["reason"] for prompt in prompts] == [expected_reason]


def test_unknown_team_sighting_stays_visible_and_conservative(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    backend.record_unknown_app_sighting(
        participant_hex,
        _APP,
        "PhoneTeam",
        _CLIENT,
        "app_unknown",
    )
    token = _open_core_session(hub_client)
    manager = TeamManager(backend.root_dir, participant_hex, _http_client=hub_client)
    manager.set_session("NoteToSelf", token, mode="passthrough")

    prompts = manager.refresh_app_sightings()

    assert len(prompts) == 1
    assert prompts[0]["reason"] == "app_unknown"
    assert prompts[0]["team_unavailable"] is True
    assert prompts[0]["team_available"] is False


def test_unknown_team_dismissal_check_does_not_create_sidecar(playground_dir):
    backend, participant_hex, _hub_client = _fresh_env(playground_dir)
    sighting = _sighting("app_unknown", team_name="PhoneTeam")
    sidecar = (
        Path(backend.root_dir)
        / "Participants"
        / participant_hex
        / "PhoneTeam"
        / "admission-events-local.db"
    )

    assert Provisioning.app_sighting_dismissed(
        backend.root_dir,
        participant_hex,
        sighting,
    ) is False
    assert not sidecar.exists()


def test_app_sightings_register_and_activate_are_double_submit_safe(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    manager_client, _manager = _manager_web(backend.root_dir, participant_hex, hub_client)
    _request_app_session(hub_client)

    for _ in range(2):
        resp = manager_client.post("/app-sightings/register", data={"app_name": _APP})
        _assert_fragment_response(resp)
    assert _app_row_count(_note_to_self_db(backend.root_dir, participant_hex)) == 1
    assert _berth_count_for_app(_note_to_self_db(backend.root_dir, participant_hex)) == 1

    for _ in range(2):
        resp = manager_client.post(
            "/app-sightings/activate",
            data={"team_name": _TEAM, "app_name": _APP},
        )
        _assert_fragment_response(resp)
    assert _app_row_count(_team_db(backend.root_dir, participant_hex)) == 1
    assert _berth_count_for_app(_team_db(backend.root_dir, participant_hex)) == 1


def test_app_sightings_dismiss_does_not_register_app(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    manager_client, _manager = _manager_web(backend.root_dir, participant_hex, hub_client)
    _request_app_session(hub_client)

    resp = manager_client.post(
        "/app-sightings/dismiss-participant",
        data={"app_name": _APP},
    )

    _assert_fragment_response(resp)
    assert "No app-bootstrap prompts." in resp.text
    assert _app_row_count(_note_to_self_db(backend.root_dir, participant_hex)) == 0
    assert _berth_count_for_app(_note_to_self_db(backend.root_dir, participant_hex)) == 0


def test_app_sighting_dismissal_scope_across_teams(playground_dir):
    backend, participant_hex, hub_client = _fresh_env(playground_dir)
    Provisioning.create_team(backend.root_dir, participant_hex, "ProjectY")
    for team_name in (_TEAM, "ProjectY"):
        backend.record_unknown_app_sighting(
            participant_hex,
            _APP,
            team_name,
            f"{_CLIENT}-{team_name}",
            "app_unknown",
        )
    token = _open_core_session(hub_client)
    manager = TeamManager(backend.root_dir, participant_hex, _http_client=hub_client)
    manager.set_session("NoteToSelf", token, mode="passthrough")

    assert {prompt["team_name"] for prompt in manager.refresh_app_sightings()} == {
        _TEAM,
        "ProjectY",
    }

    manager.dismiss_team_app_sighting(_TEAM, _APP)

    assert [prompt["team_name"] for prompt in manager.refresh_app_sightings()] == [
        "ProjectY"
    ]

    manager.dismiss_participant_app_sighting(_APP)

    assert manager.refresh_app_sightings() == []
