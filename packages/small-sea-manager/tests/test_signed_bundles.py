import base64
import json
import os
import pathlib
import sqlite3

import boto3
import cod_sync.protocol as CS
from botocore.config import Config
from cod_sync.protocol import canonical_link_bytes, verify_link_signature
from cod_sync.testing import PublicS3Remote, S3Remote
from small_sea_manager.provisioning import (
    accept_invitation, add_cloud_storage, complete_invitation_acceptance,
    create_invitation, create_new_participant, create_team,
    get_team_signing_key)


def _make_cod_sync(repo_dir, remote_name):
    os.chdir(repo_dir)
    return CS.CodSync(remote_name)


def _make_bucket_public(endpoint, access_key, secret_key, bucket_name):
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

    alice_root = pathlib.Path(playground_dir) / "alice-root"
    bob_root = pathlib.Path(playground_dir) / "bob-root"
    alice_root.mkdir()
    bob_root.mkdir()

    # --- Alice creates participant + team ---
    alice_hex = create_new_participant(alice_root, "Alice")
    team_result = create_team(alice_root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]
    team_bucket = f"ss-{team_result['station_id_hex'][:16]}"

    add_cloud_storage(
        alice_root, alice_hex,
        protocol="s3", url=alice_minio["endpoint"],
        access_key=alice_minio["access_key"], secret_key=alice_minio["secret_key"],
    )

    # --- Read Alice's signing key ---
    alice_priv, alice_pub = get_team_signing_key(alice_root, alice_hex, "ProjectX")

    # --- Verify Alice's public key is in the team DB member row ---
    alice_team_db = alice_root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    conn = sqlite3.connect(str(alice_team_db))
    row = conn.execute(
        "SELECT public_key FROM member WHERE id = ?",
        (bytes.fromhex(alice_member_id_hex),),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == alice_pub

    # --- Alice pushes a SIGNED bundle ---
    alice_team_sync = alice_root / "Participants" / alice_hex / "ProjectX" / "Sync"
    alice_remote = S3Remote(
        alice_minio["endpoint"], team_bucket,
        alice_minio["access_key"], alice_minio["secret_key"],
    )
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(
        ["main"], signing_key=alice_priv, member_id=alice_member_id_hex,
    )

    _make_bucket_public(
        alice_minio["endpoint"], alice_minio["access_key"],
        alice_minio["secret_key"], team_bucket,
    )

    # --- Create invitation and complete the flow ---
    token = create_invitation(
        alice_root, alice_hex, "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )

    # Re-push after invitation commit (signed)
    cod_alice = _make_cod_sync(alice_team_sync, "cloud")
    cod_alice.remote = alice_remote
    cod_alice.push_to_remote(
        ["main"], signing_key=alice_priv, member_id=alice_member_id_hex,
    )

    bob_hex = create_new_participant(bob_root, "Bob")
    add_cloud_storage(
        bob_root, bob_hex,
        protocol="s3", url=bob_minio["endpoint"],
        access_key=bob_minio["access_key"], secret_key=bob_minio["secret_key"],
    )

    inviter_remote = PublicS3Remote(alice_minio["endpoint"], team_bucket)
    bob_remote = S3Remote(
        bob_minio["endpoint"], team_bucket,
        bob_minio["access_key"], bob_minio["secret_key"],
    )
    acceptance_b64 = accept_invitation(
        bob_root, bob_hex, token,
        inviter_remote=inviter_remote, acceptor_remote=bob_remote,
    )

    complete_invitation_acceptance(alice_root, alice_hex, "ProjectX", acceptance_b64)

    # --- Bob reads the latest link from Alice's bucket and verifies ---
    result = inviter_remote.get_latest_link()
    assert result is not None
    link, _etag = result
    [link_ids, branches, bundles, supp_data] = link

    # Link should have a signatures entry
    assert "signatures" in supp_data
    assert alice_member_id_hex in supp_data["signatures"]
    sig_b64 = supp_data["signatures"][alice_member_id_hex]

    # Bob looks up Alice's public key from his team DB
    bob_team_db = bob_root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    bconn = sqlite3.connect(str(bob_team_db))
    alice_pub_from_bob = bconn.execute(
        "SELECT public_key FROM member WHERE id = ?",
        (bytes.fromhex(alice_member_id_hex),),
    ).fetchone()[0]
    bconn.close()

    # Verify the signature
    canonical = canonical_link_bytes(link_ids, branches, bundles, supp_data)
    assert verify_link_signature(alice_pub_from_bob, sig_b64, canonical)

    # --- Verify Bob's public key is in Alice's team DB (from acceptance token) ---
    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    bob_member_id_hex = acceptance["acceptor_member_id"]

    aconn = sqlite3.connect(str(alice_team_db))
    bob_pub_row = aconn.execute(
        "SELECT public_key FROM member WHERE id = ?",
        (bytes.fromhex(bob_member_id_hex),),
    ).fetchone()
    aconn.close()
    assert bob_pub_row is not None
    assert bob_pub_row[0] is not None
    assert len(bob_pub_row[0]) == 32  # Ed25519 public key is 32 bytes
