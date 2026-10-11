"""Micro tests for spine canonical encoding, strict decoding, IDs, and signatures."""

import hashlib
import json

import pytest

from small_sea_manager.spine import encoding as enc

GENESIS_BYTES = b'{"devices":[{"device_key":"8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c","label":"laptop","member_id":"alice"},{"device_key":"8139770ea87d175f56a35466c34c7ecccb8d8a91b4ee37a25df60f5b8fc9b394","label":"laptop","member_id":"bob"},{"device_key":"ed4928c628d1c2c6eae90338905995612959273a5c63f93636c14614ac8737d1","label":"laptop","member_id":"carol"}],"format":"spine-v1","kind":"genesis","members":[{"member_id":"alice","name":"Alice"},{"member_id":"bob","name":"Bob"},{"member_id":"carol","name":"Carol"}],"nonce":"0000000000000000000000000000000000000000000000000000000000000000","policy":{"name":"spine-demo-1","settings":{"reconcile_quorum":2}}}'
GENESIS_ID = "8c44662f111fd29795aaf4082ac1a36f127bc191cdc83aae248b7333e7b8c5a1"
EVENT_BYTES = b'{"body":{"author":"8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c","format":"spine-v1","kind":"ordinary","origin":"8c44662f111fd29795aaf4082ac1a36f127bc191cdc83aae248b7333e7b8c5a1","parents":["8c44662f111fd29795aaf4082ac1a36f127bc191cdc83aae248b7333e7b8c5a1"],"payload":{"member_id":"bob","op":"remove_member"}},"sig":"e62f9607d3603ab8b65d29ead545c9457602f9bc12b93976ca814ebd354e2013f22f6422321125e6c2f812d5aadf7e32b9c7814f78dd7efc24d363a5264b1a04"}'
EVENT_ID = "e388a8df9303dd22222bbd21933b504ba7d4042ba1400dda356f9554480bd13a"
EVENT_SIG = "e62f9607d3603ab8b65d29ead545c9457602f9bc12b93976ca814ebd354e2013f22f6422321125e6c2f812d5aadf7e32b9c7814f78dd7efc24d363a5264b1a04"
ALICE_KEY = "8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c"

SEED_A, SEED_B, SEED_C = bytes([1]) * 32, bytes([2]) * 32, bytes([3]) * 32
ID1, ID2, ID3 = "11" * 32, "22" * 32, "33" * 32


def genesis(**changes):
    g = json.loads(GENESIS_BYTES)
    g.update(changes)
    return enc.canonical(g)


def event_env(**body_changes):
    env = json.loads(EVENT_BYTES)
    env["body"].update(body_changes)
    return env


def resign(env):
    env["sig"] = enc.sign(SEED_A, env["body"])
    return enc.canonical(env)


def proposal(**changes):
    c = {
        "format": "spine-proposal-v1", "origin": GENESIS_ID, "base": GENESIS_ID,
        "signer": ALICE_KEY, "resolved_tips": [ID1, ID2], "adopted": [ID1], "rejected": [ID2],
    }
    c.update(changes)
    return c


def test_vector_genesis():
    assert enc.make_genesis("00" * 32, [
        {"member_id": "alice", "name": "Alice"},
        {"member_id": "bob", "name": "Bob"},
        {"member_id": "carol", "name": "Carol"},
    ], [
        {"device_key": enc.public_key(SEED_A), "member_id": "alice", "label": "laptop"},
        {"device_key": enc.public_key(SEED_B), "member_id": "bob", "label": "laptop"},
        {"device_key": enc.public_key(SEED_C), "member_id": "carol", "label": "laptop"},
    ]) == GENESIS_BYTES
    assert enc.decode_genesis(GENESIS_BYTES)["id"] == GENESIS_ID
    assert enc.public_key(SEED_A) == ALICE_KEY


def test_vector_event():
    data = enc.make_event(
        SEED_A, GENESIS_ID, "ordinary", GENESIS_ID,
        {"op": "remove_member", "member_id": "bob"},
    )
    assert data == EVENT_BYTES
    decoded = enc.decode_event(EVENT_BYTES)
    assert decoded["id"] == EVENT_ID
    assert decoded["sig"] == EVENT_SIG


def test_vector_proposal_digest():
    assert enc.proposal_digest(proposal()) == (
        "1e97200b878821ff56ef07adcba1297118592406881626af117f852dbd1734b5"
    )


def test_id_changes_with_body():
    changed = event_env(payload={"op": "remove_member", "member_id": "carol"})
    assert enc.object_id(changed["body"]) != EVENT_ID


def test_decoded_id_is_the_hash_of_the_body_alone():
    # Our signer is deterministic, but a key holder can use another nonce.
    # IDs depend only on the body, regardless of the signature bytes.
    env = json.loads(EVENT_BYTES)
    assert hashlib.sha256(b"spine-v1 id\0" + enc.canonical(env["body"])).hexdigest() == EVENT_ID
    assert enc.decode_event(EVENT_BYTES)["id"] == EVENT_ID


def test_tampered_signature_fails():
    env = json.loads(EVENT_BYTES)
    env["sig"] = ("0" if env["sig"][0] != "0" else "1") + env["sig"][1:]
    with pytest.raises(enc.EncodingError, match="signature"):
        enc.decode_event(enc.canonical(env))


def test_tampered_body_fails_signature():
    env = event_env(payload={"op": "remove_member", "member_id": "carol"})
    env["sig"] = json.loads(EVENT_BYTES)["sig"]
    with pytest.raises(enc.EncodingError, match="signature"):
        enc.decode_event(enc.canonical(env))


def test_wrong_author_key_fails():
    env = event_env(author=enc.public_key(SEED_B))
    with pytest.raises(enc.EncodingError, match="signature"):
        enc.decode_event(enc.canonical(env))


@pytest.mark.parametrize("value", [1.5, True, None, -1, {"a": 1.0}, [None], {1: "x"}])
def test_canonical_rejects_disallowed_values(value):
    with pytest.raises(enc.EncodingError):
        enc.canonical(value)


def test_canonical_bytes_literal():
    assert enc.canonical({"b": [1, "é"], "a": 0}) == b'{"a":0,"b":[1,"\\u00e9"]}'


@pytest.mark.parametrize("data", [
    b'{"a":1,"a":2}',
    b'{"a":1.0}',
    b'{"a":1e2}',
    b'{"a":true}',
    b'{"a":null}',
    b'{"a":NaN}',
    b'{"a":-1}',
    b'{"a": 1}',
    b'{"b":1,"a":2}',
    b'{"a":"\xc3\xa9"}',
    b'{"a":"\\u00E9"}',
    b'[1,]',
])
def test_decode_canonical_rejects(data):
    with pytest.raises(enc.EncodingError):
        enc.decode_canonical(data)


def test_decode_canonical_accepts_canonical():
    assert enc.decode_canonical(b'{"a":[0,"x"],"b":{}}') == {"a": [0, "x"], "b": {}}


def test_genesis_rejects_extra_and_missing_keys():
    g = json.loads(GENESIS_BYTES)
    g["extra"] = "x"
    with pytest.raises(enc.EncodingError):
        enc.decode_genesis(enc.canonical(g))
    del g["extra"], g["nonce"]
    with pytest.raises(enc.EncodingError):
        enc.decode_genesis(enc.canonical(g))


def test_genesis_rejects_wrong_policy_settings():
    for policy in (
        {"name": "spine-demo-1", "settings": {"reconcile_quorum": 3}},
        {"name": "other", "settings": {"reconcile_quorum": 2}},
        {"name": "spine-demo-1", "settings": {"reconcile_quorum": 2, "x": 1}},
    ):
        with pytest.raises(enc.EncodingError):
            enc.decode_genesis(genesis(policy=policy))


def test_genesis_rejects_duplicate_device_and_member_and_unlisted_member():
    g = json.loads(GENESIS_BYTES)
    with pytest.raises(enc.EncodingError, match="duplicate"):
        enc.decode_genesis(genesis(devices=g["devices"] + [g["devices"][0]]))
    with pytest.raises(enc.EncodingError, match="duplicate"):
        enc.decode_genesis(genesis(members=g["members"] + [g["members"][0]]))
    stray = {"device_key": "ab" * 32, "member_id": "dave", "label": "x"}
    with pytest.raises(enc.EncodingError, match="unlisted"):
        enc.decode_genesis(genesis(devices=g["devices"] + [stray]))


def test_genesis_rejects_wrong_types_and_uppercase_hex():
    with pytest.raises(enc.EncodingError):
        enc.decode_genesis(genesis(nonce="00" * 31))
    with pytest.raises(enc.EncodingError):
        enc.decode_genesis(genesis(nonce="AB" * 32))
    with pytest.raises(enc.EncodingError):
        enc.decode_genesis(genesis(members="alice"))
    with pytest.raises(enc.EncodingError):
        enc.decode_genesis(genesis(format="spine-v2"))


def test_event_rejects_unknown_and_missing_keys():
    env = event_env(extra="x")
    with pytest.raises(enc.EncodingError):
        enc.decode_event(resign(env))
    env = event_env()
    del env["body"]["origin"]
    with pytest.raises(enc.EncodingError):
        enc.decode_event(resign(env))
    env = json.loads(EVENT_BYTES)
    env["extra"] = 1
    with pytest.raises(enc.EncodingError):
        enc.decode_event(enc.canonical(env))


def test_event_rejects_bad_parents():
    for parents in ([], [GENESIS_ID, ID1], GENESIS_ID, [5]):
        with pytest.raises(enc.EncodingError):
            enc.decode_event(resign(event_env(parents=parents)))


def test_event_rejects_bad_kind_op_and_payload_keys():
    for changes in (
        {"kind": "bogus"},
        {"payload": {"op": "bogus", "member_id": "bob"}},
        {"payload": {"op": "remove_member"}},
        {"payload": {"op": "remove_member", "member_id": "bob", "x": "y"}},
        {"payload": {"op": "remove_member", "member_id": 7}},
        {"payload": {"op": "add_device", "member_id": "bob"}},
        {"kind": "approval"},
    ):
        with pytest.raises(enc.EncodingError):
            enc.decode_event(resign(event_env(**changes)))


def test_event_rejects_noncanonical_bytes():
    with pytest.raises(enc.EncodingError):
        enc.decode_event(EVENT_BYTES.replace(b'"sig":', b'"sig": '))
    env = json.loads(EVENT_BYTES)
    with pytest.raises(enc.EncodingError):
        enc.decode_event(json.dumps(env).encode())


def test_add_device_and_approval_events_decode():
    add = enc.make_event(SEED_B, GENESIS_ID, "ordinary", GENESIS_ID, {
        "op": "add_device", "member_id": "bob", "device_key": "ab" * 32, "label": "phone"})
    assert enc.decode_event(add)["body"]["payload"]["label"] == "phone"
    d = enc.proposal_digest(proposal())
    appr = enc.make_event(SEED_C, GENESIS_ID, "approval", GENESIS_ID, {"approves": d})
    assert enc.decode_event(appr)["body"]["kind"] == "approval"


def test_proposal_rejects_unsorted_and_extra_keys():
    with pytest.raises(enc.EncodingError):
        enc.decode_proposal(enc.canonical(proposal(resolved_tips=[ID2, ID1])))
    with pytest.raises(enc.EncodingError):
        enc.decode_proposal(enc.canonical(proposal(rejected=[ID3, ID1])))
    with pytest.raises(enc.EncodingError):
        enc.decode_proposal(enc.canonical(proposal(extra=[])))
    # adopted is in replay order, so any order is accepted
    got = enc.decode_proposal(enc.canonical(proposal(adopted=[ID3, ID1])))
    assert got["adopted"] == [ID3, ID1]


def _reconciliation(copies, approvals, **c_changes):
    c = proposal(**c_changes)
    payload = enc.make_reconciliation_payload(c, copies, approvals)
    return enc.make_event(SEED_A, GENESIS_ID, "reconciliation", GENESIS_ID, payload)


def _x_and_approval():
    x = EVENT_BYTES
    d = enc.proposal_digest(proposal())
    return x, enc.make_event(SEED_C, GENESIS_ID, "approval", GENESIS_ID, {"approves": d})


def test_reconciliation_decodes_with_copies_and_approvals_sorted_by_id():
    x, u = _x_and_approval()
    v = enc.make_event(SEED_A, GENESIS_ID, "approval", GENESIS_ID, {"approves": "ab" * 32})
    r = enc.decode_event(_reconciliation([x], [u, v]))
    assert [c["id"] for c in r["copies"]] == [EVENT_ID]
    ids = [a["id"] for a in r["approvals"]]
    assert ids == sorted(ids) and len(ids) == 2
    assert r["body"]["payload"]["proposal"]["signer"] == ALICE_KEY


def test_reconciliation_rejects_unsorted_duplicate_and_bad_embedded():
    x, u = _x_and_approval()
    v = enc.make_event(SEED_A, GENESIS_ID, "approval", GENESIS_ID, {"approves": "ab" * 32})
    c = proposal()
    ordered = enc.make_reconciliation_payload(c, [x], [u, v])
    for payload in (
        {**ordered, "approvals": ordered["approvals"][::-1]},
        {**ordered, "approvals": [ordered["approvals"][0]] * 2},
        {**ordered, "copies": [x.hex()[:-2] + "00"]},
        {**ordered, "approvals": [x.hex()]},  # an ordinary event is not an approval
        {**ordered, "copies": ["zz"]},
    ):
        with pytest.raises(enc.EncodingError):
            enc.make_event(SEED_A, GENESIS_ID, "reconciliation", GENESIS_ID, payload)


def test_reconciliation_rejects_unsorted_proposal_lists():
    for changes in ({"resolved_tips": [ID2, ID1]}, {"rejected": [ID3, ID2]}):
        with pytest.raises(enc.EncodingError, match="not sorted"):
            _reconciliation([], [], **changes)


def test_reconciliation_rejects_copy_with_other_origin():
    other = enc.make_event(
        SEED_A, ID3, "ordinary", ID3, {"op": "remove_member", "member_id": "bob"})
    with pytest.raises(enc.EncodingError, match="origin"):
        _reconciliation([other], [])


def test_reconciliation_rejects_proposal_with_other_origin():
    with pytest.raises(enc.EncodingError, match="origin"):
        _reconciliation([], [], origin=ID3)


def test_package_roundtrip_and_rejections():
    data = enc.make_package(proposal(), [EVENT_BYTES])
    pkg = enc.decode_package(data)
    assert [c["id"] for c in pkg["copies"]] == [EVENT_ID]
    assert pkg["proposal"]["adopted"] == [ID1]
    with pytest.raises(enc.EncodingError):
        enc.decode_package(enc.canonical({"proposal": proposal(), "copies": [], "x": 1}))
    with pytest.raises(enc.EncodingError):
        enc.decode_package(data.replace(b",", b", ", 1))


def test_bundle_roundtrip_sorted_and_rejections():
    data = enc.make_bundle(GENESIS_ID, EVENT_ID, [EVENT_BYTES, GENESIS_BYTES])
    b = enc.decode_bundle(data)
    assert b["origin"] == GENESIS_ID and b["tip"] == EVENT_ID
    assert sorted([GENESIS_ID, EVENT_ID]) == [o["id"] for o in b["objects"]]
    assert enc.decode_object(GENESIS_BYTES)["id"] == GENESIS_ID
    spec = json.loads(data)
    spec["objects"].reverse()
    with pytest.raises(enc.EncodingError, match="sorted"):
        enc.decode_bundle(enc.canonical(spec))
    spec["objects"] = [spec["objects"][0]] * 2
    with pytest.raises(enc.EncodingError, match="sorted"):
        enc.decode_bundle(enc.canonical(spec))
    with pytest.raises(enc.EncodingError):
        enc.decode_bundle(enc.canonical({**json.loads(data), "format": "spine-bundle-v2"}))
    with pytest.raises(enc.EncodingError):
        enc.decode_bundle(enc.canonical({**json.loads(data), "extra": 1}))
