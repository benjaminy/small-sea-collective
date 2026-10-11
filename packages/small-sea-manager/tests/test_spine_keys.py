"""Micro tests for F6: fixed forged approvals and device-key admission."""

from copy import deepcopy

import pytest

from small_sea_manager.spine import encoding as enc, policy

# These identity-R, zero-S approvals verified with cryptography 46.0.3/OpenSSL
# 3.5.4 before the guard. Message choices are fixed, with no random search here.
FORGERIES = [
    ("0000000000000000000000000000000000000000000000000000000000000000", 1),
    ("0000000000000000000000000000000000000000000000000000000000000080", 0),
    ("0100000000000000000000000000000000000000000000000000000000000000", 0),
    ("0100000000000000000000000000000000000000000000000000000000000080", 0),
    ("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05", 18),
    ("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85", 3),
    ("c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a", 10),
    ("c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa", 8),
    ("ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f", 4),
    ("ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 1),
    ("edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f", 0),
    ("edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 1),
    ("eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f", 0),
    ("eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 0),
]
FORGED_SIG = "01" + "00" * 63
BAD_KEYS = [key for key, _ in FORGERIES] + [
    # y=p+3 is outside the seven-entry small-order table; test both signs.
    ((2**255 - 16) | (sign << 255)).to_bytes(32, "little").hex()
    for sign in (0, 1)
]
SEED = bytes(range(32))
KEY = enc.public_key(SEED)
BASE = "11" * 32


def forged_approval(key, digest):
    body = {"format": enc.FORMAT, "origin": BASE, "kind": "approval",
            "author": key, "parents": [BASE], "payload": {"approves": f"{digest:064x}"}}
    return body, enc.canonical({"body": body, "sig": FORGED_SIG})


@pytest.mark.parametrize("key,digest", FORGERIES)
def test_forged_approval_is_refused(key, digest):
    body, raw = forged_approval(key, digest)
    assert not enc.verify(key, body, FORGED_SIG)
    with pytest.raises(enc.EncodingError, match="bad signature"):
        enc.decode_event(raw)


@pytest.mark.parametrize("key", BAD_KEYS)
def test_genesis_and_add_device_refuse_bad_key(key):
    with pytest.raises(enc.EncodingError, match="disallowed device key"):
        enc.make_genesis("00" * 32, [{"member_id": "alice", "name": "Alice"}],
                         [{"device_key": key, "member_id": "alice", "label": "bad"}])
    state = {"members": {"alice": {}}, "devices": {KEY: {"member_id": "alice"}},
             "policy": enc.POLICY}
    before = deepcopy(state)
    assert policy.apply_ordinary(state, KEY, {
        "op": "add_device", "member_id": "alice", "device_key": key, "label": "bad",
    }) == (None, "disallowed device key")
    assert state == before


@pytest.mark.parametrize("key", BAD_KEYS[14:] + ["00", "zz" * 32, "00" * 33])
def test_verify_refuses_noncanonical_and_malformed_keys(key):
    assert not enc.public_key_allowed(key)
    assert not enc.verify(key, {}, FORGED_SIG)


def test_embedded_forged_approval_is_refused():
    _, approval = forged_approval(FORGERIES[2][0], FORGERIES[2][1])
    proposal = {"format": "spine-proposal-v1", "origin": BASE, "base": BASE,
                "signer": KEY, "resolved_tips": [], "adopted": [], "rejected": []}
    with pytest.raises(enc.EncodingError, match="bad signature"):
        enc.make_event(SEED, BASE, "reconciliation", BASE, {
            "proposal": proposal, "copies": [], "approvals": [approval.hex()],
        })


def test_genuine_signature_and_device_admission():
    assert enc.public_key_allowed(KEY)
    body = {"message": "genuine"}
    assert enc.verify(KEY, body, enc.sign(SEED, body))
    assert not enc.verify(KEY, {"message": "changed"}, enc.sign(SEED, body))
    genesis = enc.make_genesis("00" * 32, [{"member_id": "alice", "name": "Alice"}],
                              [{"device_key": KEY, "member_id": "alice", "label": "laptop"}])
    other = enc.public_key(bytes([42]) * 32)
    result, reason = policy.apply_ordinary(policy.initial_state(genesis), KEY, {
        "op": "add_device", "member_id": "alice", "device_key": other, "label": "phone",
    })
    assert reason is None
    assert result["devices"][other]["member_id"] == "alice"
