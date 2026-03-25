import os

import pytest

from cuttlefish.ratchet import (
    decrypt,
    encrypt,
    generate_dh_key_pair,
    initialize_as_receiver,
    initialize_as_sender,
)


def _setup_session():
    """Set up a Double Ratchet session between Alice (sender) and Bob (receiver).

    Simulates a completed X3DH: both parties share a secret, and Alice knows
    Bob's ratchet public key (his signed prekey).
    """
    shared_secret = os.urandom(32)
    bob_pub, bob_priv = generate_dh_key_pair()

    alice_state = initialize_as_sender(shared_secret, bob_pub)
    bob_state = initialize_as_receiver(shared_secret, (bob_pub, bob_priv))

    return alice_state, bob_state


def test_single_message_roundtrip():
    alice, bob = _setup_session()

    alice, msg = encrypt(alice, b"hello bob")
    bob, plaintext = decrypt(bob, msg)

    assert plaintext == b"hello bob"


def test_multiple_messages_one_direction():
    alice, bob = _setup_session()

    for i in range(5):
        pt = f"message {i}".encode()
        alice, msg = encrypt(alice, pt)
        bob, decrypted = decrypt(bob, msg)
        assert decrypted == pt


def test_bidirectional_conversation():
    alice, bob = _setup_session()

    # Alice -> Bob
    alice, msg = encrypt(alice, b"hi bob")
    bob, pt = decrypt(bob, msg)
    assert pt == b"hi bob"

    # Bob -> Alice
    bob, msg = encrypt(bob, b"hi alice")
    alice, pt = decrypt(alice, msg)
    assert pt == b"hi alice"

    # Alice -> Bob again
    alice, msg = encrypt(alice, b"how are you")
    bob, pt = decrypt(bob, msg)
    assert pt == b"how are you"

    # Bob -> Alice again
    bob, msg = encrypt(bob, b"fine thanks")
    alice, pt = decrypt(alice, msg)
    assert pt == b"fine thanks"


def test_multiple_turns_ratchet_advances():
    """Each direction change triggers a DH ratchet step, producing new keys."""
    alice, bob = _setup_session()

    ratchet_keys_seen = set()

    for turn in range(5):
        # Alice sends
        alice, msg = encrypt(alice, f"a-{turn}".encode())
        ratchet_keys_seen.add(msg.ratchet_public_key)
        bob, pt = decrypt(bob, msg)
        assert pt == f"a-{turn}".encode()

        # Bob sends
        bob, msg = encrypt(bob, f"b-{turn}".encode())
        ratchet_keys_seen.add(msg.ratchet_public_key)
        alice, pt = decrypt(alice, msg)
        assert pt == f"b-{turn}".encode()

    # Each turn generates a new ratchet key for each direction
    # Alice: 1 initial + 4 ratchets = 5, Bob: 5 ratchets = 5 -> 10 unique
    assert len(ratchet_keys_seen) == 10


def test_out_of_order_same_chain():
    """Messages within the same sending chain can arrive out of order."""
    alice, bob = _setup_session()

    # Alice sends 3 messages without Bob responding (same sending chain)
    alice, msg0 = encrypt(alice, b"msg-0")
    alice, msg1 = encrypt(alice, b"msg-1")
    alice, msg2 = encrypt(alice, b"msg-2")

    # Bob decrypts in order: 2, 0, 1
    bob, pt = decrypt(bob, msg2)
    assert pt == b"msg-2"

    bob, pt = decrypt(bob, msg0)
    assert pt == b"msg-0"

    bob, pt = decrypt(bob, msg1)
    assert pt == b"msg-1"


def test_out_of_order_across_ratchet_steps():
    """Messages from a previous ratchet epoch can be decrypted after a step."""
    alice, bob = _setup_session()

    # Alice sends msg0 (epoch 0)
    alice, msg0 = encrypt(alice, b"epoch-0-msg")

    # Bob sends, triggering a ratchet step on both sides
    # But first Bob needs a message from Alice to establish receiving chain
    # Skip msg0 for now...

    # Alice sends msg1 (still epoch 0, same chain)
    alice, msg1 = encrypt(alice, b"epoch-0-msg-1")

    # Bob decrypts msg1 first (skips msg0)
    bob, pt = decrypt(bob, msg1)
    assert pt == b"epoch-0-msg-1"

    # Bob replies (triggers ratchet advance)
    bob, bob_msg = encrypt(bob, b"from bob")
    alice, pt = decrypt(alice, bob_msg)
    assert pt == b"from bob"

    # Now Bob decrypts msg0 (from skipped keys in previous epoch)
    bob, pt = decrypt(bob, msg0)
    assert pt == b"epoch-0-msg"


def test_tampered_ciphertext():
    alice, bob = _setup_session()

    alice, msg = encrypt(alice, b"original")

    # Tamper with the ciphertext
    from cuttlefish.ratchet import EncryptedMessage
    tampered = EncryptedMessage(
        ratchet_public_key=msg.ratchet_public_key,
        message_index=msg.message_index,
        previous_chain_length=msg.previous_chain_length,
        ciphertext=msg.ciphertext + b"\x00",
        iv=msg.iv,
    )

    with pytest.raises(Exception):  # InvalidTag from AES-GCM
        decrypt(bob, tampered)


def test_associated_data():
    """Associated data must match on encrypt and decrypt."""
    alice, bob = _setup_session()

    alice, msg = encrypt(alice, b"secret", associated_data=b"context-1")

    # Correct AD
    bob, pt = decrypt(bob, msg, associated_data=b"context-1")
    assert pt == b"secret"


def test_wrong_associated_data():
    alice, bob = _setup_session()

    alice, msg = encrypt(alice, b"secret", associated_data=b"context-1")

    with pytest.raises(Exception):  # InvalidTag from AES-GCM
        decrypt(bob, msg, associated_data=b"wrong-context")


def test_receiver_cannot_encrypt_before_first_message():
    """Bob can't send until he has received a message from Alice."""
    _, bob = _setup_session()

    with pytest.raises(ValueError, match="Cannot encrypt"):
        encrypt(bob, b"should fail")


def test_duplicate_message_rejected():
    alice, bob = _setup_session()

    alice, msg0 = encrypt(alice, b"msg-0")
    alice, msg1 = encrypt(alice, b"msg-1")

    # Bob decrypts msg1 (skipping msg0)
    bob, _ = decrypt(bob, msg1)

    # Bob decrypts msg0 from skipped keys
    bob, _ = decrypt(bob, msg0)

    # Replay msg0 — key was consumed
    with pytest.raises(ValueError, match="already consumed"):
        decrypt(bob, msg0)


def test_long_conversation():
    """20-turn conversation to stress the ratchet."""
    alice, bob = _setup_session()

    for i in range(20):
        # Alice sends a burst of 3
        msgs = []
        for j in range(3):
            alice, msg = encrypt(alice, f"a-{i}-{j}".encode())
            msgs.append(msg)

        for j, msg in enumerate(msgs):
            bob, pt = decrypt(bob, msg)
            assert pt == f"a-{i}-{j}".encode()

        # Bob replies with 2
        msgs = []
        for j in range(2):
            bob, msg = encrypt(bob, f"b-{i}-{j}".encode())
            msgs.append(msg)

        for j, msg in enumerate(msgs):
            alice, pt = decrypt(alice, msg)
            assert pt == f"b-{i}-{j}".encode()
