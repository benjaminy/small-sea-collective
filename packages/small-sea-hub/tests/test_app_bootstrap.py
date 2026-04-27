"""Micro tests for app bootstrap via Hub sightings and Manager registration."""

import sqlite3

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager


_VAULT_APP = "SharedFileVault"
_CORE_APP = "SmallSeaCollectiveCore"
_TEAM = "ProjectX"
_CLIENT = "SharedFileVaultTest"


def _fresh_env(playground_dir):
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, participant_hex, _TEAM)
    app.state.backend = backend
    return backend, participant_hex, TestClient(app)


def _app_rows(db_path, app_name):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT id, name FROM app WHERE name = ?",
            (app_name,),
        ).fetchall()


def _note_to_self_db(root_dir, participant_hex):
    return (
        root_dir
        / "Participants"
        / participant_hex
        / "NoteToSelf"
        / "Sync"
        / "core.db"
    )


def _team_db(root_dir, participant_hex, team_name):
    return (
        root_dir
        / "Participants"
        / participant_hex
        / team_name
        / "Sync"
        / "core.db"
    )


def _request_vault_session(client):
    return client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": _VAULT_APP,
            "team": _TEAM,
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


def _sighting_rows(backend):
    with sqlite3.connect(backend.path_local_db) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT participant_hex, app_name, team_name, client_name, reason, seen_count
                FROM unknown_app_sighting
                """
            ).fetchall()
        ]


def _team_id(db_path, team_name):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT id FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()[0]


def _insert_app(db_path, app_id, app_name):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO app (id, name) VALUES (?, ?)", (app_id, app_name))
        conn.commit()


def _insert_note_to_self_berth(db_path, berth_id, app_id, team_id):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id, team_id) VALUES (?, ?, ?)",
            (berth_id, app_id, team_id),
        )
        conn.commit()


def _insert_team_berth(db_path, berth_id, app_id):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id) VALUES (?, ?)",
            (berth_id, app_id),
        )
        conn.commit()


def _team_berth_rows(db_path, app_id):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT id, app_id FROM team_app_berth WHERE app_id = ? ORDER BY id",
            (app_id,),
        ).fetchall()


def _request_core_team_session(client):
    return client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": _CORE_APP,
            "team": _TEAM,
            "client": "Smoke Tests",
        },
    )


def _assert_bootstrap_rejection(resp, reason, app_name=_VAULT_APP):
    assert resp.status_code == 409
    assert resp.json() == {
        "error": "app_bootstrap_required",
        "reason": reason,
        "app": app_name,
        "team": _TEAM,
    }


def test_unknown_vault_request_does_not_register_app_in_production(playground_dir):
    """Production request path reports bootstrap need without implicit DB writes."""
    backend, participant_hex, client = _fresh_env(playground_dir)
    nts_db = _note_to_self_db(backend.root_dir, participant_hex)
    team_db = _team_db(backend.root_dir, participant_hex, _TEAM)

    assert _app_rows(nts_db, _VAULT_APP) == []
    assert _app_rows(team_db, _VAULT_APP) == []

    resp = _request_vault_session(client)

    assert _app_rows(nts_db, _VAULT_APP) == []
    assert _app_rows(team_db, _VAULT_APP) == []
    _assert_bootstrap_rejection(resp, "app_unknown")
    assert _sighting_rows(backend) == [
        {
            "participant_hex": participant_hex,
            "app_name": _VAULT_APP,
            "team_name": _TEAM,
            "client_name": _CLIENT,
            "reason": "app_unknown",
            "seen_count": 1,
        }
    ]

    resp = _request_vault_session(client)

    assert resp.status_code == 409
    assert _sighting_rows(backend)[0]["seen_count"] == 2

    resp = client.get("/sightings")

    assert resp.status_code == 401

    token = _open_core_session(client)
    resp = client.get("/sightings", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["seen_count"] == 2


def test_sighting_retry_upserts_across_hub_restart(playground_dir):
    backend, _participant_hex, client = _fresh_env(playground_dir)

    resp = _request_vault_session(client)
    assert resp.status_code == 409
    assert _sighting_rows(backend)[0]["seen_count"] == 1

    restarted = SmallSea.SmallSeaBackend(root_dir=backend.root_dir)
    app.state.backend = restarted
    resp = _request_vault_session(client)

    assert resp.status_code == 409
    assert _sighting_rows(restarted)[0]["seen_count"] == 2


def test_sightings_requires_manager_core_session(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    Provisioning.register_app_for_participant(
        backend.root_dir,
        participant_hex,
        _VAULT_APP,
    )
    Provisioning.activate_app_for_team(
        backend.root_dir,
        participant_hex,
        _TEAM,
        _VAULT_APP,
    )
    vault_token = _open_session(client, _VAULT_APP, _TEAM)

    resp = client.get(
        "/sightings",
        headers={"Authorization": f"Bearer {vault_token}"},
    )

    assert resp.status_code == 403


def test_manager_refreshes_sightings_and_applies_local_disposition(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    resp = _request_vault_session(client)
    assert resp.status_code == 409
    token = _open_core_session(client)
    manager = TeamManager(backend.root_dir, participant_hex, _http_client=client)
    manager.set_session("NoteToSelf", token, mode="passthrough")

    sightings = manager.refresh_app_sightings()

    assert len(sightings) == 1
    assert sightings[0]["app_name"] == _VAULT_APP

    manager.dismiss_team_app_sighting(_TEAM, _VAULT_APP)
    resp = _request_vault_session(client)

    assert resp.status_code == 409
    assert _sighting_rows(backend)[0]["seen_count"] == 2
    assert manager.refresh_app_sightings() == []


def test_manager_participant_app_disposition_suppresses_sighting(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    resp = _request_vault_session(client)
    assert resp.status_code == 409
    token = _open_core_session(client)
    manager = TeamManager(backend.root_dir, participant_hex, _http_client=client)
    manager.set_session("NoteToSelf", token, mode="passthrough")

    manager.dismiss_participant_app_sighting(_VAULT_APP)

    assert manager.refresh_app_sightings() == []


def test_team_app_disposition_requires_known_team(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    manager = TeamManager(backend.root_dir, participant_hex, _http_client=client)

    try:
        manager.dismiss_team_app_sighting("MissingTeam", _VAULT_APP)
    except ValueError as exc:
        assert "MissingTeam" in str(exc)
    else:
        raise AssertionError("Expected unknown team dismissal to fail")

    sidecar = (
        backend.root_dir
        / "Participants"
        / participant_hex
        / "MissingTeam"
        / "admission-events-local.db"
    )
    assert not sidecar.exists()


def test_participant_berth_missing_rejection(playground_dir):
    backend, _participant_hex, client = _fresh_env(playground_dir)
    team_db = _team_db(backend.root_dir, _participant_hex, _TEAM)
    app_id = b"\x11" * 16
    _insert_app(team_db, app_id, _VAULT_APP)
    _insert_team_berth(team_db, b"\x12" * 16, app_id)

    resp = _request_vault_session(client)

    _assert_bootstrap_rejection(resp, "participant_berth_missing")
    assert _sighting_rows(backend)[0]["reason"] == "participant_berth_missing"


def test_team_berth_missing_rejection(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    nts_db = _note_to_self_db(backend.root_dir, participant_hex)
    app_id = b"\x21" * 16
    _insert_app(nts_db, app_id, _VAULT_APP)
    _insert_note_to_self_berth(
        nts_db,
        b"\x22" * 16,
        app_id,
        _team_id(nts_db, _TEAM),
    )

    resp = _request_vault_session(client)

    _assert_bootstrap_rejection(resp, "team_berth_missing")
    assert _sighting_rows(backend)[0]["reason"] == "team_berth_missing"


def test_friendly_name_ambiguous_rejection(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    nts_db = _note_to_self_db(backend.root_dir, participant_hex)
    team_id = _team_id(nts_db, _TEAM)
    for index in range(2):
        app_id = bytes([0x31 + index]) * 16
        _insert_app(nts_db, app_id, _VAULT_APP)
        _insert_note_to_self_berth(
            nts_db,
            bytes([0x41 + index]) * 16,
            app_id,
            team_id,
        )

    before = _app_rows(nts_db, _VAULT_APP)

    resp = _request_vault_session(client)

    _assert_bootstrap_rejection(resp, "app_friendly_name_ambiguous")
    assert _sighting_rows(backend)[0]["reason"] == "app_friendly_name_ambiguous"
    assert _app_rows(nts_db, _VAULT_APP) == before


def test_multiple_berths_for_one_app_is_ambiguous(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    nts_db = _note_to_self_db(backend.root_dir, participant_hex)
    team_db = _team_db(backend.root_dir, participant_hex, _TEAM)
    nts_app_id = b"\x51" * 16
    team_app_id = b"\x52" * 16
    _insert_app(nts_db, nts_app_id, _VAULT_APP)
    _insert_note_to_self_berth(
        nts_db,
        b"\x53" * 16,
        nts_app_id,
        _team_id(nts_db, _TEAM),
    )
    _insert_app(team_db, team_app_id, _VAULT_APP)
    _insert_team_berth(team_db, b"\x54" * 16, team_app_id)
    _insert_team_berth(team_db, b"\x55" * 16, team_app_id)
    before = _team_berth_rows(team_db, team_app_id)

    resp = _request_vault_session(client)

    _assert_bootstrap_rejection(resp, "app_friendly_name_ambiguous")
    assert _team_berth_rows(team_db, team_app_id) == before


def test_cross_scope_name_bridge_allows_distinct_random_app_ids(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    nts_db = _note_to_self_db(backend.root_dir, participant_hex)
    team_db = _team_db(backend.root_dir, participant_hex, _TEAM)
    nts_app_id = b"\x61" * 16
    team_app_id = b"\x62" * 16
    assert nts_app_id != team_app_id
    _insert_app(nts_db, nts_app_id, _VAULT_APP)
    _insert_note_to_self_berth(
        nts_db,
        b"\x63" * 16,
        nts_app_id,
        _team_id(nts_db, _TEAM),
    )
    _insert_app(team_db, team_app_id, _VAULT_APP)
    _insert_team_berth(team_db, b"\x64" * 16, team_app_id)

    resp = _request_vault_session(client)

    assert resp.status_code == 200
    assert "pending_id" in resp.json()


def test_core_team_session_requires_participant_core_berth(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    nts_db = _note_to_self_db(backend.root_dir, participant_hex)

    resp = _request_core_team_session(client)

    assert resp.status_code == 200
    assert "pending_id" in resp.json()

    with sqlite3.connect(nts_db) as conn:
        conn.execute(
            """
            DELETE FROM team_app_berth
            WHERE app_id = (SELECT id FROM app WHERE name = ?)
            """,
            (_CORE_APP,),
        )
        conn.commit()

    resp = _request_core_team_session(client)

    _assert_bootstrap_rejection(resp, "participant_berth_missing", app_name=_CORE_APP)
    assert _sighting_rows(backend)[0]["reason"] == "participant_berth_missing"


def test_vault_bootstrap_loop_rejects_then_registers_then_activates(playground_dir):
    """Full loop: sighting, participant registration, team activation, success."""
    backend, participant_hex, client = _fresh_env(playground_dir)

    resp = _request_vault_session(client)

    assert resp.status_code == 409
    assert resp.json()["reason"] == "app_unknown"

    token = _open_core_session(client)
    resp = client.get("/sightings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()[0]["app_name"] == _VAULT_APP

    Provisioning.register_app_for_participant(
        backend.root_dir,
        participant_hex,
        _VAULT_APP,
    )
    resp = _request_vault_session(client)

    assert resp.status_code == 409
    assert resp.json()["reason"] == "team_berth_missing"

    Provisioning.activate_app_for_team(
        backend.root_dir,
        participant_hex,
        _TEAM,
        _VAULT_APP,
    )
    resp = _request_vault_session(client)

    assert resp.status_code == 200
    assert "pending_id" in resp.json()
