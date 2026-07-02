"""Micro tests for the shared Team Constitution signing envelope."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wrasse_trust.constitution import (
    canonical_constitution_bytes,
    derive_record_id,
    sign_constitution_record,
    verify_constitution_record,
)

TEAMMATE_ID = bytes.fromhex("22" * 16)
DEVICE_KEY_ID = bytes.fromhex("33" * 8)
BERTH_ID = bytes.fromhex("55" * 16)


def _fields(**overrides) -> dict:
    fields = {
        "record_type": "integration_mode_change",
        "author_teammate_id": TEAMMATE_ID.hex(),
        "author_device_key_id": DEVICE_KEY_ID.hex(),
        "created_at": "2026-07-01T00:00:00+00:00",
        "constitution_digest": (b"\x99" * 32).hex(),
        "schema_version": 1,
        "teammate_id": TEAMMATE_ID.hex(),
        "berth_id": BERTH_ID.hex(),
        "mode": "automatic",
    }
    fields.update(overrides)
    return fields


def test_canonical_bytes_are_sorted_and_deterministic():
    fields = _fields()
    shuffled = {k: fields[k] for k in reversed(list(fields))}
    assert canonical_constitution_bytes(fields) == canonical_constitution_bytes(shuffled)


def test_sign_and_verify_round_trip():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    private_key_bytes = private_key.private_bytes_raw()

    canonical = canonical_constitution_bytes(_fields())
    signature = sign_constitution_record(private_key_bytes, canonical)

    assert verify_constitution_record(public_key, canonical, signature)


def test_record_id_is_content_derived_and_stable():
    canonical = canonical_constitution_bytes(_fields())
    record_id = derive_record_id(canonical)

    assert record_id == derive_record_id(canonical)
    assert len(record_id) == 16

    other_canonical = canonical_constitution_bytes(_fields(mode="proposal-only"))
    assert derive_record_id(other_canonical) != record_id


def test_verification_fails_if_any_signed_field_is_tampered():
    private_key = Ed25519PrivateKey.generate()
    private_key_bytes = private_key.private_bytes_raw()
    public_key = private_key.public_key().public_bytes_raw()

    fields = _fields()
    canonical = canonical_constitution_bytes(fields)
    signature = sign_constitution_record(private_key_bytes, canonical)

    for key, tampered_value in [
        ("mode", "proposal-only"),
        ("teammate_id", bytes.fromhex("aa" * 16).hex()),
        ("berth_id", bytes.fromhex("bb" * 16).hex()),
        ("author_teammate_id", bytes.fromhex("cc" * 16).hex()),
        ("constitution_digest", (b"\x00" * 32).hex()),
    ]:
        tampered_fields = _fields(**{key: tampered_value})
        tampered_canonical = canonical_constitution_bytes(tampered_fields)
        assert not verify_constitution_record(public_key, tampered_canonical, signature), key


def test_verification_fails_with_wrong_key():
    private_key = Ed25519PrivateKey.generate()
    other_private_key = Ed25519PrivateKey.generate()
    other_public_key = other_private_key.public_key().public_bytes_raw()

    canonical = canonical_constitution_bytes(_fields())
    signature = sign_constitution_record(private_key.private_bytes_raw(), canonical)

    assert not verify_constitution_record(other_public_key, canonical, signature)
