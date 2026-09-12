import os
from dataclasses import replace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import cuttlefish.group as group_module
from cuttlefish.group import (
    ContextMismatch,
    GroupMessage,
    SenderHeaderMismatch,
    create_sender_key,
    group_decrypt,
    group_encrypt,
    process_sender_key_distribution,
)

GROUP_ID = b"test-group-id-00"
ALICE_ID = b"alice-participant"
BOB_ID = b"bob--participant"
CONTEXT = b"test-object-context"
OTHER_CONTEXT = b"another-object-context"


def test_create_sender_key():
    record, dist = create_sender_key(GROUP_ID, ALICE_ID)

    assert record.group_id == GROUP_ID
    assert record.sender_device_key_id == ALICE_ID
    assert len(record.chain_key) == 32
    assert len(record.chain_id) == 32
    assert len(record.signing_public_key) == 32
    assert len(record.signing_private_key) == 32
    assert record.iteration == 0
    assert record.skipped_message_keys == {}

    assert dist.group_id == GROUP_ID
    assert dist.sender_device_key_id == ALICE_ID
    assert dist.chain_key == record.chain_key
    assert dist.sender_chain_id == record.chain_id
    assert dist.signing_public_key == record.signing_public_key
    assert dist.iteration == 0


def test_roundtrip_single_message():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    plaintext = b"hello from alice"
    alice_key, msg = group_encrypt(GROUP_ID, alice_key, plaintext, CONTEXT)
    bob_has_alice, decrypted = group_decrypt(msg, bob_has_alice, CONTEXT)

    assert decrypted == plaintext


def test_multiple_messages_sequential():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    for i in range(5):
        pt = f"message {i}".encode()
        alice_key, msg = group_encrypt(GROUP_ID, alice_key, pt, CONTEXT)
        bob_has_alice, decrypted = group_decrypt(msg, bob_has_alice, CONTEXT)
        assert decrypted == pt


def test_out_of_order_decryption():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    # Alice sends 3 messages
    messages = []
    for i in range(3):
        alice_key, msg = group_encrypt(GROUP_ID, alice_key, f"msg-{i}".encode(), CONTEXT)
        messages.append(msg)

    # Bob decrypts in order: 2, 0, 1
    bob_has_alice, pt = group_decrypt(messages[2], bob_has_alice, CONTEXT)
    assert pt == b"msg-2"

    bob_has_alice, pt = group_decrypt(messages[0], bob_has_alice, CONTEXT)
    assert pt == b"msg-0"

    bob_has_alice, pt = group_decrypt(messages[1], bob_has_alice, CONTEXT)
    assert pt == b"msg-1"


def test_signature_verification_failure():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"original", CONTEXT)

    # Tamper with ciphertext
    tampered = GroupMessage(
        sender_device_key_id=msg.sender_device_key_id,
        sender_chain_id=msg.sender_chain_id,
        iteration=msg.iteration,
        context=msg.context,
        iv=msg.iv,
        ciphertext=msg.ciphertext + b"\x00",
        signature=msg.signature,
    )
    with pytest.raises(InvalidSignature):
        group_decrypt(tampered, bob_has_alice, CONTEXT)


def test_wrong_sender_key():
    alice_key, alice_dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_key, bob_dist = create_sender_key(GROUP_ID, BOB_ID)

    # Bob processes his own key (not Alice's)
    bob_has_bob = process_sender_key_distribution(bob_dist)

    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"from alice", CONTEXT)

    # Try to decrypt Alice's message with Bob's sender key — signature mismatch
    with pytest.raises(InvalidSignature):
        group_decrypt(msg, bob_has_bob, CONTEXT)


def test_cannot_encrypt_with_received_key():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    with pytest.raises(ValueError, match="Cannot encrypt"):
        group_encrypt(GROUP_ID, bob_has_alice, b"should fail", CONTEXT)


def test_chain_key_advances():
    alice_key, _ = create_sender_key(GROUP_ID, ALICE_ID)
    original_chain_key = alice_key.chain_key
    original_iteration = alice_key.iteration

    alice_key, _ = group_encrypt(GROUP_ID, alice_key, b"advance", CONTEXT)

    assert alice_key.chain_key != original_chain_key
    assert alice_key.iteration == original_iteration + 1


def test_two_members_bidirectional():
    alice_key, alice_dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_key, bob_dist = create_sender_key(GROUP_ID, BOB_ID)

    # Exchange distributions
    alice_has_bob = process_sender_key_distribution(bob_dist)
    bob_has_alice = process_sender_key_distribution(alice_dist)

    # Alice sends to group
    alice_key, msg_a = group_encrypt(GROUP_ID, alice_key, b"from alice", CONTEXT)
    bob_has_alice, pt = group_decrypt(msg_a, bob_has_alice, CONTEXT)
    assert pt == b"from alice"

    # Bob sends to group
    bob_key, msg_b = group_encrypt(GROUP_ID, bob_key, b"from bob", CONTEXT)
    alice_has_bob, pt = group_decrypt(msg_b, alice_has_bob, CONTEXT)
    assert pt == b"from bob"

    # Another round
    alice_key, msg_a2 = group_encrypt(GROUP_ID, alice_key, b"alice again", CONTEXT)
    bob_has_alice, pt = group_decrypt(msg_a2, bob_has_alice, CONTEXT)
    assert pt == b"alice again"


def test_duplicate_message_replay():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    # Alice sends 2 messages, Bob skips to message 1
    alice_key, msg0 = group_encrypt(GROUP_ID, alice_key, b"msg-0", CONTEXT)
    alice_key, msg1 = group_encrypt(GROUP_ID, alice_key, b"msg-1", CONTEXT)

    bob_has_alice, pt = group_decrypt(msg1, bob_has_alice, CONTEXT)
    assert pt == b"msg-1"

    # Decrypt msg0 from skipped keys
    bob_has_alice, pt = group_decrypt(msg0, bob_has_alice, CONTEXT)
    assert pt == b"msg-0"

    # Replay msg0 — skipped key was consumed, should fail
    with pytest.raises(ValueError, match="No skipped key"):
        group_decrypt(msg0, bob_has_alice, CONTEXT)


def test_large_gap_out_of_order():
    """Skip many messages, then go back and decrypt them all."""
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    messages = []
    for i in range(20):
        alice_key, msg = group_encrypt(GROUP_ID, alice_key, f"msg-{i:02d}".encode(), CONTEXT)
        messages.append(msg)

    # Bob decrypts the last one first
    bob_has_alice, pt = group_decrypt(messages[19], bob_has_alice, CONTEXT)
    assert pt == b"msg-19"
    assert len(bob_has_alice.skipped_message_keys) == 19

    # Now decrypt all the rest in reverse
    for i in range(18, -1, -1):
        bob_has_alice, pt = group_decrypt(messages[i], bob_has_alice, CONTEXT)
        assert pt == f"msg-{i:02d}".encode()

    assert len(bob_has_alice.skipped_message_keys) == 0


# --- Context and header binding ---
#
# The transcript details are tested once here, at the shared boundary. Route
# wiring and failure translation are the Hub's tests.


def test_context_mismatch_is_refused_with_the_key_available():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)

    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"for one object", CONTEXT)

    with pytest.raises(ContextMismatch):
        group_decrypt(msg, bob_has_alice, OTHER_CONTEXT)

    # The refusal consumed nothing: the same bytes still open under their own
    # context.
    _, plaintext = group_decrypt(msg, bob_has_alice, CONTEXT)
    assert plaintext == b"for one object"


def _resigned(signing_private_key, message):
    """Re-sign a mutated header the way the real sender would."""
    associated_data = group_module._associated_data(
        GROUP_ID,
        message.context,
        message.sender_device_key_id,
        message.sender_chain_id,
        message.iteration,
    )
    signature = Ed25519PrivateKey.from_private_bytes(signing_private_key).sign(
        group_module._signature_transcript(
            associated_data, message.iv, message.ciphertext
        )
    )
    return replace(message, signature=signature)


def test_a_valid_signature_cannot_relabel_the_sending_device():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    alice_signing = alice_key.signing_private_key
    bob_has_alice = process_sender_key_distribution(dist)
    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"from alice", CONTEXT)

    # Alice signs a header naming Bob's device and a chain that is not hers.
    # Authenticating a label is not the same as establishing its association.
    for relabelled in (
        replace(msg, sender_device_key_id=BOB_ID),
        replace(msg, sender_chain_id=b"a different chain"),
    ):
        forged = _resigned(alice_signing, relabelled)
        with pytest.raises(SenderHeaderMismatch):
            group_decrypt(forged, bob_has_alice, CONTEXT)


@pytest.mark.parametrize(
    "mutation",
    [
        {"context": OTHER_CONTEXT},
        {"iv": os.urandom(12)},
        {"ciphertext": b"\x00" * 40},
        {"signature": b"\x00" * 64},
        {"iteration": 8},
        {"sender_device_key_id": BOB_ID},
        {"sender_chain_id": b"another chain"},
    ],
)
def test_every_authenticated_field_is_covered_by_the_signature(mutation):
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)
    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"original", CONTEXT)

    with pytest.raises(InvalidSignature):
        group_decrypt(replace(msg, **mutation), bob_has_alice, CONTEXT)

    _, plaintext = group_decrypt(msg, bob_has_alice, CONTEXT)
    assert plaintext == b"original"


def test_an_altered_iteration_is_refused_before_the_chain_is_walked(monkeypatch):
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)
    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"original", CONTEXT)

    derivations = []
    real_derive = group_module._derive_message_key
    monkeypatch.setattr(
        group_module,
        "_derive_message_key",
        lambda chain_key: derivations.append(chain_key) or real_derive(chain_key),
    )

    with pytest.raises(InvalidSignature):
        group_decrypt(replace(msg, iteration=1_000_000), bob_has_alice, CONTEXT)
    assert derivations == []


def test_a_negative_iteration_is_refused_without_deriving():
    alice_key, dist = create_sender_key(GROUP_ID, ALICE_ID)
    bob_has_alice = process_sender_key_distribution(dist)
    alice_key, msg = group_encrypt(GROUP_ID, alice_key, b"original", CONTEXT)

    with pytest.raises(ValueError, match="Invalid iteration"):
        group_decrypt(replace(msg, iteration=-1), bob_has_alice, CONTEXT)
