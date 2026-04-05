"""Micro tests for keys, identity, ceremony, and trust modules."""

from wrasse_trust.ceremony import (
    complete_ceremony,
    decode_ceremony_payload,
    extract_hierarchy_certs,
    extract_target_key,
    generate_ceremony_payload,
    verify_ceremony_payload,
)
from wrasse_trust.identity import (
    build_hierarchy_certs,
    issue_cert,
    issue_revocation,
    verify_cert,
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
    assert verify_cert(cross_cert, bob_guarded.public_key)
    assert cross_cert.claims["type"] == "ceremony"


def test_direct_trust_path():
    alice_coll, _ = generate_hierarchy(ALICE_ID)
    bob_coll, bob_privs = generate_hierarchy(BOB_ID)

    alice_guarded = alice_coll.guarded_keys()[0]
    bob_guarded = bob_coll.guarded_keys()[0]

    cert = issue_cert(
        alice_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
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
    )
    cert_bc = issue_cert(
        carol_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
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
    )
    cert_bc = issue_cert(
        carol_guarded, bob_guarded, bob_privs[bob_guarded.key_id], BOB_ID,
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
    )
    cert_ca = issue_cert(
        alice_guarded, carol_guarded, carol_privs[carol_guarded.key_id], CAROL_ID,
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
