"""Hub integration test: full invitation flow routed through the Hub.

All cloud I/O goes through the Hub (TestClient). MinIO provides the S3 backend.
Provisioning is called directly (local DB/git ops only); the Manager orchestrates
session management and cloud pushes.
"""

import base64
import json
import pathlib
import sqlite3

import boto3
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from cod_sync.protocol import CodSync, ExplicitProxyRemote, SmallSeaRemote
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager

ALICE_MINIO_PORT = 19650
BOB_MINIO_PORT = 19750


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
        return result["token"]  # auto-approved
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_bucket_public(endpoint, access_key, secret_key, bucket_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=BotoConfig(signature_version="s3v4"),
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


def _push_via_hub(http, session_hex, repo_dir, base_url="http://testserver"):
    """Push a team repo to cloud via Hub using SmallSeaRemote."""
    auth = {"Authorization": f"Bearer {session_hex}"}
    resp = http.post("/cloud/setup", headers=auth)
    assert resp.status_code == 200, resp.text
    remote = SmallSeaRemote(session_hex, base_url=base_url, client=http)
    cs = CodSync("origin", repo_dir=pathlib.Path(repo_dir))
    cs.remote = remote
    cs.push_to_remote(["main"])


def test_invitation_flow_via_hub(playground_dir, minio_server_gen):
    """Full invitation flow: Alice invites Bob; all cloud I/O goes through the Hub."""
    alice_minio = minio_server_gen(port=ALICE_MINIO_PORT)
    bob_minio = minio_server_gen(port=BOB_MINIO_PORT)

    root = pathlib.Path(playground_dir)

    # ---- Shared Hub (in-process) ----
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    # ---- Provision participants ----
    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")

    # ---- Register cloud storage for Alice (NoteToSelf Hub session) ----
    alice_nts_token = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(
        alice_nts_token, "s3", alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )

    # ---- Register cloud storage for Bob (NoteToSelf Hub session) ----
    bob_nts_token = _open_session(http, "Bob", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(
        bob_nts_token, "s3", bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    # ---- Alice: create team (local) ----
    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]
    team_bucket = f"ss-{team_result['berth_id_hex'][:16]}"

    # ---- Alice: push team repo via Hub ----
    alice_team_token = _open_session(http, "Alice", "ProjectX", mode="passthrough")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # Make Alice's bucket publicly readable (anonymous clone via /cloud_proxy)
    _make_bucket_public(
        alice_minio["endpoint"],
        alice_minio["access_key"],
        alice_minio["secret_key"],
        team_bucket,
    )

    # ---- Alice: create invitation (local, no credentials in token) ----
    token_b64 = Provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )
    token_data = json.loads(base64.b64decode(token_b64).decode())
    assert "access_key" not in token_data.get("inviter_cloud", {})

    # ---- Alice: re-push after invitation commit ----
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # ---- Bob: accept via Manager (all cloud I/O through Hub) ----
    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = bob_manager.accept_invitation(token_b64)

    assert isinstance(acceptance_b64, str)
    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    bob_member_id_hex = acceptance["acceptor_member_id"]

    # ---- Bob: push accepted team repo via Hub ----
    bob_team_token = _open_session(http, "Bob", "ProjectX")
    bob_team_sync = root / "Participants" / bob_hex / "ProjectX" / "Sync"
    _push_via_hub(http, bob_team_token, bob_team_sync)

    # ---- Alice: complete the acceptance (local) ----
    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)

    # ---- Verify Alice's team DB ----
    alice_team_db = alice_team_sync / "core.db"
    aconn = sqlite3.connect(str(alice_team_db))
    members = aconn.execute("SELECT id FROM member").fetchall()
    assert len(members) == 2
    member_ids = {row[0].hex() for row in members}
    assert alice_member_id_hex in member_ids
    assert bob_member_id_hex in member_ids

    peers = aconn.execute(
        "SELECT member_id, display_name, protocol, url FROM peer"
    ).fetchall()
    assert len(peers) == 1
    assert peers[0][0].hex() == bob_member_id_hex
    assert peers[0][1] == "Bob"
    assert peers[0][2] == "s3"
    aconn.close()

    # ---- Verify Bob's team DB ----
    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    bconn = sqlite3.connect(str(bob_team_db))
    members = bconn.execute("SELECT id FROM member").fetchall()
    assert len(members) == 2
    member_ids = {row[0].hex() for row in members}
    assert alice_member_id_hex in member_ids
    assert bob_member_id_hex in member_ids
    peers = bconn.execute(
        "SELECT member_id, display_name, protocol, url FROM peer"
    ).fetchall()
    assert len(peers) == 1
    assert peers[0][0].hex() == alice_member_id_hex
    assert peers[0][1] == "Alice"
    assert peers[0][2] == "s3"
    bconn.close()

    # ---- Verify Bob's repo was pushed to his MinIO ----
    bob_s3 = boto3.client(
        "s3",
        endpoint_url=bob_minio["endpoint"],
        aws_access_key_id=bob_minio["access_key"],
        aws_secret_access_key=bob_minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    bob_team_bucket = f"ss-{team_result['berth_id_hex'][:16]}"
    objects = bob_s3.list_objects_v2(Bucket=bob_team_bucket)
    keys = {obj["Key"] for obj in objects.get("Contents", [])}
    assert "latest-link.yaml" in keys
