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
#
# Reference: https://signal.org/docs/specifications/senderkey/

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SenderKeyRecord:
    """A member's current sender chain state. Stored securely on-device."""

    # TODO: define (chain key, signing key, iteration)
    _opaque: dict = field(default_factory=dict)


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
    ciphertext: bytes
    signature: bytes      # Over ciphertext, by sender's signing key


def create_sender_key(group_id: bytes) -> tuple[SenderKeyRecord, SenderKeyDistributionMessage]:
    """Initialize a new sender key chain for this participant in a group.

    Call this when creating a group or after a membership change.
    Returns (local_record, distribution_message_to_send_to_each_member).
    """
    raise NotImplementedError


def process_sender_key_distribution(
    msg: SenderKeyDistributionMessage,
) -> SenderKeyRecord:
    """Process a distribution message received from another group member.

    The returned record should be stored keyed by (group_id, sender_participant_id).
    """
    raise NotImplementedError


def group_encrypt(
    group_id: bytes,
    my_sender_key: SenderKeyRecord,
    plaintext: bytes,
) -> tuple[SenderKeyRecord, GroupMessage]:
    """Encrypt a group message, advancing the sender chain.

    Returns (new_sender_key, message). Caller must persist new_sender_key.
    """
    raise NotImplementedError


def group_decrypt(
    message: GroupMessage,
    sender_key: SenderKeyRecord,
) -> tuple[SenderKeyRecord, bytes]:
    """Decrypt a group message using the stored sender key for that sender.

    Returns (updated_sender_key, plaintext).
    """
    raise NotImplementedError
