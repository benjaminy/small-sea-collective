# Wrasse Trust — Key types and participant key collection
#
# Each participant holds a *collection* of key-pairs that vary along two
# dimensions:
#   - Protection level: how hard the key is to unlock on a given device.
#   - Age: when the key was issued. Older keys accumulate more team certs;
#     newer keys are less likely to have been quietly compromised.
#
# Keys within a participant are arranged in a small CA hierarchy:
#   BURIED (root) -> GUARDED (intermediate) -> DAILY (leaf)
#
# See README.md for full rationale.
#
# Cryptographic primitives
# ========================
# Current implementation uses Ed25519 for all signing operations across
# all protection levels. The hybrid classical + post-quantum approach
# (Ed25519 + ML-DSA-65 for DAILY/GUARDED, Ed25519 + SLH-DSA-128s for
# BURIED) will be layered on later. The API is designed so that callers
# don't need to change when PQ support is added.
#
# Key agreement (X3DH / PQXDH):
#   Classical:        X25519
#   Post-quantum:     ML-KEM-768  (NIST FIPS 203, standardized Aug 2024)
#   Combined per:     Signal PQXDH spec (https://signal.org/docs/specifications/pqxdh/)
#
# Signatures (DAILY and GUARDED keys):
#   Classical:        Ed25519
#   Post-quantum:     ML-DSA-65   (NIST FIPS 204, standardized Aug 2024)
#
# Signatures (BURIED / root keys):
#   Classical:        Ed25519
#   Post-quantum:     SLH-DSA-128s  (NIST FIPS 205, standardized Aug 2024)

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class ProtectionLevel(Enum):
    DAILY = auto()    # Biometric / device PIN — routine message signing
    GUARDED = auto()  # Explicit passphrase — ceremony signing, capability grants
    BURIED = auto()   # Offline long passphrase — root-of-trust operations only


@dataclass
class ParticipantKey:
    """A single key-pair in a participant's key collection.

    public_key is a 32-byte Ed25519 public key (classical only for now).
    key_id is SHA-256(public_key)[:16] — a stable short identifier.
    """

    key_id: bytes          # 16-byte identifier derived from public key
    public_key: bytes      # 32-byte Ed25519 public key
    protection_level: ProtectionLevel
    created_at_iso: str    # ISO-8601 timestamp

    # The key_id of the key in the hierarchy that signed this key, if any.
    # BURIED keys have no parent (they are self-rooted).
    parent_key_id: bytes | None = None


@dataclass
class ParticipantKeyCollection:
    """The full set of keys belonging to one participant."""

    participant_id: bytes
    keys: list[ParticipantKey] = field(default_factory=list)

    def buried_keys(self) -> list[ParticipantKey]:
        return [k for k in self.keys if k.protection_level == ProtectionLevel.BURIED]

    def guarded_keys(self) -> list[ParticipantKey]:
        return [k for k in self.keys if k.protection_level == ProtectionLevel.GUARDED]

    def daily_keys(self) -> list[ParticipantKey]:
        return [k for k in self.keys if k.protection_level == ProtectionLevel.DAILY]

    def current_daily_key(self) -> ParticipantKey:
        """Return the most recently issued DAILY key."""
        daily = self.daily_keys()
        if not daily:
            raise ValueError("No DAILY keys in collection")
        return max(daily, key=lambda k: k.created_at_iso)

    def find_key(self, key_id: bytes) -> ParticipantKey | None:
        """Look up a key by its key_id."""
        for k in self.keys:
            if k.key_id == key_id:
                return k
        return None


def key_id_from_public(public_key: bytes) -> bytes:
    """Derive a 16-byte key_id from a public key."""
    return hashlib.sha256(public_key).digest()[:16]


def generate_key_pair(
    protection_level: ProtectionLevel,
    parent_key_id: bytes | None = None,
) -> tuple[ParticipantKey, bytes]:
    """Generate a new Ed25519 key pair at the given protection level.

    Returns (participant_key, private_key_bytes).
    The private key must be stored securely by the caller.
    """
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    key_id = key_id_from_public(public_bytes)
    now = datetime.now(timezone.utc).isoformat()

    pk = ParticipantKey(
        key_id=key_id,
        public_key=public_bytes,
        protection_level=protection_level,
        created_at_iso=now,
        parent_key_id=parent_key_id,
    )
    return pk, private_bytes


def generate_hierarchy(
    participant_id: bytes,
) -> tuple[ParticipantKeyCollection, dict[bytes, bytes]]:
    """Generate a full BURIED -> GUARDED -> DAILY key hierarchy.

    Returns (collection, private_keys) where private_keys maps key_id -> private_key_bytes.
    """
    buried, buried_priv = generate_key_pair(ProtectionLevel.BURIED)
    guarded, guarded_priv = generate_key_pair(
        ProtectionLevel.GUARDED, parent_key_id=buried.key_id,
    )
    daily, daily_priv = generate_key_pair(
        ProtectionLevel.DAILY, parent_key_id=guarded.key_id,
    )

    collection = ParticipantKeyCollection(
        participant_id=participant_id,
        keys=[buried, guarded, daily],
    )

    privates = {
        buried.key_id: buried_priv,
        guarded.key_id: guarded_priv,
        daily.key_id: daily_priv,
    }

    return collection, privates
