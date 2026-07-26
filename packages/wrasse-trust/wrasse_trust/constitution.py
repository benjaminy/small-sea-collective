# Wrasse Trust — shared signing envelope for Team Constitution records
#
# This is a transitional helper for the older record shape, not an
# implementation of the event DAG targeted by Documentation/team-constitution.md.
# `identity.py` (key_certificate) and `transport.py`
# (teammate_berth_storage_announcement) each independently reimplement the
# same canonical-JSON-then-sign idiom; this module is the shared version new
# Constitution record types build on instead of writing a fourth copy.
#
# Canonical bytes for signing: JSON with sorted keys over every field the
# caller passes in `signed_fields`. The caller is responsible for including
# only fields that should be signed -- separable payload columns (droppable
# PII content) must never be passed here, only their `*_commitment`.

from __future__ import annotations

import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_constitution_bytes(signed_fields: dict) -> bytes:
    """Deterministic byte string signed for a Team Constitution record.

    `signed_fields` must contain every envelope column except `record_id` and
    `signature`, plus the record type's own signed columns, with binary
    values already hex-encoded by the caller.
    """
    return json.dumps(signed_fields, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_record_id(canonical: bytes) -> bytes:
    """Content-derived record_id, matching the existing `key_certificate.cert_id` convention."""
    return hashlib.sha256(canonical).digest()[:16]


def sign_constitution_record(author_private_key: bytes, canonical: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(author_private_key).sign(canonical)


def verify_constitution_record(author_public_key: bytes, canonical: bytes, signature: bytes) -> bool:
    public_key = Ed25519PublicKey.from_public_bytes(author_public_key)
    try:
        public_key.verify(signature, canonical)
        return True
    except InvalidSignature:
        return False
