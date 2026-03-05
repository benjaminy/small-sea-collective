import time

from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
import small_sea_hub.server as Server
import small_sea_team_manager.provisioning as Provisioning


def test_notification_roundtrip(playground_dir, ntfy_server):
    """Two participants on one Hub: one sends a notification, the other receives it."""

    # -- Set up participants --
    alice_hex = Provisioning.create_new_participant(playground_dir, "Alice")
    bob_hex = Provisioning.create_new_participant(playground_dir, "Bob")

    # -- Create team for Alice, invite Bob --
    alice_cloud = {"protocol": "s3", "url": "http://fake", "access_key": "x", "secret_key": "y"}
    bob_cloud = {"protocol": "s3", "url": "http://fake", "access_key": "x", "secret_key": "y"}

    team_info = Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    token = Provisioning.create_invitation(
        playground_dir, alice_hex, "ProjectX",
        inviter_cloud=alice_cloud, invitee_label="Bob")
    Provisioning.accept_invitation(playground_dir, bob_hex, token, acceptor_cloud=bob_cloud)

    # -- Single Hub backend --
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)

    # -- Open sessions --
    alice_token = backend.open_session("Alice", "SmallSeaCollectiveCore", "ProjectX", "Smoke Tests")
    bob_token = backend.open_session("Bob", "SmallSeaCollectiveCore", "ProjectX", "Smoke Tests")
    alice_session = alice_token.hex()
    bob_session = bob_token.hex()

    # -- Register notification service for both (via team manager, not hub) --
    ntfy_url = ntfy_server["url"]
    Provisioning.add_notification_service(playground_dir, alice_hex, "ntfy", ntfy_url)
    Provisioning.add_notification_service(playground_dir, bob_hex, "ntfy", ntfy_url)

    # -- Use TestClient for HTTP calls --
    Server.app.state.backend = backend
    client = TestClient(Server.app)

    # Alice sends a notification
    resp = client.post("/notifications", json={
        "session": alice_session,
        "message": "new data available",
        "title": "Sync Update",
    })
    assert resp.status_code == 200
    send_result = resp.json()
    assert send_result["ok"] is True
    assert send_result["id"] is not None

    # Brief pause for ntfy to process
    time.sleep(0.5)

    # Bob polls for notifications
    resp = client.get("/notifications", params={
        "session": bob_session,
        "since": "all",
        "timeout": "5",
    })
    assert resp.status_code == 200
    poll_result = resp.json()
    assert poll_result["ok"] is True
    messages = poll_result["messages"]

    # Bob should see Alice's message (same station = same ntfy topic)
    assert len(messages) >= 1
    texts = [m.get("message") for m in messages]
    assert "new data available" in texts
