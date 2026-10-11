"""Micro tests for the fixed spine-demo-1 policy on hand-built states."""

import pytest

from small_sea_manager.spine import encoding, policy


BASE = "base"
DIGEST = "digest"


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
        "policy": {"name": "spine-demo-1", "settings": {"reconcile_quorum": 2}},
    }


def add(member, key, label="phone"):
    return {"op": "add_device", "member_id": member, "device_key": key, "label": label}


def event(event_id, parent, signer, payload):
    return {"id": event_id, "body": {
        "parents": [parent], "author": signer, "payload": payload,
    }}


def approval(signer, digest=DIGEST, parent=BASE):
    return {"body": {"kind": "approval", "author": signer,
                     "parents": [parent], "payload": {"approves": digest}}}


def test_initial_state_decodes_genesis_and_keys_tables():
    data = encoding.make_genesis("00" * 32, [
        {"member_id": "alice", "name": "Alice"},
        {"member_id": "bob", "name": "Bob"},
    ], [
        {"device_key": "aa" * 32, "member_id": "alice", "label": "laptop"},
        {"device_key": "bb" * 32, "member_id": "bob", "label": "phone"},
    ])
    assert policy.initial_state(data) == {
        "members": {
            "alice": {"member_id": "alice", "name": "Alice"},
            "bob": {"member_id": "bob", "name": "Bob"},
        },
        "devices": {
            "aa" * 32: {"device_key": "aa" * 32, "member_id": "alice", "label": "laptop"},
            "bb" * 32: {"device_key": "bb" * 32, "member_id": "bob", "label": "phone"},
        },
        "policy": {"name": "spine-demo-1", "settings": {"reconcile_quorum": 2}},
    }
    with pytest.raises(encoding.EncodingError):
        policy.initial_state(data.replace(b'"spine-demo-1"', b'"wrong-policy"'))


def test_add_device_adds_own_row_without_mutating_input(state):
    result, reason = policy.apply_ordinary(state, "a", add("alice", "a2", "tablet"))
    assert reason is None
    assert result["devices"]["a2"] == {
        "device_key": "a2", "member_id": "alice", "label": "tablet",
    }
    assert "a2" not in state["devices"]
    result["policy"]["settings"]["reconcile_quorum"] = 3
    assert state["policy"]["settings"]["reconcile_quorum"] == 2


@pytest.mark.parametrize(("signer", "payload", "reason"), [
    ("a", add("bob", "b3"), "cannot add a device for another member"),
    ("a", add("alice", "b"), "device key already exists"),
    ("a", {"op": "remove_member", "member_id": "alice"}, "cannot remove self"),
    ("a", {"op": "remove_member", "member_id": "dave"}, "member does not exist"),
    ("absent", add("alice", "a2"), "signer is not a device"),
])
def test_ordinary_refusals(state, signer, payload, reason):
    before = {"members": dict(state["members"]), "devices": dict(state["devices"]),
              "policy": {"name": "spine-demo-1", "settings": {"reconcile_quorum": 2}}}
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


def test_approval_checks_signer_and_changes_nothing(state):
    assert policy.approval_valid(state, "b2") is True
    assert policy.approval_valid(state, "absent") is False
    assert sorted(state["devices"]) == ["a", "b", "b2", "c"]


def test_independent_additions_are_compatible(state):
    events = [event("x", BASE, "a", add("alice", "a2")),
              event("y", BASE, "c", add("carol", "c2"))]
    assert policy.compatibility(state, events) == "compatible"
    assert sorted(state["devices"]) == ["a", "b", "b2", "c"]


def test_remove_bob_conflicts_with_bob_adding_device(state):
    events = [event("x", BASE, "a", {"op": "remove_member", "member_id": "bob"}),
              event("y", BASE, "b", add("bob", "b3"))]
    assert policy.compatibility(state, events) == "incompatible"


def test_compatibility_respects_parent_links(state):
    events = [event("x", BASE, "a", add("alice", "a2")),
              event("y", "x", "a2", add("alice", "a3"))]
    assert policy.compatibility(state, events) == "compatible"


def test_compatibility_limit_is_more_than_six(state):
    events = [event(str(i), BASE, "a", add("alice", f"a{i}")) for i in range(1, 8)]
    assert policy.compatibility(state, events[:6]) == "compatible"
    assert policy.compatibility(state, events) == "unsupported"


def test_quorum_counts_distinct_members(state):
    assert policy.reconciliation_authorized(
        state, BASE, "a", DIGEST, [approval("a"), approval("c")]) is True
    assert policy.reconciliation_authorized(
        state, BASE, "a", DIGEST, [approval("b"), approval("c")]) is True


def test_two_devices_of_one_member_do_not_meet_quorum(state):
    assert policy.reconciliation_authorized(
        state, BASE, "a", DIGEST, [approval("b"), approval("b2")]) is False


@pytest.mark.parametrize("bad_approval", [
    approval("c", digest="other"),
    approval("c", parent="other"),
    approval("absent"),
])
def test_wrong_digest_parent_or_nonbase_approver_does_not_count(state, bad_approval):
    assert policy.reconciliation_authorized(
        state, BASE, "a", DIGEST, [approval("b"), bad_approval]) is False


def test_publisher_signature_alone_is_not_an_approval(state):
    assert policy.reconciliation_authorized(state, BASE, "a", DIGEST, []) is False
    assert policy.reconciliation_authorized(
        state, BASE, "a", DIGEST, [approval("c")]) is False


def test_publisher_must_be_device_at_base(state):
    assert policy.reconciliation_authorized(
        state, BASE, "absent", DIGEST, [approval("a"), approval("c")]) is False
