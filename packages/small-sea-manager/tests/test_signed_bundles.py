import base64
import json
import pathlib
import sqlite3

import small_sea_hub.backend as SmallSea
from cod_sync.protocol import (
    CodSync, PeerSmallSeaRemote, SmallSeaRemote,
    canonical_link_bytes, verify_link_signature,
)
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager
from small_sea_manager.provisioning import (
    complete_invitation_acceptance,
    create_invitation, create_new_participant, create_team,
    get_current_team_device_key)


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


def test_signed_bundle_roundtrip(playground_dir, minio_server_gen):
    """Alice pushes a signed bundle; Bob pulls and verifies Alice's signature."""
    alice_minio = minio_server_gen(port=19800)
    bob_minio = minio_server_gen(port=19900)

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

    # -- Alice: create team --
    team_result = create_team(root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]
    team_bucket = f"ss-{team_result['berth_id_hex'][:16]}"

    # -- Read Alice's signing key --
    alice_priv, alice_pub = get_current_team_device_key(root, alice_hex, "ProjectX")

    # -- Verify Alice's current device key is in the team DB member row --
    alice_team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    conn = sqlite3.connect(str(alice_team_db))
    row = conn.execute(
        "SELECT device_public_key FROM member WHERE id = ?",
        (bytes.fromhex(alice_member_id_hex),),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == alice_pub

    # -- Alice: push signed bundle via Hub --
    alice_team_token = _open_session(http, "Alice", "ProjectX", mode="passthrough")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_via_hub(
        http, alice_team_token, alice_team_sync,
        signing_key=alice_priv, member_id=alice_member_id_hex, device_public_key=alice_pub,
    )

    _make_bucket_public(
        alice_minio["endpoint"], alice_minio["access_key"],
        alice_minio["secret_key"], team_bucket,
    )

    # -- Alice: create invitation and re-push (signed) --
    token = create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )
    _push_via_hub(
        http, alice_team_token, alice_team_sync,
        signing_key=alice_priv, member_id=alice_member_id_hex, device_public_key=alice_pub,
    )

    # -- Bob: accept via Manager --
    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = bob_manager.accept_invitation(token)

    # -- Alice: complete acceptance --
    complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)

    # -- Bob: read Alice's latest link via Hub and verify signature --
    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    bob_member_id_hex = acceptance["acceptor_member_id"]

    bob_team_token = _open_session(http, "Bob", "ProjectX", mode="passthrough")
    peer_remote = PeerSmallSeaRemote(
        bob_team_token, alice_member_id_hex,
        base_url="http://testserver", client=http,
    )
    result = peer_remote.get_latest_link()
    assert result is not None
    link, _etag = result
    [link_ids, branches, bundles, supp_data] = link

    assert "signatures" in supp_data
    assert alice_member_id_hex in supp_data["signatures"]
    alice_signature = supp_data["signatures"][alice_member_id_hex]
    assert alice_signature["device_public_key"] == alice_pub.hex()
    sig_b64 = alice_signature["signature"]

    # Bob looks up Alice's public key from his team DB
    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    bconn = sqlite3.connect(str(bob_team_db))
    alice_pub_from_bob = bconn.execute(
        "SELECT device_public_key FROM member WHERE id = ?",
        (bytes.fromhex(alice_member_id_hex),),
    ).fetchone()[0]
    bconn.close()

    canonical = canonical_link_bytes(link_ids, branches, bundles, supp_data)
    assert verify_link_signature(alice_pub_from_bob, sig_b64, canonical)

    # --- Verify Bob's public key is in Alice's team DB (from acceptance token) ---
    aconn = sqlite3.connect(str(alice_team_db))
    bob_pub_row = aconn.execute(
        "SELECT device_public_key FROM member WHERE id = ?",
        (bytes.fromhex(bob_member_id_hex),),
    ).fetchone()
    aconn.close()
    assert bob_pub_row is not None
    assert bob_pub_row[0] is not None
    assert len(bob_pub_row[0]) == 32  # Ed25519 public key is 32 bytes
