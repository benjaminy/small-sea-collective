# Cuttlefish — Double Ratchet
#
# The Double Ratchet provides per-message forward secrecy and
# post-compromise security (break-in recovery) for 1:1 sessions.
# It is initialized with the shared secret from X3DH.
#
# Signal deviation notes:
#   - None yet. This should be a faithful implementation.
#   - The async nature of Small Sea means messages may arrive very out of
#     order. Signal's ratchet handles this via "skipped message keys"; we
#     follow the same mechanism but the storage and expiry policy for skipped
#     keys will need careful thought.
#
# Reference: https://signal.org/docs/specifications/doubleratchet/

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RatchetState:
    """Persistent state for one side of a Double Ratchet session.

    Must be stored securely on-device. Loss = inability to decrypt future
    messages. Leakage = compromise of messages encrypted under this state.
    """

    # TODO: define full state (root key, chain keys, ratchet key pair, etc.)
    _opaque: dict = field(default_factory=dict)


@dataclass
class EncryptedMessage:
    ratchet_public_key: bytes    # Current sender ratchet key (DH ratchet step)
    message_index: int           # Position in the sending chain
    previous_chain_length: int   # Length of previous sending chain
    ciphertext: bytes
    # TODO: authenticated header encryption (Signal spec section 3.5)


def initialize_as_sender(shared_secret: bytes, recipient_ratchet_public_key: bytes) -> RatchetState:
    """Initialize ratchet state for the session initiator."""
    raise NotImplementedError


def initialize_as_receiver(shared_secret: bytes, my_ratchet_key_pair: tuple[bytes, bytes]) -> RatchetState:
    """Initialize ratchet state for the session responder.

    my_ratchet_key_pair: (public_key, private_key)
    """
    raise NotImplementedError


def encrypt(state: RatchetState, plaintext: bytes, associated_data: bytes = b"") -> tuple[RatchetState, EncryptedMessage]:
    """Encrypt a message, advancing the sending chain.

    Returns (new_state, message). Caller must persist new_state.
    """
    raise NotImplementedError


def decrypt(state: RatchetState, message: EncryptedMessage, associated_data: bytes = b"") -> tuple[RatchetState, bytes]:
    """Decrypt a message, advancing the receiving chain as needed.

    Returns (new_state, plaintext). Caller must persist new_state.
    Raises on authentication failure.
    """
    raise NotImplementedError
