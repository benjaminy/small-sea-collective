import base64
import json
import os
import time

import cod_sync.protocol as CS
import small_sea_hub.backend as SmallSea
import small_sea_hub.server as Server
import small_sea_team_manager.provisioning as Provisioning
from fastapi.testclient import TestClient


def _make_cod_sync(repo_dir, remote_name):
    """Create a CodSync wired to a specific repo directory."""
    os.chdir(repo_dir)
    cod = CS.CodSync(remote_name)
    cod.gitCmd = CS.gitCmd
    return cod


def test_notification_roundtrip(playground_dir, ntfy_server, minio_server_gen):
    """Two participants on one Hub: one sends a notification, the other receives it."""
    import pathlib

    minio = minio_server_gen(port=19300)

    # -- Set up participants --
    alice_hex = Provisioning.create_new_participant(playground_dir, "Alice")
    bob_hex = Provisioning.create_new_participant(playground_dir, "Bob")

    # -- Create team for Alice, invite Bob --
    cloud = {
        "protocol": "s3",
        "url": minio["endpoint"],
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
    }
    alice_bucket = "notif-alice-bucket"
    bob_bucket = "notif-bob-bucket"

    team_info = Provisioning.create_team(playground_dir, alice_hex, "ProjectX")

    # Push Alice's team repo to MinIO
    alice_team_sync = (
        pathlib.Path(playground_dir) / "Participants" / alice_hex / "ProjectX" / "Sync"
    )
    alice_remote = CS.S3Remote(
        minio["endpoint"], alice_bucket, minio["access_key"], minio["secret_key"]
    )
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    token = Provisioning.create_invitation(
        playground_dir, alice_hex, "ProjectX", inviter_cloud=cloud, invitee_label="Bob"
    )

    # Patch token with the correct bucket
    token_data = json.loads(base64.b64decode(token).decode())
    token_data["inviter_bucket"] = alice_bucket
    token = base64.b64encode(json.dumps(token_data).encode()).decode()

    # Re-push after invitation commit
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    # Bob accepts invitation
    acceptance_b64 = Provisioning.accept_invitation(
        playground_dir, bob_hex, token, acceptor_cloud=cloud, acceptor_bucket=bob_bucket
    )

    # Alice completes the acceptance
    Provisioning.complete_invitation_acceptance(
        playground_dir, alice_hex, "ProjectX", acceptance_b64
    )

    # -- Single Hub backend --
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)

    # -- Open sessions --
    alice_token = backend.open_session(
        "Alice", "SmallSeaCollectiveCore", "ProjectX", "Smoke Tests"
    )
    bob_token = backend.open_session(
        "Bob", "SmallSeaCollectiveCore", "ProjectX", "Smoke Tests"
    )
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
    resp = client.post(
        "/notifications",
        json={
            "message": "new data available",
            "title": "Sync Update",
        },
        headers={"Authorization": f"Bearer {alice_session}"},
    )
    assert resp.status_code == 200
    send_result = resp.json()
    assert send_result["ok"] is True
    assert send_result["id"] is not None

    # Brief pause for ntfy to process
    time.sleep(0.5)

    # Bob polls for notifications
    resp = client.get(
        "/notifications",
        params={
            "since": "all",
            "timeout": "5",
        },
        headers={"Authorization": f"Bearer {bob_session}"},
    )
    assert resp.status_code == 200
    poll_result = resp.json()
    assert poll_result["ok"] is True
    messages = poll_result["messages"]

    # Bob should see Alice's message (same station = same ntfy topic)
    assert len(messages) >= 1
    texts = [m.get("message") for m in messages]
    assert "new data available" in texts
