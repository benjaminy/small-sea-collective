import pytest

from cuttlefish.prekeys import (
    build_prekey_bundle,
    generate_identity_key_pair,
    generate_one_time_prekeys,
    generate_signed_prekey,
)
from cuttlefish.ratchet import (
    decrypt,
    encrypt,
    initialize_as_receiver,
    initialize_as_sender,
)
from cuttlefish.x3dh import (
    PrekeyExhaustedException,
    PrekeyExhaustionPolicy,
    x3dh_receive,
    x3dh_send,
)

ALICE_ID = b"alice-id-bytes00"
BOB_ID = b"bob-id-bytes0000"


def _bob_bundle(*, n_one_time=5):
    """Generate Bob's identity, prekeys, and bundle. Returns everything."""
    bob_identity = generate_identity_key_pair()
    bob_spk, bob_spk_priv = generate_signed_prekey(bob_identity.signing_private_key)
    bob_otps = generate_one_time_prekeys(n_one_time)
    bundle = build_prekey_bundle(
        BOB_ID, bob_identity,
        bob_spk, [otp for otp, _ in bob_otps],
    )
    otp_privates = {otp.prekey_id: priv for otp, priv in bob_otps}
    return bob_identity, bob_spk_priv, otp_privates, bundle


def test_x3dh_roundtrip_with_one_time_prekey():
    alice_identity = generate_identity_key_pair()
    bob_identity, bob_spk_priv, otp_privates, bob_bundle = _bob_bundle()

    result = x3dh_send(alice_identity, bob_bundle)

    assert result.initial_message.used_one_time_prekey_id is not None
    otp_priv = otp_privates[result.initial_message.used_one_time_prekey_id]

    bob_shared = x3dh_receive(
        bob_identity, bob_spk_priv, otp_priv,
        result.initial_message,
    )

    assert result.shared_secret == bob_shared


def test_x3dh_roundtrip_without_one_time_prekey():
    alice_identity = generate_identity_key_pair()
    bob_identity, bob_spk_priv, _, bob_bundle = _bob_bundle(n_one_time=0)

    result = x3dh_send(
        alice_identity, bob_bundle,
        exhaustion_policy=PrekeyExhaustionPolicy.DEGRADE,
    )

    assert result.initial_message.used_one_time_prekey_id is None

    bob_shared = x3dh_receive(
        bob_identity, bob_spk_priv, None,
        result.initial_message,
    )

    assert result.shared_secret == bob_shared


def test_strict_policy_raises_on_empty_prekeys():
    alice_identity = generate_identity_key_pair()
    _, _, _, bob_bundle = _bob_bundle(n_one_time=0)

    with pytest.raises(PrekeyExhaustedException):
        x3dh_send(alice_identity, bob_bundle)


def test_tampered_signed_prekey_rejected():
    """Modifying the signed prekey's public key should fail verification."""
    alice_identity = generate_identity_key_pair()
    _, _, _, bob_bundle = _bob_bundle()

    # Tamper with the signed prekey's public key
    from cuttlefish.prekeys import SignedPrekey
    tampered_spk = SignedPrekey(
        prekey_id=bob_bundle.signed_prekey.prekey_id,
        public_key=b"\x00" * 32,  # wrong key
        signature=bob_bundle.signed_prekey.signature,
    )
    bob_bundle.signed_prekey = tampered_spk

    with pytest.raises(Exception):  # InvalidSignature
        x3dh_send(alice_identity, bob_bundle)


def test_different_senders_get_different_secrets():
    alice_identity = generate_identity_key_pair()
    carol_identity = generate_identity_key_pair()
    bob_identity, bob_spk_priv, otp_privates, bob_bundle = _bob_bundle(n_one_time=10)

    result_a = x3dh_send(alice_identity, bob_bundle)
    result_c = x3dh_send(carol_identity, bob_bundle)

    assert result_a.shared_secret != result_c.shared_secret


def test_x3dh_into_double_ratchet():
    """Full end-to-end: X3DH -> Double Ratchet -> encrypted conversation."""
    alice_identity = generate_identity_key_pair()
    bob_identity, bob_spk_priv, otp_privates, bob_bundle = _bob_bundle()

    # Alice performs X3DH
    result = x3dh_send(alice_identity, bob_bundle)
    otp_priv = otp_privates[result.initial_message.used_one_time_prekey_id]

    # Bob performs X3DH
    bob_shared = x3dh_receive(
        bob_identity, bob_spk_priv, otp_priv,
        result.initial_message,
    )

    # Both derive the same shared secret
    assert result.shared_secret == bob_shared

    # Initialize Double Ratchet sessions
    # Alice is the sender: she uses Bob's signed prekey as his initial ratchet key
    alice_ratchet = initialize_as_sender(
        result.shared_secret, result.signed_prekey_public,
    )

    # Bob is the receiver: he uses his signed prekey pair as his initial ratchet key
    from cuttlefish.ratchet import generate_dh_key_pair
    # Bob's signed prekey is X25519 — we need the public key too
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    bob_spk_pub = X25519PrivateKey.from_private_bytes(bob_spk_priv).public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    bob_ratchet = initialize_as_receiver(
        bob_shared, (bob_spk_pub, bob_spk_priv),
    )

    # Alice sends to Bob
    alice_ratchet, msg1 = encrypt(alice_ratchet, b"hello bob, this is encrypted")
    bob_ratchet, pt1 = decrypt(bob_ratchet, msg1)
    assert pt1 == b"hello bob, this is encrypted"

    # Bob replies to Alice
    bob_ratchet, msg2 = encrypt(bob_ratchet, b"got it, alice!")
    alice_ratchet, pt2 = decrypt(alice_ratchet, msg2)
    assert pt2 == b"got it, alice!"

    # Another round
    alice_ratchet, msg3 = encrypt(alice_ratchet, b"forward secrecy in action")
    bob_ratchet, pt3 = decrypt(bob_ratchet, msg3)
    assert pt3 == b"forward secrecy in action"


def test_x3dh_into_ratchet_into_sender_key_distribution():
    """Full stack: X3DH -> Double Ratchet -> distribute Sender Keys."""
    from cuttlefish.group import (
        create_sender_key,
        group_decrypt,
        group_encrypt,
        process_sender_key_distribution,
    )

    alice_identity = generate_identity_key_pair()
    bob_identity, bob_spk_priv, otp_privates, bob_bundle = _bob_bundle()

    GROUP_ID = b"team-friends-000"

    # --- X3DH key agreement ---
    result = x3dh_send(alice_identity, bob_bundle)
    otp_priv = otp_privates[result.initial_message.used_one_time_prekey_id]
    bob_shared = x3dh_receive(bob_identity, bob_spk_priv, otp_priv, result.initial_message)

    # --- Double Ratchet session ---
    alice_ratchet = initialize_as_sender(result.shared_secret, result.signed_prekey_public)

    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    bob_spk_pub = X25519PrivateKey.from_private_bytes(bob_spk_priv).public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )
    bob_ratchet = initialize_as_receiver(bob_shared, (bob_spk_pub, bob_spk_priv))

    # --- Alice creates a sender key and distributes it to Bob via Double Ratchet ---
    alice_sender_key, alice_dist = create_sender_key(GROUP_ID, ALICE_ID)

    # Serialize the distribution message (in practice this would be a proper
    # wire format; here we just concatenate the fields for the test)
    import json
    dist_payload = json.dumps({
        "group_id": alice_dist.group_id.hex(),
        "sender_device_key_id": alice_dist.sender_device_key_id.hex(),
        "sender_chain_id": alice_dist.sender_chain_id.hex(),
        "iteration": alice_dist.iteration,
        "chain_key": alice_dist.chain_key.hex(),
        "signing_public_key": alice_dist.signing_public_key.hex(),
    }).encode()

    # Send via ratchet
    alice_ratchet, ratchet_msg = encrypt(alice_ratchet, dist_payload)

    # Bob receives and decrypts
    bob_ratchet, decrypted_payload = decrypt(bob_ratchet, ratchet_msg)
    dist_data = json.loads(decrypted_payload)

    # Bob reconstructs the distribution message
    from cuttlefish.group import SenderKeyDistributionMessage
    received_dist = SenderKeyDistributionMessage(
        group_id=bytes.fromhex(dist_data["group_id"]),
        sender_device_key_id=bytes.fromhex(dist_data["sender_device_key_id"]),
        sender_chain_id=bytes.fromhex(dist_data["sender_chain_id"]),
        iteration=dist_data["iteration"],
        chain_key=bytes.fromhex(dist_data["chain_key"]),
        signing_public_key=bytes.fromhex(dist_data["signing_public_key"]),
    )

    bob_has_alice = process_sender_key_distribution(received_dist)

    # --- Alice sends a group-encrypted message ---
    alice_sender_key, group_msg = group_encrypt(
        GROUP_ID, alice_sender_key, b"hello team!",
    )

    # Bob decrypts it
    bob_has_alice, plaintext = group_decrypt(group_msg, bob_has_alice)
    assert plaintext == b"hello team!"
