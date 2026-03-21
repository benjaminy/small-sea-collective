# Cuttlefish — Certificates and the CA-style key hierarchy
#
# A certificate is a signed statement: "I (signer) vouch that this public key
# belongs to this participant." All certs are published publicly to cloud
# storage so any party can trace a trust chain.
#
# The hierarchy means: BURIED signs GUARDED, GUARDED signs DAILY. A signing
# ceremony with a teammate typically targets the GUARDED key; trust flows down
# to DAILY keys automatically through the local hierarchy.

from __future__ import annotations

from dataclasses import dataclass

from .keys import ParticipantKey


@dataclass
class KeyCertificate:
    """A signed vouching statement from one key to another."""

    cert_id: bytes
    subject_key_id: bytes     # The key being vouched for
    issuer_key_id: bytes      # The key doing the vouching
    issued_at_iso: str
    # Optional: structured claims (e.g. "this is a rotation of key X")
    claims: dict

    signature: bytes          # Signature over the above fields by issuer


@dataclass
class RevocationCertificate:
    """A signed statement that a key should no longer be trusted."""

    cert_id: bytes
    revoked_key_id: bytes
    issuer_key_id: bytes      # Must be GUARDED or BURIED
    issued_at_iso: str
    reason: str               # Free text, e.g. "device stolen"

    signature: bytes


def issue_cert(
    subject_key: ParticipantKey,
    issuer_key: ParticipantKey,
    issuer_private_key: bytes,
    claims: dict | None = None,
) -> KeyCertificate:
    """Sign subject_key with issuer_key, returning a certificate.

    TODO: Define the canonical byte representation that is signed.
    """
    raise NotImplementedError


def verify_cert(cert: KeyCertificate, issuer_public_key: bytes) -> bool:
    """Verify the signature on a certificate."""
    raise NotImplementedError


def issue_revocation(
    revoked_key: ParticipantKey,
    issuer_key: ParticipantKey,
    issuer_private_key: bytes,
    reason: str,
) -> RevocationCertificate:
    raise NotImplementedError


def build_hierarchy_certs(
    buried_key: ParticipantKey,
    buried_private_key: bytes,
    guarded_key: ParticipantKey,
    daily_key: ParticipantKey,
    guarded_private_key: bytes,
) -> tuple[KeyCertificate, KeyCertificate]:
    """Issue the two intra-participant certs that establish the local hierarchy.

    Returns (buried_signs_guarded, guarded_signs_daily).
    When a DAILY key is rotated, only the second cert needs to be reissued.
    """
    raise NotImplementedError
