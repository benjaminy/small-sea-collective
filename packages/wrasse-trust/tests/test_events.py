import hashlib
import json
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wrasse_trust.events import (
    ConstitutionEvent,
    EventInvalidError,
    decode_event,
    encode_event,
    make_event,
    signed_body_bytes,
    verify_event,
)


def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _event():
    return make_event("member_added", {"name": "Ada"}, (), _key().private_bytes_raw())


def test_event_id_is_hash_of_signed_body():
    event = _event()
    body = signed_body_bytes(
        event.event_type, event.payload, event.parents, event.signer_public_key
    )
    assert event.event_id == hashlib.sha256(body).digest()
    assert len(event.event_id) == 32


def test_make_and_verify_roundtrip():
    verify_event(_event())


def test_encode_decode_roundtrip():
    event = _event()
    assert decode_event(encode_event(event)) == event


def test_tampered_payload_fails_bad_id():
    event = _event()
    with pytest.raises(EventInvalidError) as error:
        verify_event(replace(event, payload={"name": "Grace"}))
    assert error.value.code == "bad_id"


def test_tampered_parents_fails_bad_id():
    event = _event()
    with pytest.raises(EventInvalidError) as error:
        verify_event(replace(event, parents=(b"\x01" * 32,)))
    assert error.value.code == "bad_id"


def test_swapped_signer_key_fails():
    event = _event()
    other_key = _key().public_key().public_bytes_raw()
    with pytest.raises(EventInvalidError) as error:
        verify_event(replace(event, signer_public_key=other_key))
    assert error.value.code == "bad_id"


def test_bad_signature_fails():
    event = _event()
    with pytest.raises(EventInvalidError) as error:
        verify_event(replace(event, signature=b"\x00" * 64))
    assert error.value.code == "bad_signature"


def test_duplicate_parents_rejected():
    with pytest.raises(ValueError):
        make_event("test", {}, (b"\x01" * 32, b"\x01" * 32), _key().private_bytes_raw())


def test_parents_are_sorted():
    parents = (b"\x02" * 32, b"\x01" * 32)
    event = make_event("test", {}, parents, _key().private_bytes_raw())
    assert event.parents == tuple(sorted(parents))


def test_decode_rejects_unknown_format():
    obj = json.loads(encode_event(_event()))
    obj["format"] = "v1"
    with pytest.raises(EventInvalidError) as error:
        decode_event(json.dumps(obj).encode())
    assert error.value.code == "bad_format"


def test_decode_rejects_malformed_input():
    valid = json.loads(encode_event(_event()))
    cases = [
        b"not JSON",
        json.dumps({key: value for key, value in valid.items() if key != "id"}).encode(),
        json.dumps({**valid, "unexpected": 1}).encode(),
        json.dumps({**valid, "id": "not-hex"}).encode(),
    ]
    for data in cases:
        with pytest.raises(EventInvalidError) as error:
            decode_event(data)
        assert error.value.code == "bad_format"


def test_signed_body_carries_v0_marker():
    event = _event()
    body = json.loads(
        signed_body_bytes(event.event_type, event.payload, event.parents, event.signer_public_key)
    )
    assert body["format"] == "v0"
