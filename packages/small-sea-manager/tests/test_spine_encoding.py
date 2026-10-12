"""Micro tests for spine canonical encoding, strict decoding, IDs, and signatures."""

import hashlib
import json

import pytest

from small_sea_manager.spine import encoding as enc

TIME = 1_760_000_000  # the author's claimed time; no check reads it

GENESIS_BYTES = b'{"devices":[{"device_key":"8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c","label":"laptop","member_id":"alice"},{"device_key":"8139770ea87d175f56a35466c34c7ecccb8d8a91b4ee37a25df60f5b8fc9b394","label":"laptop","member_id":"bob"},{"device_key":"ed4928c628d1c2c6eae90338905995612959273a5c63f93636c14614ac8737d1","label":"laptop","member_id":"carol"}],"format":"spine-v1","kind":"genesis","members":[{"member_id":"alice","name":"Alice"},{"member_id":"bob","name":"Bob"},{"member_id":"carol","name":"Carol"}],"nonce":"0000000000000000000000000000000000000000000000000000000000000000","policy":{"name":"spine-demo-1","settings":{}},"time":1760000000}'
GENESIS_ID = "7aae385fa1c7a8d300c98cee667515bafe3105513f55dd0ab7eb3704dea40864"
EVENT_BYTES = b'{"body":{"author":"8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c","format":"spine-v1","kind":"ordinary","origin":"7aae385fa1c7a8d300c98cee667515bafe3105513f55dd0ab7eb3704dea40864","parents":["7aae385fa1c7a8d300c98cee667515bafe3105513f55dd0ab7eb3704dea40864"],"payload":{"member_id":"bob","op":"remove_member"},"time":1760000000},"sig":"55b2d3de39bcaf87a9faad40f6925626f3ce9eeaa5274a3a3f0946616b7aca645b827f0a72dd1941cfd7372347ab782a127fbcf554dc044cde1f0d8f565fa509"}'
EVENT_ID = "660dde69a4b1f0e8d011b7f93a876c4638f9649b5df204d256e599560d581974"
EVENT_SIG = "55b2d3de39bcaf87a9faad40f6925626f3ce9eeaa5274a3a3f0946616b7aca645b827f0a72dd1941cfd7372347ab782a127fbcf554dc044cde1f0d8f565fa509"
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


def test_vector_genesis():
    assert enc.make_genesis("00" * 32, [
        {"member_id": "alice", "name": "Alice"},
        {"member_id": "bob", "name": "Bob"},
        {"member_id": "carol", "name": "Carol"},
    ], [
        {"device_key": enc.public_key(SEED_A), "member_id": "alice", "label": "laptop"},
        {"device_key": enc.public_key(SEED_B), "member_id": "bob", "label": "laptop"},
        {"device_key": enc.public_key(SEED_C), "member_id": "carol", "label": "laptop"},
    ], time=TIME) == GENESIS_BYTES
    assert enc.decode_genesis(GENESIS_BYTES)["id"] == GENESIS_ID
    assert enc.public_key(SEED_A) == ALICE_KEY


def test_vector_event():
    data = enc.make_event(
        SEED_A, GENESIS_ID, "ordinary", GENESIS_ID,
        {"op": "remove_member", "member_id": "bob"}, time=TIME,
    )
    assert data == EVENT_BYTES
    decoded = enc.decode_event(EVENT_BYTES)
    assert decoded["id"] == EVENT_ID
    assert decoded["sig"] == EVENT_SIG


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
        {"name": "spine-demo-1", "settings": {"reconcile_quorum": 2}},
        {"name": "other", "settings": {}},
        {"name": "spine-demo-1"},
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


def test_time_is_required_and_a_non_negative_integer():
    for bad in ("1760000000", [TIME], {"time": TIME}):
        with pytest.raises(enc.EncodingError):
            enc.decode_genesis(genesis(time=bad))
        with pytest.raises(enc.EncodingError):
            enc.decode_event(resign(event_env(time=bad)))
    with pytest.raises(enc.EncodingError):
        enc.canonical(event_env(time=-1))
    g = json.loads(GENESIS_BYTES)
    del g["time"]
    with pytest.raises(enc.EncodingError):
        enc.decode_genesis(enc.canonical(g))
    env = event_env()
    del env["body"]["time"]
    with pytest.raises(enc.EncodingError):
        enc.decode_event(resign(env))


def test_time_is_only_a_claim():
    # A clock set to 1970 or to the far future still makes a valid event.
    for claimed in (0, 2**40):
        assert enc.decode_event(resign(event_env(time=claimed)))["body"]["time"] == claimed


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


def test_add_device_decodes_and_approvals_are_gone():
    add = enc.make_event(SEED_B, GENESIS_ID, "ordinary", GENESIS_ID, {
        "op": "add_device", "member_id": "bob", "device_key": "ab" * 32, "label": "phone"}, time=TIME)
    assert enc.decode_event(add)["body"]["payload"]["label"] == "phone"
    with pytest.raises(enc.EncodingError, match="unknown event kind"):
        enc.make_event(SEED_C, GENESIS_ID, "approval", GENESIS_ID, {"approves": "ab" * 32}, time=TIME)


def _payload(copies, **changes):
    payload = {"tips": [ID1, ID2], "adopted": [ID1], "rejected": [ID2],
               "copies": [c.hex() for c in copies]}
    payload.update(changes)
    return payload


def _reconciliation(copies, **changes):
    return enc.make_event(SEED_A, GENESIS_ID, "reconciliation", GENESIS_ID,
                          _payload(copies, **changes), time=TIME)


def _two_copies():
    """EVENT_BYTES and a second event, sorted by ID."""
    y = enc.make_event(SEED_B, GENESIS_ID, "ordinary", GENESIS_ID, {
        "op": "add_device", "member_id": "bob", "device_key": "ab" * 32, "label": "phone"}, time=TIME)
    return sorted([EVENT_BYTES, y], key=lambda e: enc.decode_event(e)["id"])


def test_reconciliation_decodes_with_copies_sorted_by_id():
    r = enc.decode_event(_reconciliation(_two_copies()))
    ids = [c["id"] for c in r["copies"]]
    assert EVENT_ID in ids and ids == sorted(ids) and len(ids) == 2
    assert set(r) == {"id", "body", "sig", "copies"}
    # adopted is in replay order, so any order is accepted
    r = enc.decode_event(_reconciliation([], adopted=[ID3, ID1]))
    assert r["body"]["payload"]["adopted"] == [ID3, ID1]


def test_make_reconciliation_payload_sorts_tips_rejected_and_copies():
    copies = _two_copies()
    payload = enc.make_reconciliation_payload([ID2, ID1], [ID3, ID1], [ID3, ID2], copies[::-1])
    assert payload == _payload(copies, adopted=[ID3, ID1], rejected=[ID2, ID3])


def test_reconciliation_rejects_unsorted_duplicate_and_bad_embedded():
    x = EVENT_BYTES
    copies = [c.hex() for c in _two_copies()]
    for payload in (
        {**_payload([]), "copies": copies[::-1]},
        {**_payload([]), "copies": [copies[0]] * 2},
        {**_payload([]), "copies": [x.hex()[:-2] + "00"]},
        {**_payload([]), "copies": ["zz"]},
        _payload([x], approvals=[]),  # the old approvals list is an unknown key
        {key: v for key, v in _payload([x]).items() if key != "tips"},
    ):
        with pytest.raises(enc.EncodingError):
            enc.make_event(SEED_A, GENESIS_ID, "reconciliation", GENESIS_ID, payload, time=TIME)


def test_reconciliation_rejects_unsorted_lists():
    for changes in ({"tips": [ID2, ID1]}, {"rejected": [ID3, ID2]}):
        with pytest.raises(enc.EncodingError, match="not sorted"):
            _reconciliation([], **changes)


def test_reconciliation_rejects_copy_with_other_origin():
    other = enc.make_event(
        SEED_A, ID3, "ordinary", ID3, {"op": "remove_member", "member_id": "bob"}, time=TIME)
    with pytest.raises(enc.EncodingError, match="origin"):
        _reconciliation([other])


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


def test_join_roundtrip_and_id_is_the_hash_of_the_body():
    data = enc.make_join(GENESIS_ID, EVENT_ID, [ID2, ID1])
    j = enc.decode_object(data)
    assert j["body"]["payload"]["tips"] == [ID1, ID2]
    assert j["id"] == enc.object_id(j["body"]) and "sig" not in j
    assert data == enc.make_join(GENESIS_ID, EVENT_ID, [ID1, ID2])


def test_join_rejects_unsorted_tips_extra_keys_and_two_parents():
    body = json.loads(enc.make_join(GENESIS_ID, EVENT_ID, [ID1, ID2]))["body"]
    for change in ({"payload": {"tips": [ID2, ID1]}}, {"payload": {"tips": [ID1, ID1]}},
                   {"parents": [ID1, ID2]}, {"author": ALICE_KEY},
                   {"payload": {"tips": [ID1, ID2], "adopted": []}}, {"time": TIME}):
        with pytest.raises(enc.EncodingError):
            enc.decode_object(enc.canonical({"body": {**body, **change}}))
    with pytest.raises(enc.EncodingError):
        enc.decode_object(enc.canonical({"body": body, "copies": []}))


def test_signed_join_is_refused():
    body = json.loads(enc.make_join(GENESIS_ID, EVENT_ID, [ID1, ID2]))["body"]
    body["author"] = ALICE_KEY
    with pytest.raises(enc.EncodingError, match="unknown event kind"):
        enc.decode_object(enc.canonical({"body": body, "sig": enc.sign(SEED_A, body)}))
