import json
import os
import time

import boto3
import cod_sync.protocol as CS
import small_sea_hub.backend as SmallSea
import small_sea_hub.server as Server
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config
from cod_sync.testing import PublicS3Remote, S3Remote
from fastapi.testclient import TestClient


def _make_cod_sync(repo_dir, remote_name):
    """Create a CodSync wired to a specific repo directory."""
    os.chdir(repo_dir)
    cod = CS.CodSync(remote_name)
    return cod


def _make_bucket_public(endpoint, access_key, secret_key, bucket_name):
    """Set a bucket policy to allow public reads (MinIO)."""
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
    import pathlib

    alice_minio = minio_server_gen(port=19600)
    bob_minio = minio_server_gen(port=19700)

    # -- Set up participants --
    alice_hex = Provisioning.create_new_participant(playground_dir, "Alice")
    bob_hex = Provisioning.create_new_participant(playground_dir, "Bob")

    # -- Create team for Alice, invite Bob --
    team_info = Provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    team_bucket = f"ss-{team_info['station_id_hex'][:16]}"

    Provisioning.add_cloud_storage(
        playground_dir, alice_hex,
        protocol="s3",
        url=alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    Provisioning.add_cloud_storage(
        playground_dir, bob_hex,
        protocol="s3",
        url=bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    # Push Alice's team repo to her MinIO
    alice_team_sync = (
        pathlib.Path(playground_dir) / "Participants" / alice_hex / "ProjectX" / "Sync"
    )
    alice_remote = S3Remote(
        alice_minio["endpoint"], team_bucket,
        alice_minio["access_key"], alice_minio["secret_key"],
    )
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    # Make Alice's bucket publicly readable
    _make_bucket_public(
        alice_minio["endpoint"],
        alice_minio["access_key"],
        alice_minio["secret_key"],
        team_bucket,
    )

    token = Provisioning.create_invitation(
        playground_dir, alice_hex, "ProjectX",
        inviter_cloud={"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )

    # Re-push after invitation commit
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(["main"])

    # Bob accepts: reads Alice's public bucket anonymously, writes to his own server
    inviter_remote = PublicS3Remote(alice_minio["endpoint"], team_bucket)
    bob_remote = S3Remote(
        bob_minio["endpoint"], team_bucket,
        bob_minio["access_key"], bob_minio["secret_key"],
    )
    acceptance_b64 = Provisioning.accept_invitation(
        playground_dir, bob_hex, token,
        inviter_remote=inviter_remote,
        acceptor_remote=bob_remote,
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
