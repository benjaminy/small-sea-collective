import json
import pathlib
import sqlite3

import boto3
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.client import Config as BotoConfig
from fastapi.testclient import TestClient

from small_sea_hub.server import app


def _request_and_confirm(client, *, team="ProjectX"):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": payload["pending_id"], "pin": payload["pin"]},
    )
    assert resp.status_code == 200
    return resp.json()


def _public_s3(minio):
    return boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _make_bucket_public(minio, bucket_name: str):
    s3 = _public_s3(minio)
    s3.create_bucket(Bucket=bucket_name)
    s3.put_bucket_policy(
        Bucket=bucket_name,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "PublicReadGetObject",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
                    }
                ],
            }
        ),
    )
    return s3


def test_peer_download_prefers_announced_transport(playground_dir, minio_server_gen):
    root = pathlib.Path(playground_dir)
    minio = minio_server_gen(port=19920)
    backend = SmallSea.SmallSeaBackend(root_dir=root)
    alice_hex = Provisioning.create_new_participant(root, "alice")
    Provisioning.add_cloud_storage(
        root,
        alice_hex,
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")

    app.state.backend = backend
    client = TestClient(app)
    session_hex = _request_and_confirm(client)

    fallback_bucket = f"ss-{team_result['berth_id_hex'][:16]}"
    announced_bucket = "peer-transport-announced"
    s3 = _make_bucket_public(minio, fallback_bucket)
    _make_bucket_public(minio, announced_bucket)
    s3.put_object(Bucket=fallback_bucket, Key="peer.txt", Body=b"fallback")
    s3.put_object(Bucket=announced_bucket, Key="peer.txt", Body=b"announced")

    Provisioning.announce_member_transport(
        root,
        alice_hex,
        "ProjectX",
        protocol="s3",
        url=minio["endpoint"],
        bucket=announced_bucket,
    )

    ok, data, _etag = backend._download_peer_file(
        session_hex,
        team_result["member_id_hex"],
        "peer.txt",
    )

    assert ok is True
    assert data == b"announced"


def test_peer_download_falls_back_when_announcement_signature_is_invalid(
    playground_dir, minio_server_gen
):
    root = pathlib.Path(playground_dir)
    minio = minio_server_gen(port=19930)
    backend = SmallSea.SmallSeaBackend(root_dir=root)
    alice_hex = Provisioning.create_new_participant(root, "alice")
    Provisioning.add_cloud_storage(
        root,
        alice_hex,
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")

    app.state.backend = backend
    client = TestClient(app)
    session_hex = _request_and_confirm(client)

    fallback_bucket = f"ss-{team_result['berth_id_hex'][:16]}"
    announced_bucket = "peer-transport-invalid"
    s3 = _make_bucket_public(minio, fallback_bucket)
    _make_bucket_public(minio, announced_bucket)
    s3.put_object(Bucket=fallback_bucket, Key="peer.txt", Body=b"fallback")
    s3.put_object(Bucket=announced_bucket, Key="peer.txt", Body=b"announced")

    announced = Provisioning.announce_member_transport(
        root,
        alice_hex,
        "ProjectX",
        protocol="s3",
        url=minio["endpoint"],
        bucket=announced_bucket,
    )
    team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(team_db)) as conn:
        conn.execute(
            "UPDATE member_transport_announcement SET signature = ? WHERE announcement_id = ?",
            (b"\x00" * 64, bytes.fromhex(announced["announcement_id_hex"])),
        )
        conn.commit()

    ok, data, _etag = backend._download_peer_file(
        session_hex,
        team_result["member_id_hex"],
        "peer.txt",
    )

    assert ok is True
    assert data == b"fallback"
