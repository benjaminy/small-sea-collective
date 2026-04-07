# Wrasse Trust — Certificates and the CA-style key hierarchy
#
# A certificate is a signed statement: "I (signer) vouch that this public key
# belongs to this participant." All certs are published publicly to cloud
# storage so any party can trace a trust chain.
#
# The hierarchy means: BURIED signs GUARDED, GUARDED signs DAILY. A signing
# ceremony with a teammate typically targets the GUARDED key; trust flows down
# to DAILY keys automatically through the local hierarchy.
#
# Canonical cert bytes for signing:
#   JSON with sorted keys over the cert fields (excluding signature).
#   This is simple, deterministic, and debuggable.

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .keys import ParticipantKey


@dataclass
class KeyCertificate:
    """A signed vouching statement from one key to another."""

    cert_id: bytes
    cert_type: str
    team_id: bytes | None
    subject_key_id: bytes
    subject_public_key: bytes
    issuer_key_id: bytes
    issuer_participant_id: bytes
    issued_at_iso: str
    claims: dict
    signature: bytes


@dataclass
class RevocationCertificate:
    """A signed statement that a key should no longer be trusted."""

    cert_id: bytes
    revoked_key_id: bytes
    issuer_key_id: bytes
    issuer_participant_id: bytes
    issued_at_iso: str
    reason: str
    signature: bytes


def _canonical_cert_bytes(
    cert_type: str,
    team_id: bytes | None,
    subject_key_id: bytes,
    subject_public_key: bytes,
    issuer_key_id: bytes,
    issuer_participant_id: bytes,
    issued_at_iso: str,
    claims: dict,
) -> bytes:
    """Produce the deterministic byte string that is signed for a cert."""
    obj = {
        "cert_type": cert_type,
        "team_id": team_id.hex() if team_id is not None else None,
        "subject_key_id": subject_key_id.hex(),
        "subject_public_key": subject_public_key.hex(),
        "issuer_key_id": issuer_key_id.hex(),
        "issuer_participant_id": issuer_participant_id.hex(),
        "issued_at_iso": issued_at_iso,
        "claims": claims,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_revocation_bytes(
    cert_id: bytes,
    revoked_key_id: bytes,
    issuer_key_id: bytes,
    issuer_participant_id: bytes,
    issued_at_iso: str,
    reason: str,
) -> bytes:
    """Produce the deterministic byte string that is signed for a revocation."""
    obj = {
        "cert_id": cert_id.hex(),
        "revoked_key_id": revoked_key_id.hex(),
        "issuer_key_id": issuer_key_id.hex(),
        "issuer_participant_id": issuer_participant_id.hex(),
        "issued_at_iso": issued_at_iso,
        "reason": reason,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def issue_cert(
    subject_key: ParticipantKey,
    issuer_key: ParticipantKey,
    issuer_private_key: bytes,
    issuer_participant_id: bytes,
    cert_type: str = "generic",
    team_id: bytes | None = None,
    claims: dict | None = None,
) -> KeyCertificate:
    """Sign subject_key with issuer_key, returning a certificate."""
    now = datetime.now(timezone.utc).isoformat()
    claims = claims or {}

    canonical = _canonical_cert_bytes(
        cert_type, team_id, subject_key.key_id, subject_key.public_key,
        issuer_key.key_id, issuer_participant_id, now, claims,
    )
    cert_id = hashlib.sha256(canonical).digest()[:16]

    private_key = Ed25519PrivateKey.from_private_bytes(issuer_private_key)
    signature = private_key.sign(canonical)

    return KeyCertificate(
        cert_id=cert_id,
        cert_type=cert_type,
        team_id=team_id,
        subject_key_id=subject_key.key_id,
        subject_public_key=subject_key.public_key,
        issuer_key_id=issuer_key.key_id,
        issuer_participant_id=issuer_participant_id,
        issued_at_iso=now,
        claims=claims,
        signature=signature,
    )


def verify_cert(cert: KeyCertificate, issuer_public_key: bytes) -> bool:
    """Verify the signature on a certificate. Returns True if valid."""
    canonical = _canonical_cert_bytes(
        cert.cert_type, cert.team_id, cert.subject_key_id, cert.subject_public_key,
        cert.issuer_key_id, cert.issuer_participant_id,
        cert.issued_at_iso, cert.claims,
    )
    expected_cert_id = hashlib.sha256(canonical).digest()[:16]
    if expected_cert_id != cert.cert_id:
        return False
    public_key = Ed25519PublicKey.from_public_bytes(issuer_public_key)
    try:
        public_key.verify(cert.signature, canonical)
        return True
    except InvalidSignature:
        return False


def issue_device_binding_cert(
    subject_key: ParticipantKey,
    issuer_key: ParticipantKey,
    issuer_private_key: bytes,
    team_id: bytes,
    member_id: bytes,
) -> KeyCertificate:
    """Issue a team-scoped device-binding cert for one member's device key."""
    return issue_cert(
        subject_key,
        issuer_key,
        issuer_private_key,
        issuer_participant_id=member_id,
        cert_type="device_binding",
        team_id=team_id,
        claims={"member_id": member_id.hex()},
    )


def verify_device_binding_cert(
    cert: KeyCertificate,
    issuer_public_key: bytes,
    team_id: bytes,
    member_id: bytes,
    subject_public_key: bytes,
) -> bool:
    """Verify a device-binding cert against explicit team/member constraints."""
    if cert.cert_type != "device_binding":
        return False
    if cert.team_id != team_id:
        return False
    if cert.subject_public_key != subject_public_key:
        return False
    if cert.issuer_participant_id != member_id:
        return False
    if cert.claims.get("member_id") != member_id.hex():
        return False
    return verify_cert(cert, issuer_public_key)


def issue_revocation(
    revoked_key: ParticipantKey,
    issuer_key: ParticipantKey,
    issuer_private_key: bytes,
    issuer_participant_id: bytes,
    reason: str,
) -> RevocationCertificate:
    """Issue a revocation certificate for a key."""
    cert_id = os.urandom(16)
    now = datetime.now(timezone.utc).isoformat()

    canonical = _canonical_revocation_bytes(
        cert_id, revoked_key.key_id, issuer_key.key_id,
        issuer_participant_id, now, reason,
    )

    private_key = Ed25519PrivateKey.from_private_bytes(issuer_private_key)
    signature = private_key.sign(canonical)

    return RevocationCertificate(
        cert_id=cert_id,
        revoked_key_id=revoked_key.key_id,
        issuer_key_id=issuer_key.key_id,
        issuer_participant_id=issuer_participant_id,
        issued_at_iso=now,
        reason=reason,
        signature=signature,
    )


def verify_revocation(rev: RevocationCertificate, issuer_public_key: bytes) -> bool:
    """Verify the signature on a revocation certificate."""
    canonical = _canonical_revocation_bytes(
        rev.cert_id, rev.revoked_key_id, rev.issuer_key_id,
        rev.issuer_participant_id, rev.issued_at_iso, rev.reason,
    )
    public_key = Ed25519PublicKey.from_public_bytes(issuer_public_key)
    try:
        public_key.verify(rev.signature, canonical)
        return True
    except InvalidSignature:
        return False


def build_hierarchy_certs(
    buried_key: ParticipantKey,
    buried_private_key: bytes,
    guarded_key: ParticipantKey,
    daily_key: ParticipantKey,
    guarded_private_key: bytes,
    participant_id: bytes,
) -> tuple[KeyCertificate, KeyCertificate]:
    """Issue the two intra-participant certs that establish the local hierarchy.

    Returns (buried_signs_guarded, guarded_signs_daily).
    When a DAILY key is rotated, only the second cert needs to be reissued.
    """
    buried_signs_guarded = issue_cert(
        guarded_key, buried_key, buried_private_key, participant_id,
        cert_type="self_binding",
        claims={"type": "hierarchy", "relationship": "buried_signs_guarded"},
    )
    guarded_signs_daily = issue_cert(
        daily_key, guarded_key, guarded_private_key, participant_id,
        cert_type="self_binding",
        claims={"type": "hierarchy", "relationship": "guarded_signs_daily"},
    )
    return buried_signs_guarded, guarded_signs_daily
