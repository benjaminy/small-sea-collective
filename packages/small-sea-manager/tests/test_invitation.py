import base64
import json
import pathlib
import sqlite3
import subprocess

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from small_sea_note_to_self.db import device_local_db_path
from cod_sync.protocol import CodSync, SmallSeaRemote
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager
from small_sea_manager.provisioning import (
    complete_invitation_acceptance,
    create_invitation,
    create_new_participant,
    create_team,
    get_current_team_device_key,
    list_invitations,
)
from wrasse_trust.identity import verify_membership_cert
from wrasse_trust.keys import key_id_from_public


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


def _push_via_hub(http, session_hex, repo_dir, **push_kwargs):
    """Push a team repo to cloud via Hub using SmallSeaRemote."""
    auth = {"Authorization": f"Bearer {session_hex}"}
    resp = http.post("/cloud/setup", headers=auth)
    assert resp.status_code == 200, resp.text
    remote = SmallSeaRemote(session_hex, base_url="http://testserver", client=http)
    cs = CodSync("origin", repo_dir=pathlib.Path(repo_dir))
    cs.remote = remote
    cs.push_to_remote(["main"], **push_kwargs)


def _make_bucket_public(endpoint, access_key, secret_key, bucket_name):
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


def test_create_invitation(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")

    alice_cloud = {
        "protocol": "s3",
        "url": "http://localhost:9000",
        "access_key": "alice-key",
        "secret_key": "alice-secret",
    }
    token = create_invitation(
        root, alice_hex, "ProjectX", alice_cloud, invitee_label="Bob"
    )
    assert isinstance(token, str)
    assert len(token) > 0

    # Verify invitation row exists
    invitations = list_invitations(root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "pending"
    assert invitations[0]["invitee_label"] == "Bob"


def test_create_invitation_includes_bucket(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")

    alice_cloud = {
        "protocol": "s3",
        "url": "http://localhost:9000",
        "access_key": "alice-key",
        "secret_key": "alice-secret",
    }
    token_b64 = create_invitation(root, alice_hex, "ProjectX", alice_cloud)
    token_json = base64.b64decode(token_b64).decode()
    token = json.loads(token_json)

    assert "inviter_bucket" in token
    assert token["inviter_bucket"].startswith("ss-")
    assert len(token["inviter_bucket"]) == 3 + 16  # "ss-" + 16 hex chars
    assert len(token["team_id"]) == 32
    assert token["inviter_sender_key"]["group_id"] == token["team_id"]
    _alice_team_private_key, alice_team_public_key = get_current_team_device_key(
        root, alice_hex, "ProjectX"
    )
    assert token["inviter_sender_key"]["sender_device_key_id"] == key_id_from_public(
        alice_team_public_key
    ).hex()


def test_full_invitation_flow(playground_dir, minio_server_gen):
    """Full invitation flow routed through the Hub."""
    alice_minio = minio_server_gen(port=19100)
    bob_minio = minio_server_gen(port=19200)

    root = pathlib.Path(playground_dir)

    # -- Shared Hub --
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    # -- Provision participants --
    alice_hex = create_new_participant(root, "Alice")
    bob_hex = create_new_participant(root, "Bob")

    # -- Register cloud storage via Hub --
    alice_nts = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(
        alice_nts, "s3", alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(
        bob_nts, "s3", bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    # -- Alice: create team and push via Hub --
    team_result = create_team(root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]
    team_bucket = f"ss-{team_result['berth_id_hex'][:16]}"

    alice_team_token = _open_session(http, "Alice", "ProjectX", mode="passthrough")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # Make Alice's bucket publicly readable (anonymous clone via /cloud_proxy)
    _make_bucket_public(
        alice_minio["endpoint"], alice_minio["access_key"],
        alice_minio["secret_key"], team_bucket,
    )

    # -- Alice: create invitation and re-push --
    token = create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )
    token_data = json.loads(base64.b64decode(token).decode())
    assert "inviter_bucket" in token_data
    assert "access_key" not in token_data.get("inviter_cloud", {})
    assert "secret_key" not in token_data.get("inviter_cloud", {})

    _push_via_hub(http, alice_team_token, alice_team_sync)

    # -- Bob: accept via Manager --
    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = bob_manager.accept_invitation(token)

    assert isinstance(acceptance_b64, str)

    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    bob_member_id_hex = acceptance["acceptor_member_id"]
    assert bob_member_id_hex != bob_hex
    assert len(bob_member_id_hex) == 32
    assert acceptance["team_id"] == token_data["team_id"]
    assert len(acceptance["acceptor_device_public_key"]) == 64
    assert acceptance["acceptor_sender_key"]["group_id"] == token_data["team_id"]
    assert acceptance["acceptor_sender_key"]["sender_device_key_id"] == key_id_from_public(
        bytes.fromhex(acceptance["acceptor_device_public_key"])
    ).hex()

    # -- Alice: complete the acceptance --
    complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)

    # --- Verify Alice's invitation is accepted ---
    invitations = list_invitations(root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "accepted"

    # --- Verify Alice's team DB has 2 members, a peer (Bob), and 2 berth_roles ---
    alice_team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    aconn = sqlite3.connect(str(alice_team_db))
    members = aconn.execute(
        "SELECT id, device_public_key FROM member"
    ).fetchall()
    assert len(members) == 2
    member_ids = {row[0].hex() for row in members}
    assert alice_member_id_hex in member_ids
    assert bob_member_id_hex in member_ids
    bob_member_row = next(row for row in members if row[0] == bytes.fromhex(bob_member_id_hex))
    assert bob_member_row[1] == bytes.fromhex(acceptance["acceptor_device_public_key"])

    bob_cert_row = aconn.execute(
        "SELECT cert_id, cert_type, subject_key_id, subject_public_key, issuer_key_id, "
        "issuer_member_id, issued_at, claims, signature "
        "FROM key_certificate WHERE subject_public_key = ?",
        (bytes.fromhex(acceptance["acceptor_device_public_key"]),),
    ).fetchone()
    assert bob_cert_row is not None
    assert bob_cert_row[1] == "membership"
    assert bob_cert_row[3] == bytes.fromhex(acceptance["acceptor_device_public_key"])
    assert bob_cert_row[5] == bytes.fromhex(alice_member_id_hex)
    assert json.loads(bob_cert_row[7])["member_id"] == bob_member_id_hex
    bob_membership_cert = provisioning._deserialize_cert(
        {
            "cert_id": bob_cert_row[0].hex(),
            "cert_type": bob_cert_row[1],
            "team_id": token_data["team_id"],
            "subject_key_id": bob_cert_row[2].hex(),
            "subject_public_key": bob_cert_row[3].hex(),
            "issuer_key_id": bob_cert_row[4].hex(),
            "issuer_participant_id": bob_cert_row[5].hex(),
            "issued_at_iso": bob_cert_row[6],
            "claims": json.loads(bob_cert_row[7]),
            "signature": bob_cert_row[8].hex(),
        }
    )
    alice_device_public_key = next(
        row[1] for row in members if row[0] == bytes.fromhex(alice_member_id_hex)
    )
    assert verify_membership_cert(
        bob_membership_cert,
        issuer_public_key=alice_device_public_key,
        team_id=bytes.fromhex(token_data["team_id"]),
        issuer_member_id=bytes.fromhex(alice_member_id_hex),
        admitted_member_id=bytes.fromhex(bob_member_id_hex),
        subject_public_key=bytes.fromhex(acceptance["acceptor_device_public_key"]),
    )

    peers = aconn.execute(
        "SELECT member_id, display_name, protocol, url FROM peer"
    ).fetchall()
    assert len(peers) == 1
    assert peers[0][0] == bytes.fromhex(bob_member_id_hex)
    assert peers[0][1] == "Bob"
    assert peers[0][2] == "s3"
    assert peers[0][3] == bob_minio["endpoint"]

    roles = aconn.execute("SELECT member_id, role FROM berth_role").fetchall()
    assert len(roles) == 2
    role_map = {row[0].hex(): row[1] for row in roles}
    assert role_map[alice_member_id_hex] == "read-write"
    assert role_map[bob_member_id_hex] == "read-write"
    aconn.close()

    # --- Verify Bob's team DB is still provisional: Alice exists, Bob does not yet ---
    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    bconn = sqlite3.connect(str(bob_team_db))
    members = bconn.execute(
        "SELECT id, device_public_key FROM member"
    ).fetchall()
    assert len(members) == 1
    member_ids = {row[0].hex() for row in members}
    assert alice_member_id_hex in member_ids
    assert bob_member_id_hex not in member_ids

    peers = bconn.execute(
        "SELECT member_id, display_name, protocol, url FROM peer"
    ).fetchall()
    assert len(peers) == 1
    assert peers[0][0] == bytes.fromhex(alice_member_id_hex)
    assert peers[0][1] == "Alice"
    assert peers[0][2] == "s3"
    assert peers[0][3] == alice_minio["endpoint"]
    bconn.close()

    # --- Verify Bob's NoteToSelf has the team pointer but NOT a TeamAppBerth for ProjectX ---
    bob_user_db = root / "Participants" / bob_hex / "NoteToSelf" / "Sync" / "core.db"
    buconn = sqlite3.connect(str(bob_user_db))
    buconn.row_factory = sqlite3.Row
    bob_local_db = device_local_db_path(root, bob_hex)
    bulconn = sqlite3.connect(str(bob_local_db))
    teams = buconn.execute("SELECT * FROM team WHERE name = 'ProjectX'").fetchall()
    assert len(teams) == 1
    assert teams[0]["id"] == bytes.fromhex(token_data["team_id"])
    assert teams[0]["self_in_team"] == bytes.fromhex(bob_member_id_hex)
    alice_sender_device_key_id = key_id_from_public(alice_device_public_key)
    bob_sender_device_key_id = key_id_from_public(
        bytes.fromhex(acceptance["acceptor_device_public_key"])
    )

    with pytest.raises(sqlite3.OperationalError):
        buconn.execute(
            "SELECT sender_device_key_id, signing_private_key "
            "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
            (bytes.fromhex(token_data["team_id"]), alice_sender_device_key_id),
        ).fetchone()

    alice_peer_sender_key = bulconn.execute(
        "SELECT sender_device_key_id, signing_private_key "
        "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
        (bytes.fromhex(token_data["team_id"]), alice_sender_device_key_id),
    ).fetchone()
    assert alice_peer_sender_key is not None
    assert alice_peer_sender_key[0] == alice_sender_device_key_id
    assert alice_peer_sender_key[1] is None

    bob_team_sender_key = bulconn.execute(
        "SELECT sender_device_key_id, signing_private_key "
        "FROM team_sender_key WHERE team_id = ?",
        (bytes.fromhex(token_data["team_id"]),),
    ).fetchone()
    assert bob_team_sender_key is not None
    assert bob_team_sender_key[0] == bob_sender_device_key_id
    assert bob_team_sender_key[1] is not None

    other_berths = buconn.execute(
        "SELECT tab.* FROM team_app_berth tab "
        "JOIN team t ON tab.team_id = t.id "
        "WHERE t.name = 'ProjectX'"
    ).fetchall()
    assert len(other_berths) == 0
    buconn.close()
    bulconn.close()

    alice_user_db = root / "Participants" / alice_hex / "NoteToSelf" / "Sync" / "core.db"
    auconn = sqlite3.connect(str(alice_user_db))
    alice_local_db = device_local_db_path(root, alice_hex)
    aulconn = sqlite3.connect(str(alice_local_db))
    with pytest.raises(sqlite3.OperationalError):
        auconn.execute(
            "SELECT sender_device_key_id, signing_private_key "
            "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
            (bytes.fromhex(token_data["team_id"]), bob_sender_device_key_id),
        ).fetchone()
    bob_peer_sender_key = aulconn.execute(
        "SELECT sender_device_key_id, signing_private_key "
        "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
        (bytes.fromhex(token_data["team_id"]), bob_sender_device_key_id),
    ).fetchone()
    assert bob_peer_sender_key is not None
    assert bob_peer_sender_key[0] == bob_sender_device_key_id
    assert bob_peer_sender_key[1] is None
    auconn.close()
    aulconn.close()

    # --- Verify Bob's team dir has a git repo with correct commit ---
    bob_sync = root / "Participants" / bob_hex / "ProjectX" / "Sync"
    result = subprocess.run(
        ["git", "-C", str(bob_sync), "log", "--oneline"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Joined team: ProjectX" in result.stdout


def test_double_accept_rejected(playground_dir, minio_server_gen):
    """Second acceptance of the same invitation should fail."""
    alice_minio = minio_server_gen(port=19300)
    bob_minio = minio_server_gen(port=19400)
    carol_minio = minio_server_gen(port=19500)

    root = pathlib.Path(playground_dir)

    # -- Shared Hub --
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    # -- Provision participants --
    alice_hex = create_new_participant(root, "Alice")
    bob_hex = create_new_participant(root, "Bob")
    carol_hex = create_new_participant(root, "Carol")

    # -- Register cloud storage via Hub --
    alice_nts = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(
        alice_nts, "s3", alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(
        bob_nts, "s3", bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )
    carol_nts = _open_session(http, "Carol", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(
        carol_nts, "s3", carol_minio["endpoint"],
        access_key=carol_minio["access_key"],
        secret_key=carol_minio["secret_key"],
    )

    # -- Alice: create team, push, create invitation --
    team_result = create_team(root, alice_hex, "ProjectX")
    team_bucket = f"ss-{team_result['berth_id_hex'][:16]}"

    alice_team_token = _open_session(http, "Alice", "ProjectX", mode="passthrough")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_via_hub(http, alice_team_token, alice_team_sync)

    _make_bucket_public(
        alice_minio["endpoint"], alice_minio["access_key"],
        alice_minio["secret_key"], team_bucket,
    )

    token = create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
    )
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # -- Bob: accept --
    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = bob_manager.accept_invitation(token)

    # -- Alice: complete Bob's acceptance and re-push so Carol can clone the latest --
    complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # -- Carol: accept the same token (provisioning succeeds, completion fails) --
    carol_manager = TeamManager(root, carol_hex, _http_client=http)
    carol_acceptance_b64 = carol_manager.accept_invitation(token)

    with pytest.raises(ValueError, match="not pending"):
        complete_invitation_acceptance(root, alice_hex, "ProjectX", carol_acceptance_b64)
