import pathlib
import sqlite3
from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import small_sea_manager.provisioning as Provisioning
from small_sea_manager.manager import TeamManager
from small_sea_manager.web import create_app
from small_sea_note_to_self.ids import uuid7
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    MemberTransportAnnouncement,
    canonical_member_transport_announcement_bytes,
)


def _signed_announcement(
    *,
    member_id: bytes,
    signer_private_key: bytes,
    signer_key_id: bytes,
    bucket: str,
) -> MemberTransportAnnouncement:
    unsigned = MemberTransportAnnouncement(
        announcement_id=uuid7(),
        member_id=member_id,
        protocol="localfolder",
        url="file:///transport-announced",
        bucket=bucket,
        announced_at="2026-04-17T00:00:00+00:00",
        signer_key_id=signer_key_id,
        signature=b"",
    )
    signature = Ed25519PrivateKey.from_private_bytes(signer_private_key).sign(
        canonical_member_transport_announcement_bytes(unsigned)
    )
    return replace(unsigned, signature=signature)


def test_announce_member_transport_updates_manager_member_status(playground_dir):
    root = pathlib.Path(playground_dir)
    cloud_dir = root / "cloud"
    cloud_dir.mkdir()

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(cloud_dir))
    Provisioning.create_team(root, alice_hex, "ProjectX")

    manager = TeamManager(root, alice_hex)
    team_before = manager.get_team("ProjectX")
    alice_before = next(member for member in team_before["members"] if member["id"] == team_before["self_in_team"])
    assert alice_before["transport_status"] == "legacy-fallback"
    assert alice_before["needs_transport_announcement"] is False

    announced = manager.announce_member_transport(
        "ProjectX",
        protocol="localfolder",
        url="file:///transport-announced",
        bucket="announced-bucket",
    )

    team_after = manager.get_team("ProjectX")
    alice_after = next(member for member in team_after["members"] if member["id"] == team_after["self_in_team"])
    assert announced["bucket"] == "announced-bucket"
    assert alice_after["transport_status"] == "announced"
    assert alice_after["needs_transport_announcement"] is False
    assert alice_after["effective_transport"]["bucket"] == "announced-bucket"


def test_transport_announcement_becomes_inert_after_device_link_removal(playground_dir):
    root = pathlib.Path(playground_dir)
    cloud_dir = root / "cloud"
    cloud_dir.mkdir()

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(cloud_dir))
    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")
    alice_member_id = bytes.fromhex(team_result["member_id_hex"])

    linked_key, linked_private_key = generate_key_pair(ProtectionLevel.DAILY)
    linked_public_key = linked_key.public_key
    linked_cert = Provisioning.issue_device_link_for_member(
        root,
        alice_hex,
        "ProjectX",
        linked_public_key,
    )
    announcement = _signed_announcement(
        member_id=alice_member_id,
        signer_private_key=linked_private_key,
        signer_key_id=key_id_from_public(linked_public_key),
        bucket="linked-device-bucket",
    )

    team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(team_db)) as conn:
        conn.execute(
            """
            INSERT INTO member_transport_announcement
            (announcement_id, member_id, protocol, url, bucket, announced_at, signer_key_id, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                announcement.announcement_id,
                announcement.member_id,
                announcement.protocol,
                announcement.url,
                announcement.bucket,
                announcement.announced_at,
                announcement.signer_key_id,
                announcement.signature,
            ),
        )
        conn.commit()

    manager = TeamManager(root, alice_hex)
    team_before = manager.get_team("ProjectX")
    alice_before = next(member for member in team_before["members"] if member["id"] == team_before["self_in_team"])
    assert alice_before["transport_status"] == "announced"
    assert alice_before["effective_transport"]["bucket"] == "linked-device-bucket"

    with sqlite3.connect(str(team_db)) as conn:
        conn.execute(
            "DELETE FROM key_certificate WHERE cert_id = ?",
            (linked_cert.cert_id,),
        )
        conn.commit()

    team_after = manager.get_team("ProjectX")
    alice_after = next(member for member in team_after["members"] if member["id"] == team_after["self_in_team"])
    assert alice_after["transport_status"] == "legacy-fallback"
    assert alice_after["needs_transport_announcement"] is False


def test_transport_announcement_route_updates_team_detail(playground_dir):
    root = pathlib.Path(playground_dir)
    cloud_dir = root / "cloud"
    cloud_dir.mkdir()

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(cloud_dir))
    Provisioning.create_team(root, alice_hex, "ProjectX")

    app = create_app(root, alice_hex)
    client = TestClient(app)

    response = client.post(
        "/teams/ProjectX/transport",
        data={
            "protocol": "localfolder",
            "url": "file:///transport-announced",
            "bucket": "ui-bucket",
        },
    )

    assert response.status_code == 200
    assert "Transport announcement published." in response.text
    assert "announced" in response.text
    assert "ui-bucket" in response.text
