import pathlib
import time

import small_sea_hub.backend as SmallSea
import small_sea_hub.server as Server
import small_sea_manager.provisioning as Provisioning
from cod_sync.protocol import CodSync, SmallSeaRemote
from fastapi.testclient import TestClient
from small_sea_manager.manager import TeamManager


def _open_session(http, nickname, team):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]  # auto-approved
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _push_via_hub(http, session_hex, repo_dir):
    """Push a team repo to cloud via Hub using SmallSeaRemote."""
    auth = {"Authorization": f"Bearer {session_hex}"}
    resp = http.post("/cloud/setup", headers=auth)
    assert resp.status_code == 200, resp.text
    remote = SmallSeaRemote(session_hex, base_url="http://testserver", client=http)
    cs = CodSync("origin", repo_dir=pathlib.Path(repo_dir))
    cs.remote = remote
    cs.push_to_remote(["main"])


def _make_bucket_public(endpoint, access_key, secret_key, bucket_name):
    import json
    import boto3
    from botocore.config import Config
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.put_bucket_policy(
        Bucket=bucket_name,
        Policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            }],
        }),
    )


def test_notification_roundtrip(playground_dir, ntfy_server, minio_server_gen):
    """Two participants on one Hub: one sends a notification, the other receives it."""
    alice_minio = minio_server_gen(port=19600)
    bob_minio = minio_server_gen(port=19700)

    root = pathlib.Path(playground_dir)

    # -- Shared Hub --
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    Server.app.state.backend = backend
    http = TestClient(Server.app)

    # -- Provision participants --
    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")

    # -- Register cloud storage via Hub --
    alice_nts = _open_session(http, "Alice", "NoteToSelf")
    backend.add_cloud_location(
        alice_nts, "s3", alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf")
    backend.add_cloud_location(
        bob_nts, "s3", bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    # -- Alice: create team, push, invite Bob --
    team_info = Provisioning.create_team(root, alice_hex, "ProjectX")
    team_bucket = f"ss-{team_info['berth_id_hex'][:16]}"

    alice_team_token = _open_session(http, "Alice", "ProjectX")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_via_hub(http, alice_team_token, alice_team_sync)

    _make_bucket_public(
        alice_minio["endpoint"], alice_minio["access_key"],
        alice_minio["secret_key"], team_bucket,
    )

    token = Provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        inviter_cloud={"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # -- Bob: accept via Manager --
    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = bob_manager.accept_invitation(token)

    # -- Alice: complete acceptance --
    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)

    # -- Register notification service for both participants --
    ntfy_url = ntfy_server["url"]
    Provisioning.add_notification_service(root, alice_hex, "ntfy", ntfy_url)
    Provisioning.add_notification_service(root, bob_hex, "ntfy", ntfy_url)

    # -- Open team sessions for notification calls --
    alice_session = _open_session(http, "Alice", "ProjectX")
    bob_session = _open_session(http, "Bob", "ProjectX")

    # Alice sends a notification
    resp = http.post(
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
    resp = http.get(
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

    # Bob should see Alice's message (same berth = same ntfy topic)
    assert len(messages) >= 1
    texts = [m.get("message") for m in messages]
    assert "new data available" in texts
