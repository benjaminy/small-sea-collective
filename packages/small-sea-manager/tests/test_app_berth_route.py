"""Micro test: the Manager provisions and publishes an app berth's route (#139).

Core's route operation also serves any activated app berth. Here Files gets
storage through the Manager and the Hub alone, with no test-only publication
helper and no direct SQL.
"""

import base64
import pathlib
import sqlite3

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from fastapi.testclient import TestClient
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import TeamManager
from small_sea_note_to_self.ids import uuid7

TEAM = "ProjectX"
FILES = "SmallSeaCollectiveFiles"


def test_files_berth_route_lets_a_files_session_write_and_read(
    playground_dir, minio_server_gen
):
    minio = minio_server_gen()
    root = pathlib.Path(playground_dir)
    hub = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    hub_app.state.backend = hub
    http = TestClient(hub_app)

    alice_hex = provisioning.create_new_participant(root, "Alice")
    manager = TeamManager(root, alice_hex, _http_client=http)
    manager.add_cloud_storage(
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    provisioning.create_team(root, alice_hex, TEAM)
    provisioning.register_app_for_participant(root, alice_hex, FILES)
    provisioning.activate_app_for_team(root, alice_hex, TEAM, FILES)

    report = manager.reconcile_team_route(TEAM, app_name=FILES)
    assert report == {"route": "ready", "route_reason": None}

    # Core's route state is untouched by the Files operation.
    files_state = provisioning.derive_team_join_state(root, alice_hex, TEAM, FILES)
    core_state = provisioning.derive_team_join_state(root, alice_hex, TEAM)
    assert files_state["berth_id"] != core_state["berth_id"]
    assert files_state["allocation"]["location"] != (
        core_state["allocation"] or {}
    ).get("location")

    resp = http.post(
        "/sessions/request",
        json={
            "participant": "Alice",
            "app": FILES,
            "team": TEAM,
            "client": "Files micro test",
            "mode": "passthrough",
        },
    )
    auth = {"Authorization": f"Bearer {resp.json()['token']}"}
    content = b"hello from files"
    resp = http.post(
        "/cloud_file",
        json={"path": "greeting.txt", "data": base64.b64encode(content).decode()},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    resp = http.get("/cloud_file", params={"path": "greeting.txt"}, headers=auth)
    assert resp.status_code == 200, resp.text
    assert base64.b64decode(resp.json()["data"]) == content


def _pin_session(http, app_name, team, mode="encrypted"):
    """Request a session and confirm it with the PIN, as a person would."""
    resp = http.post(
        "/sessions/request",
        json={
            "participant": "Alice",
            "app": app_name,
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "token" not in body
    resp = http.post(
        "/sessions/confirm", json={"pending_id": body["pending_id"], "pin": body["pin"]}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _alice_with_files(root, http, minio):
    alice_hex = provisioning.create_new_participant(root, "Alice")
    manager = TeamManager(root, alice_hex, _http_client=http)
    manager.add_cloud_storage(
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    provisioning.create_team(root, alice_hex, TEAM)
    provisioning.register_app_for_participant(root, alice_hex, FILES)
    provisioning.activate_app_for_team(root, alice_hex, TEAM, FILES)
    return alice_hex, manager


def test_pin_confirmed_core_session_sets_up_the_files_berth(
    playground_dir, minio_server_gen
):
    root = pathlib.Path(playground_dir)
    hub = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=False)
    hub_app.state.backend = hub
    http = TestClient(hub_app)
    _alice_hex, manager = _alice_with_files(root, http, minio_server_gen())

    manager.set_session(TEAM, _pin_session(http, "SmallSeaCollectiveCore", TEAM))
    report = manager.reconcile_team_route(TEAM, app_name=FILES)
    assert report == {"route": "ready", "route_reason": None}

    auth = {"Authorization": f"Bearer {_pin_session(http, FILES, TEAM, 'passthrough')}"}
    resp = http.post(
        "/cloud_file",
        json={"path": "a.txt", "data": base64.b64encode(b"pin").decode()},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    resp = http.get("/cloud_file", params={"path": "a.txt"}, headers=auth)
    assert base64.b64decode(resp.json()["data"]) == b"pin"


def test_manager_berth_setup_rejects_a_berth_from_another_team(
    playground_dir, minio_server_gen
):
    root = pathlib.Path(playground_dir)
    hub = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    hub_app.state.backend = hub
    http = TestClient(hub_app)
    alice_hex, _manager = _alice_with_files(root, http, minio_server_gen())
    provisioning.create_team(root, alice_hex, "Other")
    provisioning.activate_app_for_team(root, alice_hex, "Other", FILES)
    other_berth = provisioning.derive_team_join_state(
        root, alice_hex, "Other", FILES
    )["berth_id"]

    core = http.post(
        "/sessions/request",
        json={"participant": "Alice", "app": "SmallSeaCollectiveCore",
              "team": TEAM, "client": "t", "mode": "encrypted"},
    ).json()["token"]
    resp = http.post(
        f"/manager/berths/{other_berth.hex()}/cloud/setup",
        headers={"Authorization": f"Bearer {core}"},
    )
    assert resp.status_code == 404, resp.text

    # A Files session is not a Manager session, even for its own team.
    files = http.post(
        "/sessions/request",
        json={"participant": "Alice", "app": FILES,
              "team": TEAM, "client": "TeamManager", "mode": "encrypted"},
    ).json()["token"]
    files_berth = provisioning.derive_team_join_state(root, alice_hex, TEAM, FILES)[
        "berth_id"
    ]
    resp = http.post(
        f"/manager/berths/{files_berth.hex()}/cloud/setup",
        headers={"Authorization": f"Bearer {files}"},
    )
    assert resp.status_code == 403, resp.text


def test_duplicate_app_name_is_refused_before_allocation(
    playground_dir, minio_server_gen
):
    root = pathlib.Path(playground_dir)
    hub = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    hub_app.state.backend = hub
    http = TestClient(hub_app)
    alice_hex, manager = _alice_with_files(root, http, minio_server_gen())
    files_berth = provisioning.derive_team_join_state(root, alice_hex, TEAM, FILES)[
        "berth_id"
    ]
    # A second same-named app row with no berth: the corrupt state under test.
    team_db = root / "Participants" / alice_hex / TEAM / "Sync" / "core.db"
    with sqlite3.connect(str(team_db)) as conn:
        conn.execute("INSERT INTO app (id, name) VALUES (?, ?)", (uuid7(), FILES))

    with pytest.raises(ValueError, match="ambiguous"):
        manager.reconcile_team_route(TEAM, app_name=FILES)
    assert (
        provisioning.get_berth_cloud_allocation_for_berth(root, alice_hex, files_berth)
        is None
    )
