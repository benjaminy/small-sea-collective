"""Experiments in context/ownership separation with fresh keys and controls."""

from copy import deepcopy
from dataclasses import replace

import pytest
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cuttlefish.group import create_sender_key, process_sender_key_distribution

import publication_prototype as proto


@pytest.fixture
def publication():
    sender, distribution = create_sender_key(b"team", b"alice-device")
    reader = process_sender_key_distribution(distribution)
    expected = proto.context(b"team", b"berth", "objects/one")
    _, message = proto.seal(sender, expected, b"publication")
    return sender, reader, expected, message


def read(message, reader, expected, publisher=b"alice", ownership=None):
    if ownership is None:
        ownership = {b"alice-device": {b"alice"}}
    return proto.open_publication(message, reader, expected_context=expected,
                                  expected_publisher=publisher, ownership=ownership)


@pytest.mark.parametrize("expected", [
    proto.context(b"team", b"berth", "objects/two"),
    proto.context(b"team", b"other-berth", "objects/one"),
    proto.context(b"other-team", b"berth", "objects/one"),
])
def test_context_substitution_with_the_same_available_key(publication, expected):
    _, reader, correct, message = publication
    before = deepcopy(reader)
    with pytest.raises(proto.ContextMismatch):
        read(message, reader, expected)
    assert reader == before
    assert read(message, reader, correct)[1] == b"publication"


def test_wrong_writer_even_with_matching_signed_publisher_label(publication):
    sender, reader, minimal, _ = publication
    # Alice can claim Bob's publisher ID in a larger context and sign it validly.
    # Both candidate context designs still need the ownership lookup.
    for expected in (minimal, proto.encode([minimal.hex(), "claimed-publisher", "bob"])):
        _, message = proto.seal(sender, expected, b"false attribution")
        with pytest.raises(proto.WriterMismatch):
            read(message, reader, expected, publisher=b"bob")
        assert read(message, reader, expected, publisher=b"alice")[1] == b"false attribution"


@pytest.mark.parametrize("field,value", [
    ("sender_device_key_id", b"bob-device"), ("sender_chain_id", b"other-chain"),
    ("iteration", 8), ("iv", b"\0" * 12), ("ciphertext", b"substitute"), ("signature", b"\0" * 64),
])
def test_header_tamper_rejected_before_derivation(publication, monkeypatch, field, value):
    _, reader, expected, original = publication
    before = deepcopy(reader)
    changed = replace(original, message=replace(original.message, **{field: value}))
    calls = []
    derive = proto.candidate_key

    def observed(*args):
        calls.append(True)
        return derive(*args)

    monkeypatch.setattr(proto, "candidate_key", observed)
    with pytest.raises(InvalidSignature):
        read(changed, reader, expected)
    assert calls == []
    assert reader == before
    assert read(original, reader, expected)[1] == b"publication"


def test_valid_signature_cannot_relabel_its_established_device(publication):
    sender, reader, expected, original = publication
    changed = replace(original, message=replace(original.message, sender_device_key_id=b"bob-device"))
    signature = Ed25519PrivateKey.from_private_bytes(sender.signing_private_key).sign(proto.transcript(changed))
    changed = replace(changed, message=replace(changed.message, signature=signature))
    with pytest.raises(ValueError, match="established key record"):
        read(changed, reader, expected, publisher=b"bob", ownership={b"bob-device": {b"bob"}})
    assert read(original, reader, expected)[1] == b"publication"


@pytest.mark.parametrize("position", ["current", "future", "retained"])
def test_missing_conflicting_and_ambiguous_ownership_preserve_state(publication, position):
    sender, reader, expected, message = publication
    if position == "future":
        for _ in range(4):
            sender, message = proto.seal(sender, expected, b"publication")
    elif position == "retained":
        reader, _ = read(message, reader, expected)
    before = deepcopy(reader)
    for mapping, error in [
        (None, proto.OwnershipUnavailable), ({}, proto.OwnershipUnavailable),
        ({b"alice-device": {b"bob"}}, proto.WriterMismatch),
        ({b"alice-device": {b"alice", b"bob"}}, proto.OwnershipAmbiguous),
    ]:
        with pytest.raises(error):
            proto.open_publication(message, reader, expected_context=expected,
                                   expected_publisher=b"alice", ownership=mapping)
        assert reader == before
    updated, plaintext = read(message, reader, expected)
    assert plaintext == b"publication"
    assert reader == before  # Successful verification also returns detached candidate state.
    assert read(message, updated, expected)[1] == b"publication"


def test_two_devices_for_one_owner_do_not_require_expected_device():
    expected = proto.context(b"team", b"berth", "object")
    ownership = {b"device-1": {b"alice"}, b"device-2": {b"alice"}}
    for device in ownership:
        sender, distribution = create_sender_key(b"team", device)
        _, message = proto.seal(sender, expected, device)
        reader = process_sender_key_distribution(distribution)
        assert read(message, reader, expected, ownership=ownership)[1] == device


@pytest.mark.parametrize("position", ["current", "future", "retained"])
def test_aead_failure_after_valid_signature_preserves_candidate_state(publication, position):
    sender, reader, expected, message = publication
    if position == "future":
        for _ in range(4):
            sender, message = proto.seal(sender, expected, b"publication")
    elif position == "retained":
        reader, _ = read(message, reader, expected)
    before = deepcopy(reader)
    broken = replace(message, message=replace(message.message, ciphertext=b"broken authentication tag"))
    signature = Ed25519PrivateKey.from_private_bytes(sender.signing_private_key).sign(proto.transcript(broken))
    broken = replace(broken, message=replace(broken.message, signature=signature))
    with pytest.raises(InvalidTag):
        read(broken, reader, expected)
    assert reader == before
    assert read(message, reader, expected)[1] == b"publication"
