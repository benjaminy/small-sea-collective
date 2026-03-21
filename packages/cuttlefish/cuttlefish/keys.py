# Cuttlefish — Key types and participant key collection
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
# Cuttlefish uses a hybrid classical + post-quantum approach throughout, so
# that security holds as long as *either* primitive is unbroken.
#
# Key agreement (X3DH / PQXDH):
#   Classical:        X25519
#   Post-quantum:     ML-KEM-768  (NIST FIPS 203, standardized Aug 2024)
#   Combined per:     Signal PQXDH spec (https://signal.org/docs/specifications/pqxdh/)
#
# Signatures (DAILY and GUARDED keys):
#   Classical:        Ed25519
#   Post-quantum:     ML-DSA-65   (NIST FIPS 204, standardized Aug 2024)
#   Rationale:        Compact enough for regular use; widely reviewed.
#
# Signatures (BURIED / root keys):
#   Classical:        Ed25519
#   Post-quantum:     SLH-DSA-128s  (NIST FIPS 205, standardized Aug 2024)
#   Rationale:        Hash-based; security reduces to collision resistance only.
#                     Larger signatures (~8 KB) are fine for rare offline use.
#                     "Harvest now, decrypt later" attacks are most dangerous
#                     for long-lived root keys, making PQC most important here.
#
# All of the above are available in the `cryptography` library >= 44.0.

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class ProtectionLevel(Enum):
    DAILY = auto()    # Biometric / device PIN — routine message signing
    GUARDED = auto()  # Explicit passphrase — ceremony signing, capability grants
    BURIED = auto()   # Offline long passphrase — root-of-trust operations only


@dataclass
class ParticipantKey:
    """A single key-pair in a participant's key collection."""

    key_id: bytes          # Stable identifier (e.g. hash of public key)
    public_key: bytes      # Serialized public key
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
        raise NotImplementedError

    def guarded_keys(self) -> list[ParticipantKey]:
        raise NotImplementedError

    def daily_keys(self) -> list[ParticipantKey]:
        raise NotImplementedError

    def current_daily_key(self) -> ParticipantKey:
        """Return the most recently issued DAILY key."""
        raise NotImplementedError


def generate_key_pair(protection_level: ProtectionLevel) -> ParticipantKey:
    """Generate a new key-pair at the given protection level.

    Each key contains two sub-pairs: one classical and one post-quantum.
    The specific algorithms depend on the protection level:
      DAILY / GUARDED:  X25519 + ML-KEM-768 (DH), Ed25519 + ML-DSA-65 (signing)
      BURIED:           X25519 + ML-KEM-768 (DH), Ed25519 + SLH-DSA-128s (signing)
    """
    raise NotImplementedError


def serialize_public_key_collection(collection: ParticipantKeyCollection) -> bytes:
    """Serialize a key collection for publication to cloud storage."""
    raise NotImplementedError


def deserialize_public_key_collection(data: bytes) -> ParticipantKeyCollection:
    raise NotImplementedError
