# Cuttlefish — Extended Triple Diffie-Hellman (X3DH) key agreement
#
# X3DH provides asynchronous session initiation: Alice can establish a
# shared secret with Bob using his published prekey bundle, even if Bob
# is offline. The shared secret bootstraps a Double Ratchet session.
#
# This implements the classical X3DH protocol (X25519 only). The post-
# quantum extension (PQXDH, adding ML-KEM) will be layered on top later.
#
# Signal deviation notes:
#   - Identity keys use separate X25519 (DH) and Ed25519 (signing) key
#     pairs rather than XEdDSA. This avoids implementing the XEdDSA
#     conversion but means bundles carry two identity public keys.
#   - Prekey exhaustion default is STRICT rather than Signal's silent
#     fallback to signed prekey only.
#
# Reference: https://signal.org/docs/specifications/x3dh/

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .prekeys import IdentityKeyPair, PrekeyBundle


class PrekeyExhaustionPolicy(Enum):
    STRICT = auto()   # Fail if no one-time prekeys available (default)
    DEGRADE = auto()  # Fall back to signed prekey only (sacrifices one-time FS)


@dataclass
class X3DHResult:
    """Output of X3DH key agreement (sender side).

    shared_secret: pass to ratchet.initialize_as_sender
    initial_message: send to the recipient alongside the first ratchet message
    signed_prekey_public: the recipient's signed prekey used as initial ratchet
        key — pass to ratchet.initialize_as_sender as recipient_ratchet_public_key
    """

    shared_secret: bytes
    initial_message: X3DHInitialMessage
    signed_prekey_public: bytes


@dataclass
class X3DHInitialMessage:
    """The header a sender attaches to the first encrypted message.

    The recipient uses this to reconstruct the shared secret.
    """

    sender_identity_dh_public_key: bytes   # Sender's X25519 identity public key
    ephemeral_public_key: bytes            # Sender's ephemeral X25519 public key
    used_one_time_prekey_id: bytes | None   # None if no OTP was used


class PrekeyExhaustedException(Exception):
    """Raised when no one-time prekeys are available and policy is STRICT."""


_X3DH_INFO = b"CuttlefishX3DH"
# 32 bytes of 0xFF, prepended to DH outputs per Signal X3DH spec section 2.2
_F = b"\xff" * 32


def _dh(private_key_bytes: bytes, public_key_bytes: bytes) -> bytes:
    """X25519 Diffie-Hellman."""
    priv = X25519PrivateKey.from_private_bytes(private_key_bytes)
    pub = X25519PublicKey.from_public_bytes(public_key_bytes)
    return priv.exchange(pub)


def _kdf(dh_concat: bytes) -> bytes:
    """KDF for X3DH: HKDF-SHA256 over concatenated DH outputs.

    Per Signal spec, input is prepended with 32 bytes of 0xFF.
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"\x00" * 32,
        info=_X3DH_INFO,
    ).derive(_F + dh_concat)


def x3dh_send(
    sender_identity: IdentityKeyPair,
    recipient_bundle: PrekeyBundle,
    exhaustion_policy: PrekeyExhaustionPolicy = PrekeyExhaustionPolicy.STRICT,
) -> X3DHResult:
    """Perform X3DH from the sender's (Alice's) side.

    1. Verify the signed prekey signature.
    2. Generate ephemeral X25519 key pair.
    3. Compute DH1..DH3 (or DH4 if one-time prekey available).
    4. Derive shared secret via HKDF.

    Returns X3DHResult with shared_secret, initial_message, and the signed
    prekey public key (used as the initial ratchet key for the Double Ratchet).

    Raises PrekeyExhaustedException if policy is STRICT and no one-time
    prekeys are available.
    """
    # Verify signed prekey
    signing_pub = Ed25519PublicKey.from_public_bytes(
        recipient_bundle.identity_signing_public_key
    )
    signing_pub.verify(
        recipient_bundle.signed_prekey.signature,
        recipient_bundle.signed_prekey.public_key,
    )

    # Check one-time prekey availability
    used_otp_id = None
    otp_public = None
    if recipient_bundle.one_time_prekeys:
        otp = recipient_bundle.one_time_prekeys[0]
        used_otp_id = otp.prekey_id
        otp_public = otp.public_key
    elif exhaustion_policy == PrekeyExhaustionPolicy.STRICT:
        raise PrekeyExhaustedException(
            "No one-time prekeys available and policy is STRICT"
        )

    # Generate ephemeral key pair
    ek_priv = X25519PrivateKey.generate()
    ek_priv_bytes = ek_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ek_pub_bytes = ek_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    spk_pub = recipient_bundle.signed_prekey.public_key
    ik_b_dh = recipient_bundle.identity_dh_public_key

    # DH1 = DH(IK_A, SPK_B) — sender's identity key, recipient's signed prekey
    dh1 = _dh(sender_identity.dh_private_key, spk_pub)
    # DH2 = DH(EK_A, IK_B) — sender's ephemeral, recipient's identity key
    dh2 = _dh(ek_priv_bytes, ik_b_dh)
    # DH3 = DH(EK_A, SPK_B) — sender's ephemeral, recipient's signed prekey
    dh3 = _dh(ek_priv_bytes, spk_pub)

    dh_concat = dh1 + dh2 + dh3

    # DH4 = DH(EK_A, OPK_B) — if one-time prekey available
    if otp_public is not None:
        dh4 = _dh(ek_priv_bytes, otp_public)
        dh_concat += dh4

    shared_secret = _kdf(dh_concat)

    initial_message = X3DHInitialMessage(
        sender_identity_dh_public_key=sender_identity.dh_public_key,
        ephemeral_public_key=ek_pub_bytes,
        used_one_time_prekey_id=used_otp_id,
    )

    return X3DHResult(
        shared_secret=shared_secret,
        initial_message=initial_message,
        signed_prekey_public=spk_pub,
    )


def x3dh_receive(
    recipient_identity: IdentityKeyPair,
    recipient_signed_prekey_private: bytes,
    recipient_one_time_prekey_private: bytes | None,
    initial_message: X3DHInitialMessage,
) -> bytes:
    """Perform X3DH from the recipient's (Bob's) side. Returns the shared secret.

    The shared secret should be passed to ratchet.initialize_as_receiver with
    the signed prekey pair as the initial ratchet key pair.

    After this call the consumed one-time prekey private key must be deleted.
    """
    ik_a_dh = initial_message.sender_identity_dh_public_key
    ek_a = initial_message.ephemeral_public_key

    # DH1 = DH(SPK_B, IK_A) — same as sender's DH1 by commutativity
    dh1 = _dh(recipient_signed_prekey_private, ik_a_dh)
    # DH2 = DH(IK_B, EK_A)
    dh2 = _dh(recipient_identity.dh_private_key, ek_a)
    # DH3 = DH(SPK_B, EK_A)
    dh3 = _dh(recipient_signed_prekey_private, ek_a)

    dh_concat = dh1 + dh2 + dh3

    # DH4 if one-time prekey was used
    if recipient_one_time_prekey_private is not None:
        dh4 = _dh(recipient_one_time_prekey_private, ek_a)
        dh_concat += dh4

    return _kdf(dh_concat)
