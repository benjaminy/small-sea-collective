"""Micro tests for the Hub's record of confirmed app grants."""

from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.server import app


def _setup(root):
    backend = SmallSea.SmallSeaBackend(root_dir=root)
    participant_hex = Provisioning.create_new_participant(root, "alice")
    Provisioning.create_team(root, participant_hex, "ProjectX")
    app.state.backend = backend
    return backend, participant_hex, TestClient(app)


def _request(client, team="NoteToSelf", app_name="SmallSeaCollectiveCore"):
    response = client.post("/sessions/request", json={
        "participant": "alice", "app": app_name, "team": team,
        "client": "Micro test", "mode": "passthrough",
    })
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(client, pending):
    pin = next(
        row["pin"] for row in app.state.backend.list_pending_sessions()
        if row["pending_id"] == pending["pending_id"]
    )
    return client.post("/sessions/confirm", json={
        "pending_id": pending["pending_id"], "pin": pin,
    })


def test_confirmed_session_records_granted_app(playground_dir):
    backend, participant_hex, client = _setup(playground_dir)
    response = _confirm(client, _request(client))
    assert response.status_code == 200
    rows = backend.list_granted_apps(bytes.fromhex(participant_hex))
    assert [(row["team_name"], row["app_name"]) for row in rows] == [
        ("NoteToSelf", "SmallSeaCollectiveCore")
    ]


def test_granted_app_survives_session_deletion(playground_dir):
    backend, _participant_hex, client = _setup(playground_dir)
    _confirm(client, _request(client))
    nts = _confirm(client, _request(client)).json()
    sessions = client.get("/sessions/confirmed", headers={
        "Authorization": f"Bearer {nts}"
    }).json()
    target = sessions[0]
    assert client.delete(f"/sessions/confirmed/{target['id']}", headers={
        "Authorization": f"Bearer {nts}"
    }).status_code == 200
    participant_id = backend._lookup_session(nts).participant_id
    assert [(row["team_name"], row["app_name"])
            for row in backend.list_granted_apps(participant_id)] == [
        ("NoteToSelf", "SmallSeaCollectiveCore")
    ]


def test_unconfirmed_session_records_nothing(playground_dir):
    backend, participant_hex, client = _setup(playground_dir)
    pending = _request(client, team="ProjectX")
    participant_id = bytes.fromhex(participant_hex)
    assert backend.list_granted_apps(participant_id) == []
    assert pending["pending_id"]


def test_granted_apps_endpoint_requires_note_to_self(playground_dir):
    _backend, _participant_hex, client = _setup(playground_dir)
    project_session = _confirm(client, _request(client, team="ProjectX")).json()
    response = client.get("/apps/granted", headers={
        "Authorization": f"Bearer {project_session}"
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "session_admin_not_allowed"
