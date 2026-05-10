"""Micro tests for the Hub app-bootstrap sighting cleanup endpoints.

Covers POST /sightings/clear and POST /sightings/prune-stale, plus the
canonical timestamp format and participant-scoped boundary rules.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.server import app


_VAULT_APP = "SharedFileVault"
_CORE_APP = "SmallSeaCollectiveCore"
_TEAM = "ProjectX"
_CLIENT = "SharedFileVaultTest"


def _backend(playground_dir, *, now_fn=None, sighting_stale_window=None):
    return SmallSea.SmallSeaBackend(
        root_dir=playground_dir,
        now_fn=now_fn,
        sighting_stale_window=sighting_stale_window,
    )


def _fresh_env(playground_dir, *, now_fn=None, sighting_stale_window=None):
    backend = _backend(
        playground_dir,
        now_fn=now_fn,
        sighting_stale_window=sighting_stale_window,
    )
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, participant_hex, _TEAM)
    app.state.backend = backend
    return backend, participant_hex, TestClient(app)


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


def _list_sightings(client, token):
    resp = client.get(
        "/sightings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _clear_payload_from_sighting(sighting):
    return {
        "app_name": sighting["app_name"],
        "team_name": sighting["team_name"],
        "client_name": sighting["client_name"],
        "last_seen_at": sighting["last_seen_at"],
    }


def _clear(client, token, payload):
    return client.post(
        "/sightings/clear",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _prune_stale(client, token, *, body=None):
    if body is None:
        return client.post(
            "/sightings/prune-stale",
            headers={"Authorization": f"Bearer {token}"},
        )
    return client.post(
        "/sightings/prune-stale",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def _sighting_count(backend):
    with sqlite3.connect(backend.path_local_db) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM unknown_app_sighting"
        ).fetchone()[0]


# ---- canonical timestamp format ----


def test_record_uses_canonical_six_digit_microseconds(playground_dir):
    """An instant whose microsecond value is zero must still serialize with
    six fractional digits, so lexicographic SQL comparison stays sound."""
    fixed = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    backend, _, client = _fresh_env(playground_dir, now_fn=lambda: fixed)

    resp = _request_vault_session(client)
    assert resp.status_code == 409

    with sqlite3.connect(backend.path_local_db) as conn:
        row = conn.execute(
            "SELECT first_seen_at, last_seen_at FROM unknown_app_sighting"
        ).fetchone()

    assert row[0] == "2026-05-01T12:00:00.000000+00:00"
    assert row[1] == "2026-05-01T12:00:00.000000+00:00"


def test_helper_rejects_naive_datetime():
    with pytest.raises(ValueError):
        SmallSea._format_sighting_timestamp(datetime(2026, 5, 1, 12, 0, 0))


def test_get_sightings_last_seen_at_is_byte_identical(playground_dir):
    """Manager echoes last_seen_at on POST /sightings/clear; the GET response
    must carry the column verbatim or the precondition will silently fail."""
    fixed = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    backend, _, client = _fresh_env(playground_dir, now_fn=lambda: fixed)
    _request_vault_session(client)
    token = _open_core_session(client)

    listed = _list_sightings(client, token)
    assert listed[0]["last_seen_at"] == "2026-05-01T12:00:00.000000+00:00"

    with sqlite3.connect(backend.path_local_db) as conn:
        stored = conn.execute(
            "SELECT last_seen_at FROM unknown_app_sighting LIMIT 1"
        ).fetchone()[0]
    assert listed[0]["last_seen_at"] == stored


# ---- POST /sightings/clear ----


def test_clear_succeeds_for_matching_tuple_and_last_seen_at(playground_dir):
    backend, _, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    token = _open_core_session(client)
    sighting = _list_sightings(client, token)[0]

    resp = _clear(client, token, _clear_payload_from_sighting(sighting))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted_count": 1}
    assert _list_sightings(client, token) == []
    assert _sighting_count(backend) == 0


def test_clear_idempotent_on_repeat(playground_dir):
    _, _, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    token = _open_core_session(client)
    sighting = _list_sightings(client, token)[0]

    first = _clear(client, token, _clear_payload_from_sighting(sighting))
    second = _clear(client, token, _clear_payload_from_sighting(sighting))

    assert first.json() == {"deleted_count": 1}
    assert second.status_code == 200
    assert second.json() == {"deleted_count": 0}


def test_clear_with_stale_last_seen_at_returns_zero(playground_dir):
    """List/clear race: an app retry between Manager listing and clearing
    bumps last_seen_at; the precondition must keep the bumped row in place."""
    times = iter([
        datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 1, 13, 0, 0, tzinfo=timezone.utc),
    ])
    backend, _, client = _fresh_env(playground_dir, now_fn=lambda: next(times))

    _request_vault_session(client)
    token = _open_core_session(client)
    snapshot = _list_sightings(client, token)[0]
    _request_vault_session(client)  # bumps last_seen_at to 13:00

    resp = _clear(client, token, _clear_payload_from_sighting(snapshot))

    assert resp.status_code == 200
    assert resp.json() == {"deleted_count": 0}
    assert _sighting_count(backend) == 1


def test_clear_unauthorized_without_bearer(playground_dir):
    _, _, client = _fresh_env(playground_dir)
    _request_vault_session(client)

    resp = client.post(
        "/sightings/clear",
        json={
            "app_name": _VAULT_APP,
            "team_name": _TEAM,
            "client_name": _CLIENT,
            "last_seen_at": "ignored",
        },
    )
    assert resp.status_code == 401


def test_clear_rejects_non_manager_session(playground_dir):
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

    resp = client.post(
        "/sightings/clear",
        json={
            "app_name": _VAULT_APP,
            "team_name": _TEAM,
            "client_name": _CLIENT,
            "last_seen_at": "ignored",
        },
        headers={"Authorization": f"Bearer {vault_token}"},
    )
    assert resp.status_code == 403


def test_clear_accepts_team_name_null(playground_dir):
    """Wire shape supports null team_name even though current schema does not
    store NULL — so a future raw sighting with no team can still be cleared
    without Manager normalizing the value."""
    _, _, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    token = _open_core_session(client)

    resp = _clear(client, token, {
        "app_name": _VAULT_APP,
        "team_name": None,
        "client_name": _CLIENT,
        "last_seen_at": "2026-05-01T12:00:00.000000+00:00",
    })

    assert resp.status_code == 200
    assert resp.json() == {"deleted_count": 0}


def test_clear_does_not_register_or_activate_app(playground_dir):
    """Cleaning up a sighting must not produce app/berth rows."""
    backend, participant_hex, client = _fresh_env(playground_dir)
    nts_db = (
        backend.root_dir
        / "Participants"
        / participant_hex
        / "NoteToSelf"
        / "Sync"
        / "core.db"
    )

    _request_vault_session(client)
    token = _open_core_session(client)
    sighting = _list_sightings(client, token)[0]
    _clear(client, token, _clear_payload_from_sighting(sighting))

    with sqlite3.connect(nts_db) as conn:
        rows = conn.execute(
            "SELECT id FROM app WHERE name = ?",
            (_VAULT_APP,),
        ).fetchall()
    assert rows == []


def test_clear_does_not_prune_unrelated_stale_rows(playground_dir):
    times = iter([
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),  # stale row insert
        datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),  # fresh row insert
        datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
    ])
    backend, participant_hex, client = _fresh_env(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=timedelta(days=30),
    )

    backend.record_unknown_app_sighting(
        participant_hex, "AppOld", _TEAM, _CLIENT, "app_unknown"
    )
    backend.record_unknown_app_sighting(
        participant_hex, _VAULT_APP, _TEAM, _CLIENT, "app_unknown"
    )

    token = _open_core_session(client)
    sightings = _list_sightings(client, token)
    fresh = next(s for s in sightings if s["app_name"] == _VAULT_APP)
    _clear(client, token, _clear_payload_from_sighting(fresh))

    remaining = _list_sightings(client, token)
    assert {s["app_name"] for s in remaining} == {"AppOld"}


def test_retry_after_clear_records_fresh_sighting(playground_dir):
    """Retrying an app after cleanup creates a fresh row, so cleanup is not a
    durable rejection."""
    backend, _, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    token = _open_core_session(client)
    sighting = _list_sightings(client, token)[0]
    _clear(client, token, _clear_payload_from_sighting(sighting))

    resp = _request_vault_session(client)
    assert resp.status_code == 409

    rows = _list_sightings(client, token)
    assert len(rows) == 1
    assert rows[0]["seen_count"] == 1


# ---- POST /sightings/prune-stale ----


def test_prune_stale_deletes_only_strictly_older(playground_dir):
    """Strict less-than: rows exactly at the cutoff survive."""
    cutoff = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    stale_window = timedelta(days=30)
    now = cutoff + stale_window

    times = iter([
        cutoff - timedelta(microseconds=1),  # before the cutoff -> pruned
        cutoff,                              # equal to cutoff   -> survives
        cutoff + timedelta(microseconds=1),  # after the cutoff  -> survives
        now,                                 # the prune call   -> reads now
    ])
    backend, participant_hex, client = _fresh_env(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=stale_window,
    )

    backend.record_unknown_app_sighting(
        participant_hex, "AppBefore", _TEAM, _CLIENT, "app_unknown"
    )
    backend.record_unknown_app_sighting(
        participant_hex, "AppEqual", _TEAM, _CLIENT, "app_unknown"
    )
    backend.record_unknown_app_sighting(
        participant_hex, "AppAfter", _TEAM, _CLIENT, "app_unknown"
    )

    token = _open_core_session(client)
    resp = _prune_stale(client, token)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"pruned_count": 1}

    remaining = {row["app_name"] for row in _list_sightings(client, token)}
    assert remaining == {"AppEqual", "AppAfter"}


def test_prune_stale_accepts_empty_or_object_body(playground_dir):
    _, _, client = _fresh_env(playground_dir)
    token = _open_core_session(client)

    resp_empty = _prune_stale(client, token)
    resp_object = _prune_stale(client, token, body={})

    assert resp_empty.status_code == 200
    assert resp_object.status_code == 200
    assert resp_empty.json() == {"pruned_count": 0}
    assert resp_object.json() == {"pruned_count": 0}


def test_prune_stale_unauthorized_without_bearer(playground_dir):
    _, _, client = _fresh_env(playground_dir)
    resp = client.post("/sightings/prune-stale")
    assert resp.status_code == 401


def test_prune_stale_rejects_non_manager_session(playground_dir):
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

    resp = _prune_stale(client, vault_token)
    assert resp.status_code == 403


def test_prune_stale_is_participant_scoped(playground_dir):
    """A Manager session for participant A must not delete participant B's
    stale rows."""
    stale_window = timedelta(days=30)
    far_past = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    times = iter([far_past, far_past, now])
    backend = _backend(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=stale_window,
    )
    alice_hex = Provisioning.create_new_participant(playground_dir, "alice")
    bob_hex = Provisioning.create_new_participant(playground_dir, "bob")
    Provisioning.create_team(playground_dir, alice_hex, _TEAM)
    Provisioning.create_team(playground_dir, bob_hex, _TEAM)
    app.state.backend = backend
    client = TestClient(app)

    backend.record_unknown_app_sighting(
        alice_hex, _VAULT_APP, _TEAM, _CLIENT, "app_unknown"
    )
    backend.record_unknown_app_sighting(
        bob_hex, _VAULT_APP, _TEAM, _CLIENT, "app_unknown"
    )

    alice_token = _open_session(client, _CORE_APP, "NoteToSelf")
    resp = _prune_stale(client, alice_token)
    assert resp.json() == {"pruned_count": 1}

    with sqlite3.connect(backend.path_local_db) as conn:
        remaining = conn.execute(
            "SELECT participant_hex FROM unknown_app_sighting"
        ).fetchall()
    assert remaining == [(bob_hex,)]


def test_record_does_not_prune_unrelated_stale_rows(playground_dir):
    """Pruning must not be coupled to ordinary record/upsert work."""
    stale_window = timedelta(days=30)
    far_past = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    times = iter([far_past, now])
    backend, participant_hex, _ = _fresh_env(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=stale_window,
    )

    backend.record_unknown_app_sighting(
        participant_hex, "AppOld", _TEAM, _CLIENT, "app_unknown"
    )
    backend.record_unknown_app_sighting(
        participant_hex, _VAULT_APP, _TEAM, _CLIENT, "app_unknown"
    )

    with sqlite3.connect(backend.path_local_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM unknown_app_sighting"
        ).fetchone()[0]
    assert count == 2


def test_get_sightings_does_not_mutate(playground_dir):
    """Read-only contract: GET /sightings must not prune even when stale rows
    are present and the prune endpoint would have removed them."""
    stale_window = timedelta(days=30)
    far_past = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    times = iter([far_past, now])
    backend, participant_hex, client = _fresh_env(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=stale_window,
    )

    backend.record_unknown_app_sighting(
        participant_hex, "AppOld", _TEAM, _CLIENT, "app_unknown"
    )
    token = _open_core_session(client)

    listed = _list_sightings(client, token)

    assert len(listed) == 1
    assert _sighting_count(backend) == 1
