# Cuttlefish — Sender Keys group messaging
#
# Sender Keys allow efficient group messaging: each member publishes a sender
# chain; a group message is encrypted once by the sender and is decryptable
# by all current members using the sender's chain state.
#
# Signal deviation notes:
#   - Group membership changes (join, leave, revocation) require a full sender
#     key distribution round. In Signal this is mediated by the server; in
#     Small Sea it requires an async round through cloud storage + notifications.
#     The happy path (no membership change) should be cheap.
#   - We follow Signal's convention that sender key distribution messages are
#     sent as 1:1 X3DH/Ratchet messages to each new member.
#   - We use AES-256-GCM instead of AES-256-CBC + HMAC-SHA256 for simplicity.
#
# Reference: https://signal.org/docs/specifications/senderkey/

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hmac import HMAC


# --- Chain ratchet helpers ---


def _derive_message_key(chain_key: bytes) -> bytes:
    """HMAC-SHA256(chain_key, 0x01) -> 32-byte message key."""
    h = HMAC(chain_key, hashes.SHA256())
    h.update(b"\x01")
    return h.finalize()


def _advance_chain_key(chain_key: bytes) -> bytes:
    """HMAC-SHA256(chain_key, 0x02) -> 32-byte next chain key."""
    h = HMAC(chain_key, hashes.SHA256())
    h.update(b"\x02")
    return h.finalize()


# --- Data types ---


@dataclass
class SenderKeyRecord:
    """A member's current sender chain state. Stored securely on-device."""

    group_id: bytes
    sender_participant_id: bytes
    chain_id: bytes                  # Random ID for this chain generation
    chain_key: bytes                 # 32-byte current chain key
    iteration: int                   # Current position in the chain
    signing_public_key: bytes        # 32-byte Ed25519 public key
    signing_private_key: bytes | None = None  # 32-byte Ed25519 private key (None for received keys)
    skipped_message_keys: dict[int, bytes] = field(default_factory=dict)


@dataclass
class SenderKeyDistributionMessage:
    """Sent 1:1 (via X3DH/Ratchet) to each group member when keys change."""

    group_id: bytes
    sender_participant_id: bytes
    sender_chain_id: bytes
    iteration: int
    chain_key: bytes       # Current chain key
    signing_public_key: bytes


@dataclass
class GroupMessage:
    sender_participant_id: bytes
    sender_chain_id: bytes
    iteration: int
    iv: bytes              # 12-byte nonce for AES-256-GCM
    ciphertext: bytes      # AES-256-GCM ciphertext (includes auth tag)
    signature: bytes       # Ed25519 signature over (iv + ciphertext)


# --- Public API ---


def create_sender_key(
    group_id: bytes, sender_participant_id: bytes,
) -> tuple[SenderKeyRecord, SenderKeyDistributionMessage]:
    """Initialize a new sender key chain for this participant in a group.

    Call this when creating a group or after a membership change.
    Returns (local_record, distribution_message_to_send_to_each_member).
    """
    chain_key = os.urandom(32)
    chain_id = os.urandom(32)

    private_key = Ed25519PrivateKey.generate()
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    record = SenderKeyRecord(
        group_id=group_id,
        sender_participant_id=sender_participant_id,
        chain_id=chain_id,
        chain_key=chain_key,
        iteration=0,
        signing_public_key=public_key_bytes,
        signing_private_key=private_key_bytes,
    )

    distribution = SenderKeyDistributionMessage(
        group_id=group_id,
        sender_participant_id=sender_participant_id,
        sender_chain_id=chain_id,
        iteration=0,
        chain_key=chain_key,
        signing_public_key=public_key_bytes,
    )

    return record, distribution


def process_sender_key_distribution(
    msg: SenderKeyDistributionMessage,
) -> SenderKeyRecord:
    """Process a distribution message received from another group member.

    The returned record should be stored keyed by (group_id, sender_participant_id).
    """
    return SenderKeyRecord(
        group_id=msg.group_id,
        sender_participant_id=msg.sender_participant_id,
        chain_id=msg.sender_chain_id,
        chain_key=msg.chain_key,
        iteration=msg.iteration,
        signing_public_key=msg.signing_public_key,
        signing_private_key=None,
    )


def group_encrypt(
    group_id: bytes,
    my_sender_key: SenderKeyRecord,
    plaintext: bytes,
) -> tuple[SenderKeyRecord, GroupMessage]:
    """Encrypt a group message, advancing the sender chain.

    Returns (new_sender_key, message). Caller must persist new_sender_key.
    """
    if my_sender_key.signing_private_key is None:
        raise ValueError("Cannot encrypt with a received sender key (no private key)")

    message_key = _derive_message_key(my_sender_key.chain_key)
    next_chain_key = _advance_chain_key(my_sender_key.chain_key)

    iv = os.urandom(12)
    aesgcm = AESGCM(message_key)
    ciphertext = aesgcm.encrypt(iv, plaintext, group_id)

    # Sign (iv + ciphertext)
    private_key = Ed25519PrivateKey.from_private_bytes(my_sender_key.signing_private_key)
    signature = private_key.sign(iv + ciphertext)

    message = GroupMessage(
        sender_participant_id=my_sender_key.sender_participant_id,
        sender_chain_id=my_sender_key.chain_id,
        iteration=my_sender_key.iteration,
        iv=iv,
        ciphertext=ciphertext,
        signature=signature,
    )

    new_key = replace(
        my_sender_key,
        chain_key=next_chain_key,
        iteration=my_sender_key.iteration + 1,
    )

    return new_key, message


def group_decrypt(
    message: GroupMessage,
    sender_key: SenderKeyRecord,
) -> tuple[SenderKeyRecord, bytes]:
    """Decrypt a group message using the stored sender key for that sender.

    Returns (updated_sender_key, plaintext).
    Raises cryptography.exceptions.InvalidSignature on signature failure.
    Raises ValueError if the message key cannot be derived.
    """
    # Verify signature first (before any decryption attempt)
    public_key = Ed25519PublicKey.from_public_bytes(sender_key.signing_public_key)
    public_key.verify(message.signature, message.iv + message.ciphertext)

    target_iteration = message.iteration

    if target_iteration < sender_key.iteration:
        # Out-of-order: look up previously skipped key
        message_key = sender_key.skipped_message_keys.get(target_iteration)
        if message_key is None:
            raise ValueError(
                f"No skipped key for iteration {target_iteration} "
                f"(current iteration: {sender_key.iteration})"
            )
        new_skipped = dict(sender_key.skipped_message_keys)
        del new_skipped[target_iteration]
        new_key = replace(sender_key, skipped_message_keys=new_skipped)

    elif target_iteration == sender_key.iteration:
        # Next expected message
        message_key = _derive_message_key(sender_key.chain_key)
        next_chain_key = _advance_chain_key(sender_key.chain_key)
        new_key = replace(
            sender_key,
            chain_key=next_chain_key,
            iteration=sender_key.iteration + 1,
        )

    else:
        # Future message: advance chain, storing skipped keys
        new_skipped = dict(sender_key.skipped_message_keys)
        chain_key = sender_key.chain_key
        for i in range(sender_key.iteration, target_iteration):
            new_skipped[i] = _derive_message_key(chain_key)
            chain_key = _advance_chain_key(chain_key)

        # Now chain_key is at target_iteration; derive the message key
        message_key = _derive_message_key(chain_key)
        next_chain_key = _advance_chain_key(chain_key)
        new_key = replace(
            sender_key,
            chain_key=next_chain_key,
            iteration=target_iteration + 1,
            skipped_message_keys=new_skipped,
        )

    # Decrypt
    aesgcm = AESGCM(message_key)
    plaintext = aesgcm.decrypt(message.iv, message.ciphertext, sender_key.group_id)

    return new_key, plaintext
