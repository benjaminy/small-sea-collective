"""Micro tests for NoteToSelf refresh, team discovery, and joined_locally semantics.

Covers:
- list_known_teams / get_team return joined_locally correctly
- device-local NoteToSelf state is not synced by refresh
- two-device same-identity: device B does not see device A's team before refresh,
  but does see it (with joined_locally=False) after refresh
- device B does not auto-create a local team clone after refresh

The two-device tests use MinIO because Hub-mediated NoteToSelf sync requires a
real cloud backend (Hub's storage adapter does not support localfolder).
"""
import pathlib
import sqlite3

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager.provisioning import add_cloud_storage, create_new_participant
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path

MINIO_PORT_DISCOVERY = 19730
MINIO_PORT_LOCAL_STATE = 19732


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


# ---------------------------------------------------------------------------
# joined_locally flag tests (no Hub or cloud needed)
# ---------------------------------------------------------------------------


def test_list_known_teams_joined_locally_for_local_team(playground_dir):
    """A team that exists in NoteToSelf AND has a local clone reports joined_locally=True."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    # create_team writes the team to both NoteToSelf and local FS
    manager = TeamManager(root, alice_hex)
    result = manager.create_team("CoolProject")
    team_id_hex = result["team_id_hex"]

    teams = manager.list_known_teams()
    cool = next((t for t in teams if t["name"] == "CoolProject"), None)
    assert cool is not None
    assert cool["joined_locally"] is True


def test_list_known_teams_joined_locally_false_for_nts_only_row(playground_dir):
    """A team row in shared NoteToSelf without a local clone reports joined_locally=False."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")

    # Insert a fake team into shared NoteToSelf DB directly (no local clone)
    fake_team_id = bytes(16)  # all-zeros, won't conflict with real UUIDs
    nts_db = note_to_self_sync_db_path(root, alice_hex)
    with sqlite3.connect(nts_db) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (fake_team_id, "GhostTeam", bytes(16)),
        )
        conn.commit()

    manager = TeamManager(root, alice_hex)
    teams = manager.list_known_teams()
    ghost = next((t for t in teams if t["name"] == "GhostTeam"), None)
    assert ghost is not None
    assert ghost["joined_locally"] is False


def test_get_team_not_joined_locally_returns_guard_dict(playground_dir):
    """get_team on a NoteToSelf-only team returns a guard dict without crashing."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")

    # Insert fake team row with no local clone
    nts_db = note_to_self_sync_db_path(root, alice_hex)
    with sqlite3.connect(nts_db) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (bytes(16), "GhostTeam", bytes(16)),
        )
        conn.commit()

    manager = TeamManager(root, alice_hex)
    detail = manager.get_team("GhostTeam")
    assert detail["name"] == "GhostTeam"
    assert detail["joined_locally"] is False
    # guard dict must not contain members/invitations from a non-existent local DB
    assert detail["members"] == []
    assert detail["invitations"] == []


def test_get_team_joined_locally_returns_full_detail(playground_dir):
    """get_team on a fully joined team returns joined_locally=True with member data."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    manager = TeamManager(root, alice_hex)
    manager.create_team("RealProject")

    detail = manager.get_team("RealProject")
    assert detail["joined_locally"] is True
    assert len(detail["members"]) == 1


# ---------------------------------------------------------------------------
# Two-device same-identity refresh test (uses Hub + MinIO)
# ---------------------------------------------------------------------------


def _wire_device_b_credentials(root_b, alice_hex, minio):
    """After bootstrap, inject S3 credentials into device B's local NoteToSelf DB.

    During bootstrap device B clones the shared NoteToSelf (getting the
    cloud_storage URL row), but has no matching credential row. We insert
    the credential directly against the existing row ID so the Hub can use
    it for steady-state push/pull.
    """
    nts_db_b = note_to_self_sync_db_path(root_b, alice_hex)
    local_db_b = device_local_db_path(root_b, alice_hex)
    with sqlite3.connect(nts_db_b) as conn:
        row = conn.execute("SELECT id FROM cloud_storage LIMIT 1").fetchone()
    assert row is not None, "No cloud_storage row found in device B's shared NoteToSelf"
    cloud_storage_id = row[0]
    with sqlite3.connect(local_db_b) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO cloud_storage_credential
                (cloud_storage_id, access_key, secret_key)
            VALUES (?, ?, ?)
            """,
            (cloud_storage_id, minio["access_key"], minio["secret_key"]),
        )
        conn.commit()


def test_refresh_note_to_self_two_device_team_discovery(playground_dir, minio_server_gen):
    """Device B discovers a team created on device A after refresh; not before.

    Uses MinIO + Hub TestClient. Hub backend is swapped between installations
    following the pattern in test_identity_bootstrap.py.
    """
    minio = minio_server_gen(port=MINIO_PORT_DISCOVERY)
    workspace = pathlib.Path(playground_dir)
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    # --- Device A: create identity, configure S3, push NoteToSelf ---
    alice_hex = create_new_participant(root_a, "Alice")

    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)

    # Open NoteToSelf session on device A so the Hub knows the berth
    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    backend_a.add_cloud_location(
        nts_token_a, "s3", minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )

    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)

    # Device A creates a team and pushes NoteToSelf to S3
    manager_a.create_team("SharedProject")
    manager_a.push_note_to_self()

    # --- Bootstrap device B ---
    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    # bootstrap_existing_identity uses the Hub bootstrap transport for S3
    from small_sea_manager.manager import bootstrap_existing_identity as _bootstrap
    _bootstrap(root_b, welcome["welcome_bundle"], _http_client=http_a)

    # Sanity: device B has shared NoteToSelf
    shared_b = note_to_self_sync_db_path(root_b, alice_hex)
    assert shared_b.exists()

    # Wire S3 credentials for device B (production: user configures own cloud)
    _wire_device_b_credentials(root_b, alice_hex, minio)

    # --- Device A creates a SECOND team AFTER bootstrap ---
    manager_a.create_team("PostBootstrapProject")
    manager_a.push_note_to_self()

    # --- Switch Hub to device B ---
    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)

    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)

    # Before refresh: device B does NOT see PostBootstrapProject
    teams_before = manager_b.list_known_teams()
    team_names_before = {t["name"] for t in teams_before}
    assert "PostBootstrapProject" not in team_names_before, (
        "Device B must not see PostBootstrapProject before refresh"
    )

    # Device B must not have a local clone
    assert not (root_b / "Participants" / alice_hex / "PostBootstrapProject").exists()

    # --- Refresh ---
    refresh_result = manager_b.refresh_note_to_self()
    assert "teams" in refresh_result

    # After refresh: device B sees PostBootstrapProject
    teams_after = manager_b.list_known_teams()
    team_names_after = {t["name"] for t in teams_after}
    assert "PostBootstrapProject" in team_names_after, (
        "Device B must see PostBootstrapProject after refresh"
    )

    # S5: discovery is not team join
    assert not (root_b / "Participants" / alice_hex / "PostBootstrapProject").exists(), (
        "Device B must not auto-create a local team clone after discovery"
    )

    discovered = next(t for t in teams_after if t["name"] == "PostBootstrapProject")
    assert discovered["joined_locally"] is False

    # get_team guard: returns clear not-joined state
    detail = manager_b.get_team("PostBootstrapProject")
    assert detail["joined_locally"] is False
    assert detail["members"] == []


def test_refresh_does_not_sync_device_local_state(playground_dir, minio_server_gen):
    """Refresh must not move device-local secrets into the shared NoteToSelf DB."""
    minio = minio_server_gen(port=MINIO_PORT_LOCAL_STATE)
    workspace = pathlib.Path(playground_dir)
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    alice_hex = create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)

    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    backend_a.add_cloud_location(
        nts_token_a, "s3", minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.create_team("MyTeam")
    manager_a.push_note_to_self()

    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    from small_sea_manager.manager import bootstrap_existing_identity as _bootstrap
    _bootstrap(root_b, welcome["welcome_bundle"], _http_client=http_a)
    _wire_device_b_credentials(root_b, alice_hex, minio)

    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)

    manager_b.refresh_note_to_self()

    # Shared NoteToSelf DB must not contain device-local tables
    shared_b = note_to_self_sync_db_path(root_b, alice_hex)
    with sqlite3.connect(shared_b) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    local_only_tables = {
        "team_sender_key",
        "peer_sender_key",
        "team_device_key_secret",
        "note_to_self_sync_state",
    }
    leaked = local_only_tables & tables
    assert not leaked, f"Device-local tables leaked into shared NoteToSelf: {leaked}"
