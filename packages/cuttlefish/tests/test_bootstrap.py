import pytest
from cryptography.exceptions import InvalidTag

from cuttlefish import generate_bootstrap_keypair, open_welcome_bundle, seal_welcome_bundle


def test_welcome_bundle_round_trip():
    recipient_private, recipient_public = generate_bootstrap_keypair()
    aad = b"SmallSeaWelcomeBundle/v1|participant=alice|device=phone"
    plaintext = b'{"hello":"world"}'

    sealed = seal_welcome_bundle(recipient_public, plaintext, associated_data=aad)

    assert open_welcome_bundle(recipient_private, sealed, associated_data=aad) == plaintext


def test_welcome_bundle_rejects_wrong_key():
    recipient_private, recipient_public = generate_bootstrap_keypair()
    wrong_private, _wrong_public = generate_bootstrap_keypair()

    sealed = seal_welcome_bundle(recipient_public, b"payload", associated_data=b"ctx")

    with pytest.raises(InvalidTag):
        open_welcome_bundle(wrong_private, sealed, associated_data=b"ctx")


def test_welcome_bundle_rejects_tampering():
    recipient_private, recipient_public = generate_bootstrap_keypair()
    sealed = bytearray(
        seal_welcome_bundle(recipient_public, b"payload", associated_data=b"ctx")
    )
    sealed[-1] ^= 0x01

    with pytest.raises((InvalidTag, ValueError)):
        open_welcome_bundle(recipient_private, bytes(sealed), associated_data=b"ctx")
