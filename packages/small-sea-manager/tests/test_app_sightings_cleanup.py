"""Micro tests for TeamManager.refresh_app_sightings cleanup integration.

Covers Phase 3 of the sightings cleanup branch: per-row clear after
re-evaluation, stale pruning after the snapshot, dismissed/unresolved
preservation, conservative prompts for unknown teams, and non-fatal cleanup
failures.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import TeamManager


_VAULT_APP = "SharedFileVault"
_CORE_APP = "SmallSeaCollectiveCore"
_TEAM = "ProjectX"
_OTHER_TEAM = "ProjectY"
_CLIENT = "SharedFileVaultTest"


def _fresh_env(playground_dir, *, now_fn=None, sighting_stale_window=None):
    backend = SmallSea.SmallSeaBackend(
        root_dir=playground_dir,
        now_fn=now_fn,
        sighting_stale_window=sighting_stale_window,
    )
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, participant_hex, _TEAM)
    hub_app.state.backend = backend
    return backend, participant_hex, TestClient(hub_app)


def _open_core_session(client):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": _CORE_APP,
            "team": "NoteToSelf",
            "client": "Smoke Tests",
            "mode": "passthrough",
        },
    )
    body = resp.json()
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": body["pending_id"], "pin": body["pin"]},
    )
    return resp.json()


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


def _make_manager(backend, participant_hex, client):
    mgr = TeamManager(backend.root_dir, participant_hex, _http_client=client)
    token = _open_core_session(client)
    mgr.set_session("NoteToSelf", token, mode="passthrough")
    return mgr


def _hub_rows(backend):
    with sqlite3.connect(backend.path_local_db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT app_name, team_name, last_seen_at FROM unknown_app_sighting"
        ).fetchall()]


# ---- resolved rows are cleared ----


def test_resolved_row_is_cleared_after_registration_and_activation(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    mgr = _make_manager(backend, participant_hex, client)

    Provisioning.register_app_for_participant(
        backend.root_dir, participant_hex, _VAULT_APP,
    )
    Provisioning.activate_app_for_team(
        backend.root_dir, participant_hex, _TEAM, _VAULT_APP,
    )

    prompts = mgr.refresh_app_sightings()

    assert list(prompts) == []
    assert _hub_rows(backend) == []
    assert prompts.cleanup_warning is None


def test_resolved_row_cleared_even_when_dismissed(playground_dir):
    """Resolved + dismissed: dismissal is a UI preference and must not pin a
    resolved row in the Hub."""
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    mgr = _make_manager(backend, participant_hex, client)
    mgr.dismiss_team_app_sighting(_TEAM, _VAULT_APP)

    Provisioning.register_app_for_participant(
        backend.root_dir, participant_hex, _VAULT_APP,
    )
    Provisioning.activate_app_for_team(
        backend.root_dir, participant_hex, _TEAM, _VAULT_APP,
    )

    prompts = mgr.refresh_app_sightings()

    assert list(prompts) == []
    assert _hub_rows(backend) == []


# ---- dismissed unresolved rows remain ----


def test_dismissed_unresolved_row_remains(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    mgr = _make_manager(backend, participant_hex, client)
    mgr.dismiss_team_app_sighting(_TEAM, _VAULT_APP)

    prompts = mgr.refresh_app_sightings()

    assert list(prompts) == []
    rows = _hub_rows(backend)
    assert len(rows) == 1
    assert rows[0]["app_name"] == _VAULT_APP


def test_fresh_dismissed_unresolved_row_keeps_existing_display(playground_dir):
    """The Phase 3 reordering must not change display for the
    dismissed-unresolved-fresh path: still no prompt, still no clear."""
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    mgr = _make_manager(backend, participant_hex, client)
    mgr.dismiss_participant_app_sighting(_VAULT_APP)

    prompts = mgr.refresh_app_sightings()

    assert list(prompts) == []
    assert len(_hub_rows(backend)) == 1
    assert prompts.cleanup_warning is None


# ---- conservative prompts for unknown team ----


def test_team_not_locally_cloned_keeps_conservative_prompt(playground_dir):
    """Manager has no clone of _OTHER_TEAM, so the row gets a conservative
    prompt rather than being cleared. The Hub row must remain."""
    backend, participant_hex, client = _fresh_env(playground_dir)
    backend.record_unknown_app_sighting(
        participant_hex, _VAULT_APP, _OTHER_TEAM, _CLIENT, "app_unknown",
    )
    mgr = _make_manager(backend, participant_hex, client)

    prompts = mgr.refresh_app_sightings()

    assert len(prompts) == 1
    assert prompts[0]["app_name"] == _VAULT_APP
    assert prompts[0]["team_name"] == _OTHER_TEAM
    assert prompts[0].get("team_unavailable") is True
    rows = _hub_rows(backend)
    assert len(rows) == 1


# ---- stale prune is called after listing/evaluation ----


def test_stale_row_shown_once_from_pre_prune_snapshot(playground_dir):
    """A sighting older than the stale window should be visible from the
    first refresh after a long absence and gone from the next refresh."""
    stale_window = timedelta(days=30)
    far_past = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    times = iter([far_past, now, now, now, now])
    backend, participant_hex, client = _fresh_env(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=stale_window,
    )

    backend.record_unknown_app_sighting(
        participant_hex, _VAULT_APP, _TEAM, _CLIENT, "app_unknown",
    )
    mgr = _make_manager(backend, participant_hex, client)

    first = mgr.refresh_app_sightings()
    assert len(first) == 1

    second = mgr.refresh_app_sightings()
    assert list(second) == []
    assert _hub_rows(backend) == []


def test_stale_row_with_unknown_team_shown_once_then_pruned(playground_dir):
    """Stale + still-actionable conservative prompt: visible once, then
    pruned. Team-not-locally-cloned is the canonical 'still actionable but
    we cannot prove resolution' fixture."""
    stale_window = timedelta(days=30)
    far_past = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    times = iter([far_past, now, now, now, now])
    backend, participant_hex, client = _fresh_env(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=stale_window,
    )

    backend.record_unknown_app_sighting(
        participant_hex, _VAULT_APP, _OTHER_TEAM, _CLIENT, "app_unknown",
    )
    mgr = _make_manager(backend, participant_hex, client)

    first = mgr.refresh_app_sightings()
    assert len(first) == 1
    assert first[0].get("team_unavailable") is True

    second = mgr.refresh_app_sightings()
    assert list(second) == []
    assert _hub_rows(backend) == []


def test_stale_dismissed_unresolved_row_shown_zero_times_but_pruned(playground_dir):
    """Dismissal suppresses display; pruning still removes the stale row."""
    stale_window = timedelta(days=30)
    far_past = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    times = iter([far_past, now, now, now, now])
    backend, participant_hex, client = _fresh_env(
        playground_dir,
        now_fn=lambda: next(times),
        sighting_stale_window=stale_window,
    )

    backend.record_unknown_app_sighting(
        participant_hex, _VAULT_APP, _TEAM, _CLIENT, "app_unknown",
    )
    mgr = _make_manager(backend, participant_hex, client)
    mgr.dismiss_team_app_sighting(_TEAM, _VAULT_APP)

    prompts = mgr.refresh_app_sightings()

    assert list(prompts) == []
    assert _hub_rows(backend) == []


# ---- cleanup failures are non-fatal ----


def test_clear_failure_is_non_fatal_and_omits_resolved_prompt(playground_dir):
    """If the per-row clear call raises, refresh still returns the prompts
    it computed and surfaces a single warning. The resolved row is not added
    to prompts because its current_app_sighting_prompt is None."""
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    mgr = _make_manager(backend, participant_hex, client)
    Provisioning.register_app_for_participant(
        backend.root_dir, participant_hex, _VAULT_APP,
    )
    Provisioning.activate_app_for_team(
        backend.root_dir, participant_hex, _TEAM, _VAULT_APP,
    )

    session = mgr._open_note_to_self_session()
    real_clear = session.clear_app_sighting

    def _boom(**_kwargs):
        raise RuntimeError("hub flaked")

    session.clear_app_sighting = _boom
    try:
        prompts = mgr.refresh_app_sightings()
    finally:
        session.clear_app_sighting = real_clear

    assert list(prompts) == []
    assert prompts.cleanup_warning is not None
    assert "could not clear" in prompts.cleanup_warning


def test_prune_failure_is_non_fatal(playground_dir):
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    mgr = _make_manager(backend, participant_hex, client)
    session = mgr._open_note_to_self_session()
    real_prune = session.prune_stale_app_sightings

    def _boom():
        raise RuntimeError("hub flaked")

    session.prune_stale_app_sightings = _boom
    try:
        prompts = mgr.refresh_app_sightings()
    finally:
        session.prune_stale_app_sightings = real_prune

    assert len(prompts) == 1
    assert prompts.cleanup_warning is not None
    assert "prune" in prompts.cleanup_warning.lower()


# ---- refresh returns prompts not raw Hub rows ----


def test_refresh_returns_current_prompts_not_raw_hub_rows(playground_dir):
    """Hub rows record a stored reason; Manager refreshes the reason against
    current local state. After registration, an old app_unknown row should
    surface as team_berth_missing, not as the stored reason."""
    backend, participant_hex, client = _fresh_env(playground_dir)
    _request_vault_session(client)
    Provisioning.register_app_for_participant(
        backend.root_dir, participant_hex, _VAULT_APP,
    )
    mgr = _make_manager(backend, participant_hex, client)

    prompts = mgr.refresh_app_sightings()

    assert len(prompts) == 1
    assert prompts[0]["reason"] == "team_berth_missing"
    assert prompts[0]["stored_reason"] == "app_unknown"
