# Wrasse Trust — Key signing ceremony helpers
#
# A "ceremony" is a short, physical-proximity signing event between two
# participants. The goal is that signing a teammate's key should be as simple
# as bumping phones or scanning a QR code.
#
# Design intent: the ceremony signs the GUARDED key (or a ceremony-specific
# delegation). The local CA hierarchy then propagates trust down to DAILY keys
# without any further teammate interaction.

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
    """Produce the payload this participant presents during a ceremony."""
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
    """Decode a ceremony payload from JSON bytes."""
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
    """Verify that the hierarchy certs in a ceremony payload form a valid chain."""
    certs = extract_hierarchy_certs(payload)
    if not certs:
        return False

    for i, cert in enumerate(certs):
        if i == 0:
            continue
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
    """Verify a received ceremony payload and issue a certificate for the target key."""
    target_key = extract_target_key(payload)

    return issue_cert(
        target_key, my_key, my_private_key, my_participant_id,
        claims={
            "type": "ceremony",
            "nonce": payload["nonce"],
            "target_participant_id": payload["participant_id"],
        },
    )
