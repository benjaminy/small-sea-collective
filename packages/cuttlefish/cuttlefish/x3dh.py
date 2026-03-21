# Cuttlefish — Post-Quantum Extended Triple Diffie-Hellman (PQXDH) key agreement
#
# PQXDH is Signal's extension of X3DH that adds ML-KEM-1024 alongside X25519.
# The shared secret is derived from both; security holds if either primitive
# is unbroken. We follow the PQXDH spec rather than the original X3DH spec.
#
# Signal shipped PQXDH in 2023: https://signal.org/docs/specifications/pqxdh/
#
# Prekey exhaustion policy:
#   The default is STRICT: if no one-time prekeys are available in the
#   recipient's bundle, key agreement fails. The sender must wait or contact
#   the recipient through another channel to trigger prekey replenishment.
#   The rationale is "secure by default": falling back to the signed prekey
#   only sacrifices one-time forward secrecy.
#
#   Callers may opt in to DEGRADE mode, which falls back to the signed prekey
#   if no one-time prekeys are available — matching Signal's original behavior.
#   This should be used only when availability is more important than the
#   incremental forward secrecy of one-time prekeys.
#
# Signal deviation notes:
#   - We use ML-KEM-768 rather than ML-KEM-1024 for DAILY/GUARDED keys
#     (smaller ciphertexts; still 192-bit post-quantum security). BURIED keys
#     use ML-KEM-1024.
#   - Prekey exhaustion default is STRICT rather than Signal's silent fallback.
#
# Reference: https://signal.org/docs/specifications/pqxdh/

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .prekeys import PrekeyBundle


class PrekeyExhaustionPolicy(Enum):
    STRICT = auto()   # Fail if no one-time prekeys available (default)
    DEGRADE = auto()  # Fall back to signed prekey only (sacrifices one-time FS)


@dataclass
class X3DHSendResult:
    """Output of the sender's X3DH computation."""

    shared_secret: bytes
    ephemeral_public_key: bytes   # Must be included in the initial message
    used_one_time_prekey_id: bytes | None  # None if no OTP was available


@dataclass
class X3DHInitialMessage:
    """The header a sender attaches to the first encrypted message.

    The recipient uses this to reconstruct the shared secret.
    """

    sender_identity_public_key: bytes
    ephemeral_public_key: bytes
    used_one_time_prekey_id: bytes | None


class PrekeyExhaustedException(Exception):
    """Raised when no one-time prekeys are available and policy is STRICT."""


def pqxdh_send(
    sender_identity_private_key: bytes,
    sender_identity_public_key: bytes,
    recipient_bundle: PrekeyBundle,
    exhaustion_policy: PrekeyExhaustionPolicy = PrekeyExhaustionPolicy.STRICT,
) -> X3DHSendResult:
    """Perform PQXDH from the sender's side.

    Returns the shared secret and the initial message header to send.
    The shared secret should be passed directly to ratchet.initialize_as_sender.

    Raises PrekeyExhaustedException if policy is STRICT and no one-time
    prekeys are available in recipient_bundle.
    """
    raise NotImplementedError


def pqxdh_receive(
    recipient_identity_private_key: bytes,
    recipient_signed_prekey_private_key: bytes,
    recipient_one_time_prekey_private_key: bytes | None,
    initial_message: X3DHInitialMessage,
) -> bytes:
    """Perform X3DH from the recipient's side. Returns the shared secret.

    The shared secret should be passed to ratchet.initialize_as_receiver.
    After this call the consumed one-time prekey private key must be deleted
    and the prekey bundle updated in storage.
    """
    raise NotImplementedError
