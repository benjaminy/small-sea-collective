# Cuttlefish — Double Ratchet
#
# The Double Ratchet provides per-message forward secrecy and
# post-compromise security (break-in recovery) for 1:1 sessions.
# It is initialized with the shared secret from X3DH (or any other
# key agreement that produces a shared secret + a ratchet public key).
#
# Signal deviation notes:
#   - Uses AES-256-GCM instead of AES-256-CBC + HMAC-SHA256.
#   - Header encryption (Signal spec section 3.5) is deferred.
#   - The async nature of Small Sea means messages may arrive very out of
#     order. We follow Signal's skipped message key mechanism.
#
# Reference: https://signal.org/docs/specifications/doubleratchet/

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# --- KDF helpers ---

# Info string for HKDF, per Signal spec recommendation
_HKDF_INFO = b"CuttlefishDoubleRatchet"


def _kdf_rk(root_key: bytes, dh_output: bytes) -> tuple[bytes, bytes]:
    """Root key KDF: derive new root key and chain key from DH output.

    HKDF(salt=root_key, input=dh_output) -> 64 bytes, split into
    (new_root_key[32], chain_key[32]).
    """
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=root_key,
        info=_HKDF_INFO,
    ).derive(dh_output)
    return derived[:32], derived[32:]


def _kdf_ck(chain_key: bytes) -> tuple[bytes, bytes]:
    """Chain key KDF: derive message key and next chain key.

    message_key = HMAC-SHA256(chain_key, 0x01)
    next_chain_key = HMAC-SHA256(chain_key, 0x02)
    """
    h1 = HMAC(chain_key, hashes.SHA256())
    h1.update(b"\x01")
    message_key = h1.finalize()

    h2 = HMAC(chain_key, hashes.SHA256())
    h2.update(b"\x02")
    next_chain_key = h2.finalize()

    return message_key, next_chain_key


# --- DH helpers ---


def generate_dh_key_pair() -> tuple[bytes, bytes]:
    """Generate an X25519 key pair. Returns (public_key, private_key) as raw bytes."""
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
    return public_bytes, private_bytes


def _dh(private_key_bytes: bytes, public_key_bytes: bytes) -> bytes:
    """X25519 Diffie-Hellman key exchange. Returns 32-byte shared secret."""
    private_key = X25519PrivateKey.from_private_bytes(private_key_bytes)
    public_key = X25519PublicKey.from_public_bytes(public_key_bytes)
    return private_key.exchange(public_key)


# --- Data types ---


@dataclass
class RatchetState:
    """Persistent state for one side of a Double Ratchet session.

    Must be stored securely on-device. Loss = inability to decrypt future
    messages. Leakage = compromise of messages encrypted under this state.
    """

    dh_public_key: bytes              # Our current ratchet public key (32 bytes)
    dh_private_key: bytes             # Our current ratchet private key (32 bytes)
    dh_remote_public_key: bytes | None  # Their current ratchet public key
    root_key: bytes                   # Current root key (32 bytes)
    sending_chain_key: bytes | None   # Current sending chain key
    receiving_chain_key: bytes | None  # Current receiving chain key
    sending_message_index: int = 0    # Next message index for sending
    receiving_message_index: int = 0  # Next expected message index for receiving
    previous_sending_chain_length: int = 0  # Length of previous sending chain
    skipped_keys: dict[tuple[bytes, int], bytes] = field(default_factory=dict)
    # skipped_keys: (ratchet_public_key, message_index) -> message_key


@dataclass
class EncryptedMessage:
    ratchet_public_key: bytes    # Current sender ratchet key (DH ratchet step)
    message_index: int           # Position in the sending chain
    previous_chain_length: int   # Length of previous sending chain
    ciphertext: bytes            # AES-256-GCM ciphertext (includes auth tag)
    iv: bytes                    # 12-byte nonce for AES-256-GCM


# --- Public API ---


def initialize_as_sender(
    shared_secret: bytes, recipient_ratchet_public_key: bytes,
) -> RatchetState:
    """Initialize ratchet state for the session initiator (Alice).

    Alice has just completed X3DH and has the shared secret and Bob's
    signed prekey (used as his initial ratchet public key).
    """
    dh_pub, dh_priv = generate_dh_key_pair()
    dh_output = _dh(dh_priv, recipient_ratchet_public_key)
    root_key, sending_chain_key = _kdf_rk(shared_secret, dh_output)

    return RatchetState(
        dh_public_key=dh_pub,
        dh_private_key=dh_priv,
        dh_remote_public_key=recipient_ratchet_public_key,
        root_key=root_key,
        sending_chain_key=sending_chain_key,
        receiving_chain_key=None,
    )


def initialize_as_receiver(
    shared_secret: bytes, my_ratchet_key_pair: tuple[bytes, bytes],
) -> RatchetState:
    """Initialize ratchet state for the session responder (Bob).

    Bob uses his signed prekey pair as the initial ratchet key pair.
    my_ratchet_key_pair: (public_key, private_key)
    """
    pub, priv = my_ratchet_key_pair
    return RatchetState(
        dh_public_key=pub,
        dh_private_key=priv,
        dh_remote_public_key=None,
        root_key=shared_secret,
        sending_chain_key=None,
        receiving_chain_key=None,
    )


def encrypt(
    state: RatchetState, plaintext: bytes, associated_data: bytes = b"",
) -> tuple[RatchetState, EncryptedMessage]:
    """Encrypt a message, advancing the sending chain.

    Returns (new_state, message). Caller must persist new_state.
    """
    if state.sending_chain_key is None:
        raise ValueError("Cannot encrypt: no sending chain (waiting for first message)")

    message_key, next_chain_key = _kdf_ck(state.sending_chain_key)

    iv = os.urandom(12)
    aesgcm = AESGCM(message_key)
    ciphertext = aesgcm.encrypt(iv, plaintext, associated_data)

    message = EncryptedMessage(
        ratchet_public_key=state.dh_public_key,
        message_index=state.sending_message_index,
        previous_chain_length=state.previous_sending_chain_length,
        ciphertext=ciphertext,
        iv=iv,
    )

    new_state = replace(
        state,
        sending_chain_key=next_chain_key,
        sending_message_index=state.sending_message_index + 1,
    )

    return new_state, message


def decrypt(
    state: RatchetState, message: EncryptedMessage, associated_data: bytes = b"",
) -> tuple[RatchetState, bytes]:
    """Decrypt a message, advancing the receiving chain as needed.

    Returns (new_state, plaintext). Caller must persist new_state.
    Raises on authentication failure.
    """
    # Check skipped keys first
    skip_key = (message.ratchet_public_key, message.message_index)
    if skip_key in state.skipped_keys:
        message_key = state.skipped_keys[skip_key]
        new_skipped = dict(state.skipped_keys)
        del new_skipped[skip_key]
        new_state = replace(state, skipped_keys=new_skipped)
        plaintext = AESGCM(message_key).decrypt(message.iv, message.ciphertext, associated_data)
        return new_state, plaintext

    # DH ratchet step if the sender's ratchet key has changed
    if message.ratchet_public_key != state.dh_remote_public_key:
        state = _perform_dh_ratchet(state, message)

    # Advance receiving chain to the message index, storing skipped keys
    if message.message_index < state.receiving_message_index:
        raise ValueError(
            f"Message index {message.message_index} already consumed "
            f"(current: {state.receiving_message_index})"
        )

    new_skipped = dict(state.skipped_keys)
    chain_key = state.receiving_chain_key
    for i in range(state.receiving_message_index, message.message_index):
        mk, chain_key = _kdf_ck(chain_key)
        new_skipped[(message.ratchet_public_key, i)] = mk

    message_key, next_chain_key = _kdf_ck(chain_key)

    new_state = replace(
        state,
        receiving_chain_key=next_chain_key,
        receiving_message_index=message.message_index + 1,
        skipped_keys=new_skipped,
    )

    plaintext = AESGCM(message_key).decrypt(message.iv, message.ciphertext, associated_data)
    return new_state, plaintext


def _perform_dh_ratchet(state: RatchetState, message: EncryptedMessage) -> RatchetState:
    """Perform a DH ratchet step when receiving a message with a new ratchet key.

    1. Skip any remaining messages in the current receiving chain.
    2. Derive new receiving chain from DH(our_priv, their_new_pub).
    3. Generate new DH pair.
    4. Derive new sending chain from DH(new_priv, their_new_pub).
    """
    new_skipped = dict(state.skipped_keys)

    # Store skipped keys from the current receiving chain
    if state.receiving_chain_key is not None and state.dh_remote_public_key is not None:
        chain_key = state.receiving_chain_key
        for i in range(state.receiving_message_index, message.previous_chain_length):
            mk, chain_key = _kdf_ck(chain_key)
            new_skipped[(state.dh_remote_public_key, i)] = mk

    # DH ratchet step: receiving side
    dh_output = _dh(state.dh_private_key, message.ratchet_public_key)
    root_key, receiving_chain_key = _kdf_rk(state.root_key, dh_output)

    # DH ratchet step: sending side (with new key pair)
    new_pub, new_priv = generate_dh_key_pair()
    dh_output = _dh(new_priv, message.ratchet_public_key)
    root_key, sending_chain_key = _kdf_rk(root_key, dh_output)

    return replace(
        state,
        dh_public_key=new_pub,
        dh_private_key=new_priv,
        dh_remote_public_key=message.ratchet_public_key,
        root_key=root_key,
        sending_chain_key=sending_chain_key,
        receiving_chain_key=receiving_chain_key,
        previous_sending_chain_length=state.sending_message_index,
        sending_message_index=0,
        receiving_message_index=0,
        skipped_keys=new_skipped,
    )
