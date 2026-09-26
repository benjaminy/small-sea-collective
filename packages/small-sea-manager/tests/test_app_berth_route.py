"""Micro test: the Manager provisions and publishes an app berth's route (#139).

Core's route operation also serves any activated app berth. Here Files gets
storage through the Manager and the Hub alone, with no test-only publication
helper and no direct SQL.
"""

import base64
import pathlib

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from fastapi.testclient import TestClient
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import TeamManager

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
