import json
import pathlib
import sqlite3

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.client import Config as BotoConfig
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from small_sea_hub.server import app
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    MemberBerthStorageAnnouncement,
    canonical_member_berth_storage_announcement_bytes,
)


def _request_and_confirm(client, *, team="ProjectX"):
    return _request_and_confirm_app(
        client,
        app_name="SmallSeaCollectiveCore",
        team=team,
    )


def _request_and_confirm_app(client, *, app_name, team="ProjectX"):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": app_name,
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


def _core_allocation_bucket(root, participant_hex, team_result):
    allocation = Provisioning.get_berth_cloud_allocation_for_berth(
        root,
        participant_hex,
        team_result["berth_id_hex"],
    )
    assert allocation is not None
    return allocation["location"]


def _signed_storage_announcement(
    *,
    member_id: bytes,
    berth_id: bytes,
    signer_private_key: bytes,
    signer_key_id: bytes,
    url: str,
    location: str,
):
    unsigned = MemberBerthStorageAnnouncement(
        announcement_id=Provisioning.uuid7(),
        member_id=member_id,
        berth_id=berth_id,
        protocol="s3",
        url=url,
        location=location,
        announced_at="2026-04-18T00:00:00+00:00",
        signer_key_id=signer_key_id,
        signature=b"",
    )
    signature = Ed25519PrivateKey.from_private_bytes(signer_private_key).sign(
        canonical_member_berth_storage_announcement_bytes(unsigned)
    )
    return MemberBerthStorageAnnouncement(
        announcement_id=unsigned.announcement_id,
        member_id=unsigned.member_id,
        berth_id=unsigned.berth_id,
        protocol=unsigned.protocol,
        url=unsigned.url,
        location=unsigned.location,
        announced_at=unsigned.announced_at,
        signer_key_id=unsigned.signer_key_id,
        signature=signature,
    )


def test_peer_download_uses_member_berth_storage_announcement(
    playground_dir, minio_server_gen
):
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

    fallback_bucket = _core_allocation_bucket(root, alice_hex, team_result)
    announced_bucket = "peer-transport-announced"
    s3 = _make_bucket_public(minio, fallback_bucket)
    _make_bucket_public(minio, announced_bucket)
    s3.put_object(Bucket=fallback_bucket, Key="peer.txt", Body=b"berth")
    s3.put_object(Bucket=announced_bucket, Key="peer.txt", Body=b"announced")

    Provisioning.publish_member_berth_storage_announcement(
        root,
        alice_hex,
        "ProjectX",
        bytes.fromhex(team_result["member_id_hex"]),
        bytes.fromhex(team_result["berth_id_hex"]),
        {
            "protocol": "s3",
            "url": minio["endpoint"],
            "location": announced_bucket,
        },
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

    fallback_bucket = _core_allocation_bucket(root, alice_hex, team_result)
    announced_bucket = "peer-transport-invalid"
    s3 = _make_bucket_public(minio, fallback_bucket)
    _make_bucket_public(minio, announced_bucket)
    s3.put_object(Bucket=fallback_bucket, Key="peer.txt", Body=b"fallback")
    s3.put_object(Bucket=announced_bucket, Key="peer.txt", Body=b"announced")

    announced = Provisioning.publish_member_berth_storage_announcement(
        root,
        alice_hex,
        "ProjectX",
        bytes.fromhex(team_result["member_id_hex"]),
        bytes.fromhex(team_result["berth_id_hex"]),
        {
            "protocol": "s3",
            "url": minio["endpoint"],
            "location": announced_bucket,
        },
    )
    team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(team_db)) as conn:
        conn.execute(
            "UPDATE member_berth_storage_announcement SET signature = ? WHERE announcement_id = ?",
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


def test_peer_download_falls_back_when_announcement_signer_loses_trust(
    playground_dir, minio_server_gen
):
    root = pathlib.Path(playground_dir)
    minio = minio_server_gen(port=19940)
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

    fallback_bucket = _core_allocation_bucket(root, alice_hex, team_result)
    announced_bucket = "peer-transport-untrusted"
    s3 = _make_bucket_public(minio, fallback_bucket)
    _make_bucket_public(minio, announced_bucket)
    s3.put_object(Bucket=fallback_bucket, Key="peer.txt", Body=b"fallback")
    s3.put_object(Bucket=announced_bucket, Key="peer.txt", Body=b"announced")

    linked_key, linked_private_key = generate_key_pair(ProtectionLevel.DAILY)
    linked_public_key = linked_key.public_key
    linked_cert = Provisioning.issue_device_link_for_member(
        root,
        alice_hex,
        "ProjectX",
        linked_public_key,
    )
    announcement = _signed_storage_announcement(
        member_id=bytes.fromhex(team_result["member_id_hex"]),
        berth_id=bytes.fromhex(team_result["berth_id_hex"]),
        signer_private_key=linked_private_key,
        signer_key_id=key_id_from_public(linked_public_key),
        url=minio["endpoint"],
        location=announced_bucket,
    )
    team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(team_db)) as conn:
        conn.execute(
            """
            INSERT INTO member_berth_storage_announcement
            (announcement_id, member_id, berth_id, protocol, url, location, announced_at, signer_key_id, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                announcement.announcement_id,
                announcement.member_id,
                announcement.berth_id,
                announcement.protocol,
                announcement.url,
                announcement.location,
                announcement.announced_at,
                announcement.signer_key_id,
                announcement.signature,
            ),
        )
        conn.commit()

    with sqlite3.connect(str(team_db)) as conn:
        conn.execute(
            "DELETE FROM key_certificate WHERE cert_id = ?",
            (linked_cert.cert_id,),
        )
        conn.commit()

    ok, data, _etag = backend._download_peer_file(
        session_hex,
        team_result["member_id_hex"],
        "peer.txt",
    )

    assert ok is True
    assert data == b"fallback"


def test_app_berth_peer_read_without_announcement_returns_missing(
    playground_dir, minio_server_gen
):
    root = pathlib.Path(playground_dir)
    minio = minio_server_gen(port=19950)
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
    Provisioning.register_app_for_participant(root, alice_hex, "SharedFileVault")
    Provisioning.activate_app_for_team(root, alice_hex, "ProjectX", "SharedFileVault")

    app.state.backend = backend
    client = TestClient(app)
    session_hex = _request_and_confirm_app(client, app_name="SharedFileVault")

    with pytest.raises(SmallSea.SmallSeaNotFoundExn):
        backend._download_peer_file(
            session_hex,
            team_result["member_id_hex"],
            "peer.txt",
        )


def test_app_berth_peer_read_uses_announcement_location(
    playground_dir, minio_server_gen
):
    root = pathlib.Path(playground_dir)
    minio = minio_server_gen(port=19960)
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
    Provisioning.register_app_for_participant(root, alice_hex, "SharedFileVault")
    Provisioning.activate_app_for_team(
        root,
        alice_hex,
        "ProjectX",
        "SharedFileVault",
    )
    app_berth_id = Provisioning._resolve_berth_id_for_allocation(
        root,
        alice_hex,
        "ProjectX",
        "SharedFileVault",
    )

    app.state.backend = backend
    client = TestClient(app)
    session_hex = _request_and_confirm_app(client, app_name="SharedFileVault")

    announced_bucket = "app-berth-announced"
    s3 = _make_bucket_public(minio, announced_bucket)
    s3.put_object(Bucket=announced_bucket, Key="peer.txt", Body=b"app-announced")
    Provisioning.publish_member_berth_storage_announcement(
        root,
        alice_hex,
        "ProjectX",
        bytes.fromhex(team_result["member_id_hex"]),
        app_berth_id,
        {
            "protocol": "s3",
            "url": minio["endpoint"],
            "location": announced_bucket,
        },
    )

    ok, data, _etag = backend._download_peer_file(
        session_hex,
        team_result["member_id_hex"],
        "peer.txt",
    )

    assert ok is True
    assert data == b"app-announced"
