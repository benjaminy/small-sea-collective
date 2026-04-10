"""Micro tests for keys, identity, ceremony, and trust modules."""

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wrasse_trust.ceremony import (
    complete_ceremony,
    decode_ceremony_payload,
    extract_hierarchy_certs,
    extract_target_key,
    generate_ceremony_payload,
    verify_ceremony_payload,
)
from wrasse_trust.identity import (
    CertType,
    KeyCertificate,
    _canonical_cert_bytes,
    build_hierarchy_certs,
    issue_device_link_cert,
    issue_cert,
    issue_membership_cert,
    issue_revocation,
    trusted_device_keys_for_member,
    verify_cert,
    verify_device_link_cert,
    verify_membership_cert,
    verify_revocation,
)
from wrasse_trust.keys import (
    ProtectionLevel,
    generate_hierarchy,
    generate_key_pair,
)
from wrasse_trust.trust import CertGraph, find_trust_paths

ALICE_ID = b"alice-id-bytes00"
BOB_ID = b"bob-id-bytes0000"
CAROL_ID = b"carol-id-bytes00"


def _manual_cert(
    subject_key,
    issuer_key,
    issuer_private_key,
    issuer_participant_id,
    cert_type,
    team_id=None,
    claims=None,
):
    claims = claims or {}
    issued_at_iso = "2026-04-07T00:00:00+00:00"
    canonical = _canonical_cert_bytes(
        cert_type,
        team_id,
        subject_key.key_id,
        subject_key.public_key,
        issuer_key.key_id,
        issuer_participant_id,
        issued_at_iso,
        claims,
    )
    cert_id = hashlib.sha256(canonical).digest()[:16]
    signature = Ed25519PrivateKey.from_private_bytes(issuer_private_key).sign(canonical)
    return KeyCertificate(
        cert_id=cert_id,
        cert_type=cert_type,
        team_id=team_id,
        subject_key_id=subject_key.key_id,
        subject_public_key=subject_key.public_key,
        issuer_key_id=issuer_key.key_id,
        issuer_participant_id=issuer_participant_id,
        issued_at_iso=issued_at_iso,
        claims=claims,
        signature=signature,
    )


def test_generate_key_pair():
    key, priv = generate_key_pair(ProtectionLevel.DAILY)
    assert len(key.key_id) == 16
    assert len(key.public_key) == 32
    assert len(priv) == 32
    assert key.protection_level == ProtectionLevel.DAILY
    assert key.parent_key_id is None


def test_generate_hierarchy():
    collection, privates = generate_hierarchy(ALICE_ID)

    assert collection.participant_id == ALICE_ID
    assert len(collection.keys) == 3
    assert len(collection.buried_keys()) == 1
    assert len(collection.guarded_keys()) == 1
    assert len(collection.daily_keys()) == 1

    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]
    daily = collection.daily_keys()[0]

    assert buried.parent_key_id is None
    assert guarded.parent_key_id == buried.key_id
    assert daily.parent_key_id == guarded.key_id

    assert len(privates) == 3
    assert buried.key_id in privates


def test_collection_find_key():
    collection, _ = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]

    found = collection.find_key(buried.key_id)
    assert found is not None
    assert found.key_id == buried.key_id

    assert collection.find_key(b"\x00" * 16) is None


def test_current_daily_key():
    collection, _ = generate_hierarchy(ALICE_ID)
    daily = collection.current_daily_key()
    assert daily.protection_level == ProtectionLevel.DAILY


def test_issue_and_verify_cert():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    cert = issue_cert(
        guarded, buried, privates[buried.key_id], ALICE_ID,
        cert_type=CertType.SELF_BINDING,
        claims={"type": "hierarchy"},
    )

    assert cert.subject_key_id == guarded.key_id
    assert cert.issuer_key_id == buried.key_id
    assert verify_cert(cert, buried.public_key)


def test_cert_verification_fails_with_wrong_key():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]
    daily = collection.daily_keys()[0]

    cert = issue_cert(
        guarded, buried, privates[buried.key_id], ALICE_ID,
        cert_type=CertType.SELF_BINDING,
    )

    assert not verify_cert(cert, daily.public_key)


def test_build_hierarchy_certs():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]
    daily = collection.daily_keys()[0]

    cert_bg, cert_gd = build_hierarchy_certs(
        buried, privates[buried.key_id],
        guarded, daily, privates[guarded.key_id],
        ALICE_ID,
    )

    assert cert_bg.subject_key_id == guarded.key_id
    assert cert_bg.issuer_key_id == buried.key_id
    assert verify_cert(cert_bg, buried.public_key)

    assert cert_gd.subject_key_id == daily.key_id
    assert cert_gd.issuer_key_id == guarded.key_id
    assert verify_cert(cert_gd, guarded.public_key)


def test_issue_and_verify_revocation():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    daily = collection.daily_keys()[0]

    rev = issue_revocation(
        daily, buried, privates[buried.key_id], ALICE_ID,
        reason="device stolen",
    )

    assert rev.revoked_key_id == daily.key_id
    assert verify_revocation(rev, buried.public_key)
    assert not verify_revocation(rev, daily.public_key)


def test_ceremony_roundtrip():
    alice_coll, alice_privs = generate_hierarchy(ALICE_ID)
    bob_coll, bob_privs = generate_hierarchy(BOB_ID)

    alice_buried = alice_coll.buried_keys()[0]
    alice_guarded = alice_coll.guarded_keys()[0]
    alice_daily = alice_coll.daily_keys()[0]

    cert_bg, cert_gd = build_hierarchy_certs(
        alice_buried, alice_privs[alice_buried.key_id],
        alice_guarded, alice_daily, alice_privs[alice_guarded.key_id],
        ALICE_ID,
    )

    payload_bytes = generate_ceremony_payload(
        ALICE_ID, alice_guarded, [cert_bg, cert_gd],
    )

    payload = decode_ceremony_payload(payload_bytes)
    target = extract_target_key(payload)
    assert target.key_id == alice_guarded.key_id
    assert target.public_key == alice_guarded.public_key

    hierarchy_certs = extract_hierarchy_certs(payload)
    assert len(hierarchy_certs) == 2
    assert verify_ceremony_payload(payload)

    bob_guarded = bob_coll.guarded_keys()[0]
    cross_cert = complete_ceremony(
        payload, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
    )

    assert cross_cert.subject_key_id == alice_guarded.key_id
    assert cross_cert.issuer_key_id == bob_guarded.key_id
    assert cross_cert.cert_type == CertType.CROSS_CERTIFICATION
    assert verify_cert(cross_cert, bob_guarded.public_key)
    assert cross_cert.claims["type"] == "ceremony"


def test_direct_trust_path():
    alice_coll, _ = generate_hierarchy(ALICE_ID)
    bob_coll, bob_privs = generate_hierarchy(BOB_ID)

    alice_guarded = alice_coll.guarded_keys()[0]
    bob_guarded = bob_coll.guarded_keys()[0]

    cert = issue_cert(
        alice_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )

    graph = CertGraph(certs=[cert])
    paths = find_trust_paths(
        alice_guarded.key_id,
        {bob_guarded.key_id},
        graph,
    )

    assert len(paths) == 1
    assert paths[0].anchor_key_id == bob_guarded.key_id
    assert paths[0].subject_key_id == alice_guarded.key_id
    assert len(paths[0].chain) == 1


def test_transitive_trust_path():
    alice_coll, _ = generate_hierarchy(ALICE_ID)
    bob_coll, bob_privs = generate_hierarchy(BOB_ID)
    carol_coll, carol_privs = generate_hierarchy(CAROL_ID)

    alice_guarded = alice_coll.guarded_keys()[0]
    bob_guarded = bob_coll.guarded_keys()[0]
    carol_guarded = carol_coll.guarded_keys()[0]

    cert_ca = issue_cert(
        alice_guarded, carol_guarded, carol_privs[carol_guarded.key_id], CAROL_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )
    cert_bc = issue_cert(
        carol_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )

    graph = CertGraph(certs=[cert_ca, cert_bc])
    paths = find_trust_paths(
        alice_guarded.key_id,
        {bob_guarded.key_id},
        graph,
    )

    assert len(paths) == 1
    assert len(paths[0].chain) == 2
    assert paths[0].anchor_key_id == bob_guarded.key_id


def test_no_trust_path():
    alice_coll, _ = generate_hierarchy(ALICE_ID)
    bob_coll, _ = generate_hierarchy(BOB_ID)

    graph = CertGraph()
    paths = find_trust_paths(
        alice_coll.guarded_keys()[0].key_id,
        {bob_coll.guarded_keys()[0].key_id},
        graph,
    )

    assert len(paths) == 0


def test_revoked_key_blocks_path():
    alice_coll, _ = generate_hierarchy(ALICE_ID)
    bob_coll, bob_privs = generate_hierarchy(BOB_ID)
    carol_coll, carol_privs = generate_hierarchy(CAROL_ID)

    alice_guarded = alice_coll.guarded_keys()[0]
    bob_guarded = bob_coll.guarded_keys()[0]
    carol_guarded = carol_coll.guarded_keys()[0]

    cert_ca = issue_cert(
        alice_guarded, carol_guarded, carol_privs[carol_guarded.key_id], CAROL_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )
    cert_bc = issue_cert(
        carol_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )

    carol_buried = carol_coll.buried_keys()[0]
    rev = issue_revocation(
        carol_guarded, carol_buried, carol_privs[carol_buried.key_id], CAROL_ID,
        reason="compromised",
    )

    graph = CertGraph(certs=[cert_ca, cert_bc], revocations=[rev])
    paths = find_trust_paths(
        alice_guarded.key_id,
        {bob_guarded.key_id},
        graph,
    )

    assert len(paths) == 0


def test_hierarchy_trust_path():
    alice_coll, alice_privs = generate_hierarchy(ALICE_ID)
    bob_coll, bob_privs = generate_hierarchy(BOB_ID)

    alice_buried = alice_coll.buried_keys()[0]
    alice_guarded = alice_coll.guarded_keys()[0]
    alice_daily = alice_coll.daily_keys()[0]
    bob_guarded = bob_coll.guarded_keys()[0]

    cert_bg, cert_gd = build_hierarchy_certs(
        alice_buried, alice_privs[alice_buried.key_id],
        alice_guarded, alice_daily, alice_privs[alice_guarded.key_id],
        ALICE_ID,
    )

    cert_cross = issue_cert(
        alice_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )

    graph = CertGraph(certs=[cert_bg, cert_gd, cert_cross])
    paths = find_trust_paths(
        alice_daily.key_id,
        {bob_guarded.key_id},
        graph,
    )

    assert len(paths) == 1
    assert len(paths[0].chain) == 2
    assert paths[0].anchor_key_id == bob_guarded.key_id


def test_multiple_trust_paths():
    alice_coll, _ = generate_hierarchy(ALICE_ID)
    bob_coll, bob_privs = generate_hierarchy(BOB_ID)
    carol_coll, carol_privs = generate_hierarchy(CAROL_ID)

    alice_guarded = alice_coll.guarded_keys()[0]
    bob_guarded = bob_coll.guarded_keys()[0]
    carol_guarded = carol_coll.guarded_keys()[0]

    cert_ba = issue_cert(
        alice_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )
    cert_ca = issue_cert(
        alice_guarded, carol_guarded, carol_privs[carol_guarded.key_id], CAROL_ID,
        cert_type=CertType.CROSS_CERTIFICATION,
    )

    graph = CertGraph(certs=[cert_ba, cert_ca])
    paths = find_trust_paths(
        alice_guarded.key_id,
        {bob_guarded.key_id, carol_guarded.key_id},
        graph,
    )

    assert len(paths) == 2
    anchors = {p.anchor_key_id for p in paths}
    assert bob_guarded.key_id in anchors
    assert carol_guarded.key_id in anchors


def test_cert_type_string_stability():
    expected = {
        CertType.SELF_BINDING: "self_binding",
        CertType.DEVICE_BINDING: "device_binding",
        CertType.DEVICE_LINK: "device_link",
        CertType.CROSS_CERTIFICATION: "cross_certification",
        CertType.MEMBERSHIP: "membership",
        CertType.SUCCESSION: "succession",
        CertType.IDENTITY_LINK: "identity_link",
        CertType.ATTESTATION: "attestation",
        CertType.AMBIENT_PROXIMITY: "ambient_proximity",
        CertType.REVOCATION: "revocation",
    }
    assert {cert_type: cert_type.value for cert_type in CertType} == expected


def test_issue_cert_requires_explicit_cert_type():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    with pytest.raises(TypeError):
        issue_cert(guarded, buried, privates[buried.key_id], ALICE_ID)


@pytest.mark.parametrize(
    "cert_type",
    [
        CertType.SUCCESSION,
        CertType.IDENTITY_LINK,
        CertType.ATTESTATION,
        CertType.AMBIENT_PROXIMITY,
        CertType.REVOCATION,
    ],
)
def test_issue_cert_rejects_reserved_but_unsupported_types(cert_type):
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    with pytest.raises(ValueError):
        issue_cert(
            guarded,
            buried,
            privates[buried.key_id],
            ALICE_ID,
            cert_type=cert_type,
        )


def test_issue_membership_cert_round_trip():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    team_id = b"team-id-bytes-01"
    admitted_member_id = b"member-id-bytes1"

    cert = issue_membership_cert(
        subject_key=guarded,
        issuer_key=buried,
        issuer_private_key=privates[buried.key_id],
        team_id=team_id,
        issuer_member_id=ALICE_ID,
        admitted_member_id=admitted_member_id,
    )

    assert verify_membership_cert(
        cert,
        issuer_public_key=buried.public_key,
        team_id=team_id,
        issuer_member_id=ALICE_ID,
        admitted_member_id=admitted_member_id,
        subject_public_key=guarded.public_key,
    )


def test_issue_device_link_cert_round_trip():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    team_id = b"team-id-bytes-01"
    member_id = b"member-id-bytes1"

    cert = issue_device_link_cert(
        subject_key=guarded,
        issuer_key=buried,
        issuer_private_key=privates[buried.key_id],
        team_id=team_id,
        member_id=member_id,
    )

    assert verify_device_link_cert(
        cert,
        issuer_public_key=buried.public_key,
        team_id=team_id,
        member_id=member_id,
        subject_public_key=guarded.public_key,
    )


def test_trusted_device_keys_for_member_allows_transitive_device_links():
    team_id = b"team-id-bytes-01"
    alice_member_id = b"alice-member-id0"
    bob_member_id = b"bob-member-id0000"

    alice_initial, alice_initial_priv = generate_key_pair(ProtectionLevel.DAILY)
    alice_laptop, alice_laptop_priv = generate_key_pair(ProtectionLevel.DAILY)
    bob_initial, bob_initial_priv = generate_key_pair(ProtectionLevel.DAILY)

    certs = [
        issue_membership_cert(
            subject_key=alice_initial,
            issuer_key=alice_initial,
            issuer_private_key=alice_initial_priv,
            team_id=team_id,
            issuer_member_id=alice_member_id,
            admitted_member_id=alice_member_id,
        ),
        issue_membership_cert(
            subject_key=bob_initial,
            issuer_key=alice_initial,
            issuer_private_key=alice_initial_priv,
            team_id=team_id,
            issuer_member_id=alice_member_id,
            admitted_member_id=bob_member_id,
        ),
        issue_device_link_cert(
            subject_key=alice_laptop,
            issuer_key=alice_initial,
            issuer_private_key=alice_initial_priv,
            team_id=team_id,
            member_id=alice_member_id,
        ),
    ]

    trusted = trusted_device_keys_for_member(certs, team_id, alice_member_id)
    assert alice_initial.public_key in trusted
    assert alice_laptop.public_key in trusted
    # The Bob membership cert is not a device_link for Alice, so Bob's key must
    # not leak into Alice's device set.
    assert bob_initial.public_key not in trusted


def test_trusted_device_keys_for_member_accepts_transitive_non_founding_signer():
    team_id = b"team-id-bytes-01"
    member_id = b"alice-member-id0"

    first_device, first_priv = generate_key_pair(ProtectionLevel.DAILY)
    second_device, second_priv = generate_key_pair(ProtectionLevel.DAILY)
    third_device, _third_priv = generate_key_pair(ProtectionLevel.DAILY)

    certs = [
        issue_membership_cert(
            subject_key=first_device,
            issuer_key=first_device,
            issuer_private_key=first_priv,
            team_id=team_id,
            issuer_member_id=member_id,
            admitted_member_id=member_id,
        ),
        issue_device_link_cert(
            subject_key=second_device,
            issuer_key=first_device,
            issuer_private_key=first_priv,
            team_id=team_id,
            member_id=member_id,
        ),
        issue_device_link_cert(
            subject_key=third_device,
            issuer_key=second_device,
            issuer_private_key=second_priv,
            team_id=team_id,
            member_id=member_id,
        ),
    ]

    trusted = trusted_device_keys_for_member(certs, team_id, member_id)
    assert first_device.public_key in trusted
    assert second_device.public_key in trusted
    assert third_device.public_key in trusted


def test_trusted_device_keys_for_member_ignores_unknown_signer():
    team_id = b"team-id-bytes-01"
    member_id = b"alice-member-id0"

    founding_device, founding_priv = generate_key_pair(ProtectionLevel.DAILY)
    stranger_device, stranger_priv = generate_key_pair(ProtectionLevel.DAILY)
    candidate_device, _candidate_priv = generate_key_pair(ProtectionLevel.DAILY)

    certs = [
        issue_membership_cert(
            subject_key=founding_device,
            issuer_key=founding_device,
            issuer_private_key=founding_priv,
            team_id=team_id,
            issuer_member_id=member_id,
            admitted_member_id=member_id,
        ),
        issue_device_link_cert(
            subject_key=candidate_device,
            issuer_key=stranger_device,
            issuer_private_key=stranger_priv,
            team_id=team_id,
            member_id=member_id,
        ),
    ]

    trusted = trusted_device_keys_for_member(certs, team_id, member_id)
    assert founding_device.public_key in trusted
    assert candidate_device.public_key not in trusted


def test_extract_hierarchy_certs_rejects_missing_cert_type():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]
    daily = collection.daily_keys()[0]
    cert_bg, cert_gd = build_hierarchy_certs(
        buried, privates[buried.key_id],
        guarded, daily, privates[guarded.key_id],
        ALICE_ID,
    )

    payload = decode_ceremony_payload(generate_ceremony_payload(ALICE_ID, guarded, [cert_bg, cert_gd]))
    del payload["hierarchy_certs"][0]["cert_type"]

    with pytest.raises(KeyError):
        extract_hierarchy_certs(payload)


def test_extract_hierarchy_certs_rejects_unknown_cert_type():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]
    daily = collection.daily_keys()[0]
    cert_bg, cert_gd = build_hierarchy_certs(
        buried, privates[buried.key_id],
        guarded, daily, privates[guarded.key_id],
        ALICE_ID,
    )

    payload = decode_ceremony_payload(generate_ceremony_payload(ALICE_ID, guarded, [cert_bg, cert_gd]))
    payload["hierarchy_certs"][0]["cert_type"] = "unknown_type"

    with pytest.raises(ValueError):
        extract_hierarchy_certs(payload)


@pytest.mark.parametrize(
    "cert_type",
    [
        CertType.SUCCESSION,
        CertType.IDENTITY_LINK,
        CertType.ATTESTATION,
        CertType.AMBIENT_PROXIMITY,
        CertType.REVOCATION,
    ],
)
def test_verify_cert_rejects_reserved_but_unsupported_types(cert_type):
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    cert = _manual_cert(
        guarded,
        buried,
        privates[buried.key_id],
        ALICE_ID,
        cert_type=cert_type,
    )

    assert not verify_cert(cert, buried.public_key)


def test_verify_cert_rejects_mutated_unknown_cert_type():
    collection, privates = generate_hierarchy(ALICE_ID)
    buried = collection.buried_keys()[0]
    guarded = collection.guarded_keys()[0]

    cert = issue_cert(
        guarded,
        buried,
        privates[buried.key_id],
        ALICE_ID,
        cert_type=CertType.SELF_BINDING,
    )
    cert.cert_type = "generic"

    assert not verify_cert(cert, buried.public_key)
