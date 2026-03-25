import os

import pytest
from cryptography.exceptions import InvalidSignature

from cuttlefish.group import (
    GroupMessage,
    create_sender_key,
    group_decrypt,
    group_encrypt,
    process_sender_key_distribution,
)

GROUP_ID = b"test-group-id-00"
ALICE_ID = b"alice-participant"
BOB_ID = b"bob--participant"


def test_create_sender_key():
    record, dist = create_sender_key(GROUP_ID, ALICE_ID)

    assert record.group_id == GROUP_ID
    assert record.sender_participant_id == ALICE_ID
    assert len(record.chain_key) == 32
    assert len(record.chain_id) == 32
    assert len(record.signing_public_key) == 32
    assert len(record.signing_private_key) == 32
    assert record.iteration == 0
    assert record.skipped_message_keys == {}

    assert dist.group_id == GROUP_ID
    assert dist.sender_participant_id == ALICE_ID
    assert dist.chain_key == record.chain_key
    assert dist.sender_chain_id == record.chain_id
    assert dist.signing_public_key == record.signing_public_key
    assert dist.iteration == 0


def test_roundtrip_single_message():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    plaintext = b"hello from alice"
    alice_key, msg = group_encrypt(GROUP_ID, alice_key, plaintext)
    bob_has_alice, decrypted = group_decrypt(msg, bob_has_alice)

    assert decrypted == plaintext


def test_multiple_messages_sequential():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    for i in range(5):
        pt = f"message {i}".encode()
        alice_key, msg = group_encrypt(GROUP_ID, alice_key, pt)
        bob_has_alice, decrypted = group_decrypt(msg, bob_has_alice)
        assert decrypted == pt


def test_out_of_order_decryption():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    # Alice sends 3 messages
    messages = []
    for i in range(3):
        alice_key, msg = group_encrypt(GROUP_ID, alice_key, f"msg-{i}".encode())
        messages.append(msg)

    # Bob decrypts in order: 2, 0, 1
    bob_has_alice, pt = group_decrypt(messages[2], bob_has_alice)
    assert pt == b"msg-2"

    bob_has_alice, pt = group_decrypt(messages[0], bob_has_alice)
    assert pt == b"msg-0"

    bob_has_alice, pt = group_decrypt(messages[1], bob_has_alice)
    assert pt == b"msg-1"


def test_signature_verification_failure():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"original")

    # Tamper with ciphertext
    tampered = GroupMessage(
        sender_participant_id=msg.sender_participant_id,
        sender_chain_id=msg.sender_chain_id,
        iteration=msg.iteration,
        iv=msg.iv,
        ciphertext=msg.ciphertext + b"\x00",
        signature=msg.signature,
    )
    with pytest.raises(InvalidSignature):
        group_decrypt(tampered, bob_has_alice)


def test_wrong_sender_key():
    alice_key, alice_dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_key, bob_dist = create_sender_key(GROUP_ID, BOB_ID)

    # Bob processes his own key (not Alice's)
    bob_has_bob = process_sender_key_distribution(bob_dist)

    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"from alice")

    # Try to decrypt Alice's message with Bob's sender key — signature mismatch
    with pytest.raises(InvalidSignature):
        group_decrypt(msg, bob_has_bob)


def test_cannot_encrypt_with_received_key():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    with pytest.raises(ValueError, match="Cannot encrypt"):
        group_encrypt(GROUP_ID, bob_has_alice, b"should fail")


def test_chain_key_advances():
    alice_key, _ = create_sender_key(GROUP_ID, ALICE_ID)
    original_chain_key = alice_key.chain_key
    original_iteration = alice_key.iteration

    alice_key, _ = group_encrypt(GROUP_ID, alice_key, b"advance")

    assert alice_key.chain_key != original_chain_key
    assert alice_key.iteration == original_iteration + 1


def test_two_members_bidirectional():
    alice_key, alice_dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_key, bob_dist = create_sender_key(GROUP_ID, BOB_ID)

    # Exchange distributions
    alice_has_bob = process_sender_key_distribution(bob_dist)
    bob_has_alice = process_sender_key_distribution(alice_dist)

    # Alice sends to group
    alice_key, msg_a = group_encrypt(GROUP_ID, alice_key, b"from alice")
    bob_has_alice, pt = group_decrypt(msg_a, bob_has_alice)
    assert pt == b"from alice"

    # Bob sends to group
    bob_key, msg_b = group_encrypt(GROUP_ID, bob_key, b"from bob")
    alice_has_bob, pt = group_decrypt(msg_b, alice_has_bob)
    assert pt == b"from bob"

    # Another round
    alice_key, msg_a2 = group_encrypt(GROUP_ID, alice_key, b"alice again")
    bob_has_alice, pt = group_decrypt(msg_a2, bob_has_alice)
    assert pt == b"alice again"


def test_duplicate_message_replay():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    # Alice sends 2 messages, Bob skips to message 1
    alice_key, msg0 = group_encrypt(GROUP_ID, alice_key, b"msg-0")
    alice_key, msg1 = group_encrypt(GROUP_ID, alice_key, b"msg-1")

    bob_has_alice, pt = group_decrypt(msg1, bob_has_alice)
    assert pt == b"msg-1"

    # Decrypt msg0 from skipped keys
    bob_has_alice, pt = group_decrypt(msg0, bob_has_alice)
    assert pt == b"msg-0"

    # Replay msg0 — skipped key was consumed, should fail
    with pytest.raises(ValueError, match="No skipped key"):
        group_decrypt(msg0, bob_has_alice)


def test_large_gap_out_of_order():
    """Skip many messages, then go back and decrypt them all."""
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    messages = []
    for i in range(20):
        alice_key, msg = group_encrypt(GROUP_ID, alice_key, f"msg-{i:02d}".encode())
        messages.append(msg)

    # Bob decrypts the last one first
    bob_has_alice, pt = group_decrypt(messages[19], bob_has_alice)
    assert pt == b"msg-19"
    assert len(bob_has_alice.skipped_message_keys) == 19

    # Now decrypt all the rest in reverse
    for i in range(18, -1, -1):
        bob_has_alice, pt = group_decrypt(messages[i], bob_has_alice)
        assert pt == f"msg-{i:02d}".encode()

    assert len(bob_has_alice.skipped_message_keys) == 0
