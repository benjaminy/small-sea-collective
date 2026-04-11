from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_WELCOME_BUNDLE_INFO = b"SmallSeaWelcomeBundle/v1"
_ENVELOPE_VERSION = 1


def generate_bootstrap_keypair() -> tuple[bytes, bytes]:
    """Generate an X25519 keypair for identity-bootstrap transport."""
    private_key = X25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_bytes, public_bytes


def generate_bootstrap_signing_keypair() -> tuple[bytes, bytes]:
    """Generate an Ed25519 keypair for identity-bootstrap signatures."""
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
    return private_bytes, public_bytes


def _hkdf(shared_secret: bytes, *, salt: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=info,
    ).derive(shared_secret)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def seal_welcome_bundle(
    recipient_public_key: bytes,
    plaintext: bytes,
    *,
    associated_data: bytes = b"",
) -> bytes:
    """Seal a welcome bundle for a recipient X25519 public key."""
    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_public = ephemeral_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    recipient_public = X25519PublicKey.from_public_bytes(recipient_public_key)
    shared_secret = ephemeral_private.exchange(recipient_public)
    nonce = os.urandom(12)
    key = _hkdf(
        shared_secret,
        salt=ephemeral_public + recipient_public_key,
        info=_WELCOME_BUNDLE_INFO,
    )
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, associated_data)
    envelope = {
        "version": _ENVELOPE_VERSION,
        "ephemeral_public_key": _b64(ephemeral_public),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def open_welcome_bundle(
    recipient_private_key: bytes,
    sealed_bundle: bytes,
    *,
    associated_data: bytes = b"",
) -> bytes:
    """Open a welcome bundle previously sealed to the matching key."""
    envelope: dict[str, Any] = json.loads(sealed_bundle.decode("utf-8"))
    if envelope.get("version") != _ENVELOPE_VERSION:
        raise ValueError(f"Unsupported welcome bundle envelope version: {envelope.get('version')}")

    ephemeral_public = _b64d(envelope["ephemeral_public_key"])
    nonce = _b64d(envelope["nonce"])
    ciphertext = _b64d(envelope["ciphertext"])

    recipient_private = X25519PrivateKey.from_private_bytes(recipient_private_key)
    recipient_public = recipient_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    shared_secret = recipient_private.exchange(
        X25519PublicKey.from_public_bytes(ephemeral_public)
    )
    key = _hkdf(
        shared_secret,
        salt=ephemeral_public + recipient_public,
        info=_WELCOME_BUNDLE_INFO,
    )
    return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, associated_data)


def sign_welcome_bundle(signing_private_key: bytes, plaintext: bytes) -> bytes:
    """Sign welcome-bundle plaintext with an Ed25519 signing key."""
    return Ed25519PrivateKey.from_private_bytes(signing_private_key).sign(plaintext)


def verify_welcome_bundle_signature(
    signing_public_key: bytes,
    plaintext: bytes,
    signature: bytes,
) -> bool:
    """Verify an Ed25519 signature over welcome-bundle plaintext."""
    try:
        Ed25519PublicKey.from_public_bytes(signing_public_key).verify(signature, plaintext)
        return True
    except Exception:
        return False
