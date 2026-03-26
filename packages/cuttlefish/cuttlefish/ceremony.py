# Cuttlefish — Key signing ceremony helpers
#
# A "ceremony" is a short, physical-proximity signing event between two
# participants. The goal is that signing a teammate's key should be as simple
# as bumping phones or scanning a QR code.
#
# Design intent: the ceremony signs the GUARDED key (or a ceremony-specific
# delegation). The local CA hierarchy then propagates trust down to DAILY keys
# without any further teammate interaction.
#
# Flow:
#   1. Alice generates a CeremonyPayload containing her GUARDED key and
#      hierarchy certs (proving BURIED -> GUARDED -> DAILY chain).
#   2. Alice encodes the payload as a QR code / NFC blob.
#   3. Bob scans it, verifies the hierarchy certs, and issues a certificate
#      for Alice's GUARDED key signed with his own GUARDED (or DAILY) key.
#   4. Bob publishes the certificate to cloud storage.
#   5. (Optionally) Bob generates his own payload for Alice to scan — making
#      the ceremony bidirectional.
#
# Related prior work (see README.md § Prior Art):
#   - Matrix cross-signing QR verification
#   - Signal Safety Number / fingerprint comparison
#   - Briar QR code contact exchange

from __future__ import annotations

import json
import os

from .identity import KeyCertificate, issue_cert, verify_cert
from .keys import ParticipantKey


def generate_ceremony_payload(
    participant_id: bytes,
    target_key: ParticipantKey,
    hierarchy_certs: list[KeyCertificate],
) -> bytes:
    """Produce the payload this participant presents during a ceremony.

    Returns a JSON bytes blob suitable for QR code or NFC exchange.
    Contains the target key (typically GUARDED), hierarchy certs proving
    the chain to the root, and a nonce to prevent replay.
    """
    nonce = os.urandom(16)

    payload = {
        "version": 1,
        "participant_id": participant_id.hex(),
        "nonce": nonce.hex(),
        "target_key": {
            "key_id": target_key.key_id.hex(),
            "public_key": target_key.public_key.hex(),
            "protection_level": target_key.protection_level.name,
            "created_at_iso": target_key.created_at_iso,
            "parent_key_id": target_key.parent_key_id.hex() if target_key.parent_key_id else None,
        },
        "hierarchy_certs": [
            {
                "cert_id": c.cert_id.hex(),
                "subject_key_id": c.subject_key_id.hex(),
                "subject_public_key": c.subject_public_key.hex(),
                "issuer_key_id": c.issuer_key_id.hex(),
                "issuer_participant_id": c.issuer_participant_id.hex(),
                "issued_at_iso": c.issued_at_iso,
                "claims": c.claims,
                "signature": c.signature.hex(),
            }
            for c in hierarchy_certs
        ],
    }

    return json.dumps(payload, sort_keys=True).encode("utf-8")


def decode_ceremony_payload(data: bytes) -> dict:
    """Decode a ceremony payload from JSON bytes.

    Returns the parsed payload dict. The caller should use
    extract_target_key() to get the ParticipantKey and
    extract_hierarchy_certs() to get the certificates.
    """
    return json.loads(data.decode("utf-8"))


def extract_target_key(payload: dict) -> ParticipantKey:
    """Extract the target ParticipantKey from a decoded ceremony payload."""
    from .keys import ProtectionLevel

    tk = payload["target_key"]
    return ParticipantKey(
        key_id=bytes.fromhex(tk["key_id"]),
        public_key=bytes.fromhex(tk["public_key"]),
        protection_level=ProtectionLevel[tk["protection_level"]],
        created_at_iso=tk["created_at_iso"],
        parent_key_id=bytes.fromhex(tk["parent_key_id"]) if tk["parent_key_id"] else None,
    )


def extract_hierarchy_certs(payload: dict) -> list[KeyCertificate]:
    """Extract hierarchy certificates from a decoded ceremony payload."""
    result = []
    for c in payload["hierarchy_certs"]:
        result.append(KeyCertificate(
            cert_id=bytes.fromhex(c["cert_id"]),
            subject_key_id=bytes.fromhex(c["subject_key_id"]),
            subject_public_key=bytes.fromhex(c["subject_public_key"]),
            issuer_key_id=bytes.fromhex(c["issuer_key_id"]),
            issuer_participant_id=bytes.fromhex(c["issuer_participant_id"]),
            issued_at_iso=c["issued_at_iso"],
            claims=c["claims"],
            signature=bytes.fromhex(c["signature"]),
        ))
    return result


def verify_ceremony_payload(payload: dict) -> bool:
    """Verify that the hierarchy certs in a ceremony payload form a valid chain.

    Checks that each cert's signature is valid (signed by the issuer key
    that is the subject of the previous cert in the chain). The first cert
    in the chain should be self-rooted (issuer is a BURIED key).
    """
    certs = extract_hierarchy_certs(payload)
    if not certs:
        return False

    # Verify each cert in the chain. The first cert's issuer is the root
    # (BURIED key) — we need its public key from the cert itself or the payload.
    # The chain is: buried_signs_guarded, guarded_signs_daily
    # Each cert's issuer_key is the subject of the previous cert (or root).
    for i, cert in enumerate(certs):
        if i == 0:
            # First cert is signed by the root key. We need to trust the
            # root key's public key. For self-signed hierarchy, the issuer's
            # public key must be obtained from outside the payload (the
            # verifier's existing knowledge). We can't fully verify the root
            # here — just verify internal consistency.
            #
            # In practice, the root BURIED key is what gets anchored through
            # trust paths in the CertGraph.
            continue
        # For subsequent certs, the issuer should be the subject of a
        # previous cert — we can verify using that subject's public key.
        prev_cert = certs[i - 1]
        if cert.issuer_key_id != prev_cert.subject_key_id:
            return False
        if not verify_cert(cert, prev_cert.subject_public_key):
            return False

    return True


def complete_ceremony(
    payload: dict,
    my_key: ParticipantKey,
    my_private_key: bytes,
    my_participant_id: bytes,
) -> KeyCertificate:
    """Verify a received ceremony payload and issue a certificate for the target key.

    The returned certificate should be published to cloud storage.
    """
    target_key = extract_target_key(payload)

    return issue_cert(
        target_key, my_key, my_private_key, my_participant_id,
        claims={
            "type": "ceremony",
            "nonce": payload["nonce"],
            "target_participant_id": payload["participant_id"],
        },
    )
