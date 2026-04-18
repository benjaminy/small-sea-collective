import json
from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wrasse_trust.identity import (
    issue_device_link_cert,
    issue_membership_cert,
    trusted_device_keys_for_member,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    MemberTransportAnnouncement,
    TransportEndpoint,
    canonical_member_transport_announcement_bytes,
    key_certificate_from_team_db_record,
    select_effective_member_transport,
)


TEAM_ID = bytes.fromhex("11" * 16)
MEMBER_ID = bytes.fromhex("22" * 16)


def _signed_announcement(
    *,
    announcement_id: bytes,
    member_id: bytes,
    protocol: str,
    url: str,
    bucket: str,
    announced_at: str,
    signer_private_key: bytes,
    signer_key_id: bytes,
) -> MemberTransportAnnouncement:
    unsigned = MemberTransportAnnouncement(
        announcement_id=announcement_id,
        member_id=member_id,
        protocol=protocol,
        url=url,
        bucket=bucket,
        announced_at=announced_at,
        signer_key_id=signer_key_id,
        signature=b"",
    )
    signature = Ed25519PrivateKey.from_private_bytes(signer_private_key).sign(
        canonical_member_transport_announcement_bytes(unsigned)
    )
    return replace(unsigned, signature=signature)


def test_key_certificate_from_team_db_record_injects_team_id_for_trust_lookup():
    founder_key, founder_private_key = generate_key_pair(ProtectionLevel.DAILY)
    membership = issue_membership_cert(
        founder_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        issuer_member_id=MEMBER_ID,
        admitted_member_id=MEMBER_ID,
    )
    reconstructed = key_certificate_from_team_db_record(
        team_id=TEAM_ID,
        cert_id=membership.cert_id,
        cert_type=membership.cert_type.value,
        subject_key_id=membership.subject_key_id,
        subject_public_key=membership.subject_public_key,
        issuer_key_id=membership.issuer_key_id,
        issuer_member_id=membership.issuer_participant_id,
        issued_at=membership.issued_at_iso,
        claims_json=json.dumps(membership.claims, sort_keys=True),
        signature=membership.signature,
    )

    trusted = trusted_device_keys_for_member([reconstructed], TEAM_ID, MEMBER_ID)

    assert founder_key.public_key in trusted


def test_select_effective_member_transport_uses_announcement_id_not_announced_at():
    founder_key, founder_private_key = generate_key_pair(ProtectionLevel.DAILY)
    membership = issue_membership_cert(
        founder_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        issuer_member_id=MEMBER_ID,
        admitted_member_id=MEMBER_ID,
    )
    signer_key_id = key_id_from_public(founder_key.public_key)
    older = _signed_announcement(
        announcement_id=bytes.fromhex("01" * 16),
        member_id=MEMBER_ID,
        protocol="s3",
        url="http://future.example",
        bucket="future-bucket",
        announced_at="2099-01-01T00:00:00+00:00",
        signer_private_key=founder_private_key,
        signer_key_id=signer_key_id,
    )
    newer = _signed_announcement(
        announcement_id=bytes.fromhex("02" * 16),
        member_id=MEMBER_ID,
        protocol="s3",
        url="http://current.example",
        bucket="current-bucket",
        announced_at="2026-01-01T00:00:00+00:00",
        signer_private_key=founder_private_key,
        signer_key_id=signer_key_id,
    )

    selection = select_effective_member_transport(
        member_id=MEMBER_ID,
        announcements=[older, newer],
        certs=[membership],
        team_id=TEAM_ID,
        device_public_keys_by_key_id={signer_key_id: founder_key.public_key},
    )

    assert selection.status == "announced"
    assert selection.transport is not None
    assert selection.transport.url == "http://current.example"
    assert selection.transport.bucket == "current-bucket"


def test_select_effective_member_transport_binds_signer_key_id_in_signature():
    founder_key, founder_private_key = generate_key_pair(ProtectionLevel.DAILY)
    linked_key, linked_private_key = generate_key_pair(ProtectionLevel.DAILY)
    membership = issue_membership_cert(
        founder_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        issuer_member_id=MEMBER_ID,
        admitted_member_id=MEMBER_ID,
    )
    device_link = issue_device_link_cert(
        linked_key,
        founder_key,
        founder_private_key,
        TEAM_ID,
        MEMBER_ID,
    )
    founder_key_id = key_id_from_public(founder_key.public_key)
    linked_key_id = key_id_from_public(linked_key.public_key)
    valid = _signed_announcement(
        announcement_id=bytes.fromhex("03" * 16),
        member_id=MEMBER_ID,
        protocol="s3",
        url="http://valid.example",
        bucket="valid-bucket",
        announced_at="2026-01-01T00:00:00+00:00",
        signer_private_key=founder_private_key,
        signer_key_id=founder_key_id,
    )
    tampered = replace(valid, signer_key_id=linked_key_id)

    selection = select_effective_member_transport(
        member_id=MEMBER_ID,
        announcements=[tampered],
        certs=[membership, device_link],
        team_id=TEAM_ID,
        device_public_keys_by_key_id={
            founder_key_id: founder_key.public_key,
            linked_key_id: linked_key.public_key,
        },
        legacy_fallback=TransportEndpoint(
            protocol="s3",
            url="http://fallback.example",
            bucket="fallback-bucket",
        ),
    )

    assert linked_private_key is not None
    assert selection.status == "legacy-fallback"
    assert selection.transport is not None
    assert selection.transport.bucket == "fallback-bucket"


def test_select_effective_member_transport_rejects_other_members_signer():
    alice_key, alice_private_key = generate_key_pair(ProtectionLevel.DAILY)
    bob_key, bob_private_key = generate_key_pair(ProtectionLevel.DAILY)
    alice_member_id = bytes.fromhex("33" * 16)
    bob_member_id = bytes.fromhex("44" * 16)
    alice_membership = issue_membership_cert(
        alice_key,
        alice_key,
        alice_private_key,
        TEAM_ID,
        issuer_member_id=alice_member_id,
        admitted_member_id=alice_member_id,
    )
    bob_membership = issue_membership_cert(
        bob_key,
        bob_key,
        bob_private_key,
        TEAM_ID,
        issuer_member_id=bob_member_id,
        admitted_member_id=bob_member_id,
    )
    bad_announcement = _signed_announcement(
        announcement_id=bytes.fromhex("04" * 16),
        member_id=alice_member_id,
        protocol="s3",
        url="http://wrong-member.example",
        bucket="wrong-member-bucket",
        announced_at="2026-01-01T00:00:00+00:00",
        signer_private_key=bob_private_key,
        signer_key_id=key_id_from_public(bob_key.public_key),
    )

    selection = select_effective_member_transport(
        member_id=alice_member_id,
        announcements=[bad_announcement],
        certs=[alice_membership, bob_membership],
        team_id=TEAM_ID,
        device_public_keys_by_key_id={
            key_id_from_public(alice_key.public_key): alice_key.public_key,
            key_id_from_public(bob_key.public_key): bob_key.public_key,
        },
        legacy_fallback=TransportEndpoint(
            protocol="s3",
            url="http://fallback.example",
            bucket="fallback-bucket",
        ),
    )

    assert selection.status == "legacy-fallback"
    assert selection.transport is not None
    assert selection.transport.bucket == "fallback-bucket"
