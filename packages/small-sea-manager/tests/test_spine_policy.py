"""Micro tests for the fixed spine-demo-1 policy on hand-built states."""

import pytest

from small_sea_manager.spine import encoding, policy

TIME = 1_760_000_000  # the author's claimed time; no check reads it

BASE = "base"
A2, A3, B3, C2 = [encoding.public_key(bytes([i]) * 32) for i in range(10, 14)]


@pytest.fixture
def state():
    return {
        "members": {
            "alice": {"member_id": "alice", "name": "Alice"},
            "bob": {"member_id": "bob", "name": "Bob"},
            "carol": {"member_id": "carol", "name": "Carol"},
        },
        "devices": {
            "a": {"device_key": "a", "member_id": "alice", "label": "laptop"},
            "b": {"device_key": "b", "member_id": "bob", "label": "laptop"},
            "b2": {"device_key": "b2", "member_id": "bob", "label": "phone"},
            "c": {"device_key": "c", "member_id": "carol", "label": "laptop"},
        },
        "policy": {"name": "spine-demo-1", "settings": {}},
    }


def add(member, key, label="phone"):
    return {"op": "add_device", "member_id": member, "device_key": key, "label": label}


def event(event_id, parent, signer, payload):
    return {"id": event_id, "body": {
        "parents": [parent], "author": signer, "payload": payload,
    }}


def test_initial_state_decodes_genesis_and_keys_tables():
    data = encoding.make_genesis("00" * 32, [
        {"member_id": "alice", "name": "Alice"},
        {"member_id": "bob", "name": "Bob"},
    ], [
        {"device_key": "aa" * 32, "member_id": "alice", "label": "laptop"},
        {"device_key": "bb" * 32, "member_id": "bob", "label": "phone"},
    ], time=TIME)
    assert policy.initial_state(data) == {
        "members": {
            "alice": {"member_id": "alice", "name": "Alice"},
            "bob": {"member_id": "bob", "name": "Bob"},
        },
        "devices": {
            "aa" * 32: {"device_key": "aa" * 32, "member_id": "alice", "label": "laptop"},
            "bb" * 32: {"device_key": "bb" * 32, "member_id": "bob", "label": "phone"},
        },
        "policy": {"name": "spine-demo-1", "settings": {}},
    }
    with pytest.raises(encoding.EncodingError):
        policy.initial_state(data.replace(b'"spine-demo-1"', b'"wrong-policy"'))


def test_add_device_adds_own_row_without_mutating_input(state):
    result, reason = policy.apply_ordinary(state, "a", add("alice", A2, "tablet"))
    assert reason is None
    assert result["devices"][A2] == {
        "device_key": A2, "member_id": "alice", "label": "tablet",
    }
    assert A2 not in state["devices"]
    result["policy"]["settings"]["extra"] = "x"
    assert state["policy"]["settings"] == {}


@pytest.mark.parametrize(("signer", "payload", "reason"), [
    ("a", add("bob", B3), "cannot add a device for another member"),
    ("a", add("alice", "b"), "device key already exists"),
    ("a", {"op": "remove_member", "member_id": "alice"}, "cannot remove self"),
    ("a", {"op": "remove_member", "member_id": "dave"}, "member does not exist"),
    ("absent", add("alice", A2), "signer is not a device"),
])
def test_ordinary_refusals(state, signer, payload, reason):
    before = {"members": dict(state["members"]), "devices": dict(state["devices"]),
              "policy": {"name": "spine-demo-1", "settings": {}}}
    assert policy.apply_ordinary(state, signer, payload) == (None, reason)
    assert state == before


def test_remove_member_removes_all_its_devices(state):
    result, reason = policy.apply_ordinary(
        state, "a", {"op": "remove_member", "member_id": "bob"})
    assert reason is None
    assert result["members"] == {
        "alice": {"member_id": "alice", "name": "Alice"},
        "carol": {"member_id": "carol", "name": "Carol"},
    }
    assert sorted(result["devices"]) == ["a", "c"]
    assert sorted(state["devices"]) == ["a", "b", "b2", "c"]


def test_independent_additions_merge(state):
    events = [event("x", BASE, "a", add("alice", A2)),
              event("y", BASE, "c", add("carol", C2))]
    merged, reason = policy.merge(state, events)
    assert reason is None
    assert sorted(merged["devices"]) == sorted(["a", "b", "b2", "c", A2, C2])
    assert sorted(state["devices"]) == ["a", "b", "b2", "c"]


def test_remove_bob_conflicts_with_bob_adding_device(state):
    events = [event("x", BASE, "a", {"op": "remove_member", "member_id": "bob"}),
              event("y", BASE, "b", add("bob", B3))]
    assert policy.merge(state, events) == (None, "some order fails or the orders disagree")


def test_merge_respects_parent_links(state):
    events = [event("x", BASE, "a", add("alice", A2)),
              event("y", "x", A2, add("alice", A3))]
    assert policy.merge(state, events)[1] is None


def test_merge_limit_is_more_than_six(state):
    events = [
        event(str(i), BASE, "a", add("alice", encoding.public_key(bytes([i + 20]) * 32)))
        for i in range(1, 8)
    ]
    assert policy.merge(state, events[:6])[1] is None
    assert policy.merge(state, events) == (None, "more than six events")

