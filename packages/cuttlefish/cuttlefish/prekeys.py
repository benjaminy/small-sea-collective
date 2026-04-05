# Cuttlefish — X3DH Prekey bundles
#
# In Signal, a client uploads a batch of one-time prekeys to the server. Any
# other client can fetch one and initiate an encrypted session without the
# recipient being online.
#
# In Small Sea there is no Signal server. Prekey bundles are published to
# cloud storage (via the Hub) and fetched from there. The semantics are
# otherwise the same: each one-time prekey should be consumed at most once.
#
# Identity keys have two components:
#   - X25519 key pair (for DH in the X3DH protocol)
#   - Ed25519 key pair (for signing prekeys)
# These bootstrap-only session identity keys are distinct from the broader
# BURIED/GUARDED/DAILY trust-side identity model, which now lives in the
# separate wrasse-trust package.
# These are separate key pairs. Signal uses XEdDSA to sign with a Curve25519
# key directly; we avoid that complexity by carrying both public keys in the
# bundle.
#
# Reference: https://signal.org/docs/specifications/x3dh/

from __future__ import annotations

import os

from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


@dataclass
class OneTimePrekey:
    """A single-use X25519 DH prekey."""

    prekey_id: bytes       # Unique identifier (random)
    public_key: bytes      # 32-byte X25519 public key


@dataclass
class SignedPrekey:
    """A medium-term X25519 DH prekey, signed by the participant's identity key.

    Rotated periodically (Signal recommends weekly).
    """

    prekey_id: bytes
    public_key: bytes      # 32-byte X25519 public key
    signature: bytes       # Ed25519 signature over public_key


@dataclass
class PrekeyBundle:
    """The full bundle a sender fetches before initiating a session."""

    participant_id: bytes
    identity_dh_public_key: bytes       # X25519 public key (for DH)
    identity_signing_public_key: bytes  # Ed25519 public key (for verifying signed prekey)
    signed_prekey: SignedPrekey
    one_time_prekeys: list[OneTimePrekey] = field(default_factory=list)


@dataclass
class IdentityKeyPair:
    """A participant's identity key material — DH and signing components."""

    dh_public_key: bytes       # 32-byte X25519 public key
    dh_private_key: bytes      # 32-byte X25519 private key
    signing_public_key: bytes  # 32-byte Ed25519 public key
    signing_private_key: bytes # 32-byte Ed25519 private key


def generate_identity_key_pair() -> IdentityKeyPair:
    """Generate a new identity key pair (X25519 for DH + Ed25519 for signing)."""
    dh_priv = X25519PrivateKey.generate()
    dh_priv_bytes = dh_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    dh_pub_bytes = dh_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    sign_priv = Ed25519PrivateKey.generate()
    sign_priv_bytes = sign_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    sign_pub_bytes = sign_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    return IdentityKeyPair(
        dh_public_key=dh_pub_bytes,
        dh_private_key=dh_priv_bytes,
        signing_public_key=sign_pub_bytes,
        signing_private_key=sign_priv_bytes,
    )


def generate_one_time_prekeys(n: int) -> list[tuple[OneTimePrekey, bytes]]:
    """Generate n one-time X25519 prekeys.

    Returns list of (prekey, private_key_bytes).
    Caller must store private keys securely and delete them after use.
    """
    result = []
    for _ in range(n):
        priv = X25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        prekey_id = os.urandom(16)
        result.append((OneTimePrekey(prekey_id=prekey_id, public_key=pub_bytes), priv_bytes))
    return result


def generate_signed_prekey(
    identity_signing_private_key: bytes,
) -> tuple[SignedPrekey, bytes]:
    """Generate a signed X25519 prekey.

    The prekey's public key is signed with the identity Ed25519 key.
    Returns (signed_prekey, x25519_private_key_bytes).
    """
    dh_priv = X25519PrivateKey.generate()
    dh_priv_bytes = dh_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    dh_pub_bytes = dh_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    sign_key = Ed25519PrivateKey.from_private_bytes(identity_signing_private_key)
    signature = sign_key.sign(dh_pub_bytes)

    prekey_id = os.urandom(16)
    signed = SignedPrekey(
        prekey_id=prekey_id,
        public_key=dh_pub_bytes,
        signature=signature,
    )
    return signed, dh_priv_bytes


def build_prekey_bundle(
    participant_id: bytes,
    identity: IdentityKeyPair,
    signed_prekey: SignedPrekey,
    one_time_prekeys: list[OneTimePrekey] | None = None,
) -> PrekeyBundle:
    """Assemble a prekey bundle for publication."""
    return PrekeyBundle(
        participant_id=participant_id,
        identity_dh_public_key=identity.dh_public_key,
        identity_signing_public_key=identity.signing_public_key,
        signed_prekey=signed_prekey,
        one_time_prekeys=one_time_prekeys or [],
    )
