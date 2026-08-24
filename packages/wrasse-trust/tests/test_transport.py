import json
from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wrasse_trust.identity import (
    issue_device_link_cert,
    issue_membership_cert,
    trusted_device_keys_for_teammate,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    TeammateBerthStorageAnnouncement,
    canonical_teammate_berth_storage_announcement_bytes,
    key_certificate_from_team_db_record,
    select_effective_teammate_berth_storage,
)


TEAM_ID = bytes.fromhex("11" * 16)
TEAMMATE_ID = bytes.fromhex("22" * 16)
BERTH_ID = bytes.fromhex("55" * 16)


def _signed_storage_announcement(
    *,
    announcement_id: bytes,
    teammate_id: bytes,
    berth_id: bytes,
    protocol: str,
    url: str,
    location: str,
    announced_at: str,
    signer_private_key: bytes,
    signer_key_id: bytes,
) -> TeammateBerthStorageAnnouncement:
    unsigned = TeammateBerthStorageAnnouncement(
        announcement_id=announcement_id,
        teammate_id=teammate_id,
        berth_id=berth_id,
        protocol=protocol,
        url=url,
        location=location,
        announced_at=announced_at,
        signer_key_id=signer_key_id,
        signature=b"",
    )
    signature = Ed25519PrivateKey.from_private_bytes(signer_private_key).sign(
        canonical_teammate_berth_storage_announcement_bytes(unsigned)
    )
    return replace(unsigned, signature=signature)


def test_key_certificate_from_team_db_record_injects_team_id_for_trust_lookup():
    founder_key, founder_private_key = generate_key_pair(ProtectionLevel.DAILY)
    membership = issue_membership_cert(
        founder_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        issuer_teammate_id=TEAMMATE_ID,
        admitted_teammate_id=TEAMMATE_ID,
    )
    reconstructed = key_certificate_from_team_db_record(
        team_id=TEAM_ID,
        cert_id=membership.cert_id,
        cert_type=membership.cert_type.value,
        subject_key_id=membership.subject_key_id,
        subject_public_key=membership.subject_public_key,
        issuer_key_id=membership.issuer_key_id,
        issuer_teammate_id=membership.issuer_participant_id,
        issued_at=membership.issued_at_iso,
        claims_json=json.dumps(membership.claims, sort_keys=True),
        signature=membership.signature,
    )

    trusted = trusted_device_keys_for_teammate([reconstructed], TEAM_ID, TEAMMATE_ID)

    assert founder_key.public_key in trusted


def test_teammate_berth_storage_canonical_bytes_are_stable():
    announcement = TeammateBerthStorageAnnouncement(
        announcement_id=bytes.fromhex("01" * 16),
        teammate_id=bytes.fromhex("02" * 16),
        berth_id=bytes.fromhex("03" * 16),
        protocol="s3",
        url="http://minio.example",
        location="ss-abc123",
        announced_at="2026-01-02T03:04:05+00:00",
        signer_key_id=bytes.fromhex("04" * 32),
        signature=b"ignored",
    )

    assert canonical_teammate_berth_storage_announcement_bytes(announcement) == (
        b'{"announced_at":"2026-01-02T03:04:05+00:00",'
        b'"announcement_id":"01010101010101010101010101010101",'
        b'"berth_id":"03030303030303030303030303030303",'
        b'"location":"ss-abc123",'
        b'"protocol":"s3",'
        b'"signer_key_id":"0404040404040404040404040404040404040404040404040404040404040404",'
        b'"teammate_id":"02020202020202020202020202020202",'
        b'"url":"http://minio.example"}'
    )


def test_select_effective_teammate_berth_storage_uses_announcement_id_and_berth():
    founder_key, founder_private_key = generate_key_pair(ProtectionLevel.DAILY)
    membership = issue_membership_cert(
        founder_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        issuer_teammate_id=TEAMMATE_ID,
        admitted_teammate_id=TEAMMATE_ID,
    )
    signer_key_id = key_id_from_public(founder_key.public_key)
    other_berth = _signed_storage_announcement(
        announcement_id=bytes.fromhex("03" * 16),
        teammate_id=TEAMMATE_ID,
        berth_id=bytes.fromhex("66" * 16),
        protocol="s3",
        url="http://wrong-berth.example",
        location="wrong-berth-location",
        announced_at="2026-01-01T00:00:00+00:00",
        signer_private_key=founder_private_key,
        signer_key_id=signer_key_id,
    )
    older = _signed_storage_announcement(
        announcement_id=bytes.fromhex("01" * 16),
        teammate_id=TEAMMATE_ID,
        berth_id=BERTH_ID,
        protocol="s3",
        url="http://future.example",
        location="future-location",
        announced_at="2099-01-01T00:00:00+00:00",
        signer_private_key=founder_private_key,
        signer_key_id=signer_key_id,
    )
    newer = _signed_storage_announcement(
        announcement_id=bytes.fromhex("02" * 16),
        teammate_id=TEAMMATE_ID,
        berth_id=BERTH_ID,
        protocol="s3",
        url="http://current.example",
        location="current-location",
        announced_at="2026-01-01T00:00:00+00:00",
        signer_private_key=founder_private_key,
        signer_key_id=signer_key_id,
    )

    selection = select_effective_teammate_berth_storage(
        teammate_id=TEAMMATE_ID,
        berth_id=BERTH_ID,
        announcements=[older, newer, other_berth],
        certs=[membership],
        team_id=TEAM_ID,
        device_public_keys_by_key_id={signer_key_id: founder_key.public_key},
    )

    assert selection.status == "announced"
    assert selection.transport is not None
    assert selection.transport.url == "http://current.example"
    assert selection.transport.location == "current-location"


def test_select_effective_teammate_berth_storage_binds_signer_key_id_in_signature():
    founder_key, founder_private_key = generate_key_pair(ProtectionLevel.DAILY)
    linked_key, linked_private_key = generate_key_pair(ProtectionLevel.DAILY)
    membership = issue_membership_cert(
        founder_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        issuer_teammate_id=TEAMMATE_ID,
        admitted_teammate_id=TEAMMATE_ID,
    )
    device_link = issue_device_link_cert(
        linked_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        TEAMMATE_ID,
    )
    founder_key_id = key_id_from_public(founder_key.public_key)
    linked_key_id = key_id_from_public(linked_key.public_key)
    valid = _signed_storage_announcement(
        announcement_id=bytes.fromhex("03" * 16),
        teammate_id=TEAMMATE_ID,
        berth_id=BERTH_ID,
        protocol="s3",
        url="http://valid.example",
        location="valid-location",
        announced_at="2026-01-01T00:00:00+00:00",
        signer_private_key=founder_private_key,
        signer_key_id=founder_key_id,
    )
    tampered = replace(valid, signer_key_id=linked_key_id)

    selection = select_effective_teammate_berth_storage(
        teammate_id=TEAMMATE_ID,
        berth_id=BERTH_ID,
        announcements=[tampered],
        certs=[membership, device_link],
        team_id=TEAM_ID,
        device_public_keys_by_key_id={
            founder_key_id: founder_key.public_key,
            linked_key_id: linked_key.public_key,
        },
    )

    # Re-pointing signer_key_id at another trusted device of the same teammate
    # breaks the signature over the canonical bytes, so the row is rejected.
    assert linked_private_key is not None
    assert selection.status == "missing"
    assert selection.transport is None


def test_select_effective_teammate_berth_storage_rejects_other_teammates_signer():
    alice_key, alice_private_key = generate_key_pair(ProtectionLevel.DAILY)
    bob_key, bob_private_key = generate_key_pair(ProtectionLevel.DAILY)
    alice_teammate_id = bytes.fromhex("33" * 16)
    bob_teammate_id = bytes.fromhex("44" * 16)
    alice_membership = issue_membership_cert(
        alice_key,
        alice_key,
        alice_private_key,
        TEAM_ID,
        issuer_teammate_id=alice_teammate_id,
        admitted_teammate_id=alice_teammate_id,
    )
    bob_membership = issue_membership_cert(
        bob_key,
        bob_key,
        bob_private_key,
        TEAM_ID,
        issuer_teammate_id=bob_teammate_id,
        admitted_teammate_id=bob_teammate_id,
    )
    bad_announcement = _signed_storage_announcement(
        announcement_id=bytes.fromhex("04" * 16),
        teammate_id=alice_teammate_id,
        berth_id=BERTH_ID,
        protocol="s3",
        url="http://wrong-teammate.example",
        location="wrong-teammate-location",
        announced_at="2026-01-01T00:00:00+00:00",
        signer_private_key=bob_private_key,
        signer_key_id=key_id_from_public(bob_key.public_key),
    )

    selection = select_effective_teammate_berth_storage(
        teammate_id=alice_teammate_id,
        berth_id=BERTH_ID,
        announcements=[bad_announcement],
        certs=[alice_membership, bob_membership],
        team_id=TEAM_ID,
        device_public_keys_by_key_id={
            key_id_from_public(alice_key.public_key): alice_key.public_key,
            key_id_from_public(bob_key.public_key): bob_key.public_key,
        },
    )

    # Bob's key is trusted for Bob, not for Alice, so Alice's row is rejected.
    assert selection.status == "missing"
    assert selection.transport is None
