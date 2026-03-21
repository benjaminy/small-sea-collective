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
# Reference: https://signal.org/docs/specifications/x3dh/

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OneTimePrekey:
    """A single-use DH prekey."""

    prekey_id: bytes
    public_key: bytes


@dataclass
class SignedPrekey:
    """A medium-term DH prekey, signed by the participant's identity key.

    Rotated periodically (Signal recommends weekly).
    """

    prekey_id: bytes
    public_key: bytes
    signature: bytes        # Signed by the participant's DAILY identity key


@dataclass
class PrekeyBundle:
    """The full bundle a sender fetches before initiating a session."""

    participant_id: bytes
    identity_public_key: bytes   # The recipient's DAILY identity key
    signed_prekey: SignedPrekey
    one_time_prekeys: list[OneTimePrekey] = field(default_factory=list)


def generate_one_time_prekeys(n: int) -> list[tuple[OneTimePrekey, bytes]]:
    """Generate n one-time prekeys. Returns list of (prekey, private_key)."""
    raise NotImplementedError


def generate_signed_prekey(
    identity_private_key: bytes,
) -> tuple[SignedPrekey, bytes]:
    """Generate a signed prekey. Returns (signed_prekey, private_key)."""
    raise NotImplementedError


def publish_prekey_bundle(
    bundle: PrekeyBundle,
    storage_upload_fn,
) -> None:
    """Serialize and upload a prekey bundle to cloud storage.

    storage_upload_fn: callable(path: str, data: bytes) -> None
    """
    raise NotImplementedError


def fetch_prekey_bundle(
    participant_id: bytes,
    storage_download_fn,
) -> PrekeyBundle:
    """Download and deserialize a prekey bundle from cloud storage."""
    raise NotImplementedError
