"""Create OpenSSH SSHSIG signatures for git commit bodies."""

import base64
import hashlib
import textwrap

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def _string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def public_key_from_private(private_bytes: bytes) -> str:
    """Return the OpenSSH public key for raw Ed25519 private bytes."""
    key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    return key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode("ascii")


def sign_git_commit(payload: bytes, private_bytes: bytes) -> tuple[str, str]:
    """Return an armored SSHSIG and the matching OpenSSH public key."""
    key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    public_key = public_key_from_private(private_bytes)
    public_blob = base64.b64decode(public_key.split()[1])
    signed = b"SSHSIG" + _string(b"git") + _string(b"") + _string(b"sha512")
    signed += _string(hashlib.sha512(payload).digest())
    signature = _string(b"ssh-ed25519") + _string(key.sign(signed))
    binary = b"SSHSIG" + (1).to_bytes(4, "big") + _string(public_blob)
    binary += _string(b"git") + _string(b"") + _string(b"sha512") + _string(signature)
    armor = "\n".join(textwrap.wrap(base64.b64encode(binary).decode("ascii"), 70))
    return f"-----BEGIN SSH SIGNATURE-----\n{armor}\n-----END SSH SIGNATURE-----\n", public_key
