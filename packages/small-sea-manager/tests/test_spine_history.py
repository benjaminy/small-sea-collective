"""Micro tests for spine lookup, ancestry, state at an object, and original validation."""

import pytest

from small_sea_manager.spine import encoding, history

SEEDS = {name: bytes([n]) * 32 for n, name in enumerate(["a", "b", "c", "c2", "b2", "x"], 1)}
KEY = {name: encoding.public_key(seed) for name, seed in SEEDS.items()}


def make_genesis(nonce="00"):
    return encoding.make_genesis(nonce * 32, [
        {"member_id": "alice", "name": "Alice"},
        {"member_id": "bob", "name": "Bob"},
        {"member_id": "carol", "name": "Carol"},
    ], [
        {"device_key": KEY["a"], "member_id": "alice", "label": "laptop"},
        {"device_key": KEY["b"], "member_id": "bob", "label": "laptop"},
        {"device_key": KEY["c"], "member_id": "carol", "label": "laptop"},
    ])


def oid(encoded):
    return encoding.decode_object(encoded)["id"]


def add_device(member, name, label="phone"):
    return {"op": "add_device", "member_id": member, "device_key": KEY[name], "label": label}


def remove(member):
    return {"op": "remove_member", "member_id": member}


class World:
    """G -> P (Carol adds c2); X (Alice removes Bob) and Y (Bob adds b2) both on P."""

    def __init__(self):
        self.g_bytes = make_genesis()
        self.g = oid(self.g_bytes)
        self.p_bytes = encoding.make_event(SEEDS["c"], self.g, "ordinary", self.g, add_device("carol", "c2"))
        self.p = oid(self.p_bytes)
        self.x_bytes = encoding.make_event(SEEDS["a"], self.g, "ordinary", self.p, remove("bob"))
        self.x = oid(self.x_bytes)
        self.y_bytes = encoding.make_event(SEEDS["b"], self.g, "ordinary", self.p, add_device("bob", "b2"))
        self.y = oid(self.y_bytes)

    def event(self, seed, parent, payload, kind="ordinary"):
        return encoding.make_event(SEEDS[seed], self.g, kind, parent, payload)

    def approval(self, seed, parent):
        return self.event(seed, parent, {"approves": "ab" * 32}, "approval")

    def reconciliation(self, adopted, rejected, copies, approvals=(), parent=None, signer="a"):
        parent = parent or self.p
        proposal = {
            "format": "spine-proposal-v1", "origin": self.g, "base": parent, "signer": KEY[signer],
            "resolved_tips": sorted([self.x, self.y]), "adopted": adopted, "rejected": sorted(rejected),
        }
        payload = encoding.make_reconciliation_payload(proposal, list(copies), list(approvals))
        return self.event(signer, parent, payload, "reconciliation")

    def lookup(self, *objects):
        return history.build_lookup(list(objects), self.g_bytes)


@pytest.fixture
def w():
    return World()


def names(state):
    return {k: v["name"] for k, v in state["members"].items()}


def devices(state):
    return sorted((v["member_id"], v["label"]) for v in state["devices"].values())


def test_state_after_add_then_removal(w):
    result = history.state_at(w.lookup(w.p_bytes, w.x_bytes), w.x)
    assert result["status"] == "valid"
    assert names(result["state"]) == {"alice": "Alice", "carol": "Carol"}
    assert devices(result["state"]) == [("alice", "laptop"), ("carol", "laptop"), ("carol", "phone")]


def test_state_at_genesis_is_genesis_tables(w):
    result = history.state_at(w.lookup(), w.g)
    assert names(result["state"]) == {"alice": "Alice", "bob": "Bob", "carol": "Carol"}
    assert devices(result["state"]) == [("alice", "laptop"), ("bob", "laptop"), ("carol", "laptop")]


def test_ancestry_incomplete_names_missing_parent(w):
    lookup = w.lookup(w.x_bytes)
    result = history.ancestry(lookup, w.x)
    assert result["status"] == "incomplete"
    assert result["missing"] == [w.p]
    assert history.state_at(lookup, w.x)["missing"] == [w.p]


def test_ancestry_unknown_object_is_incomplete(w):
    assert history.ancestry(w.lookup(), "ff" * 32)["missing"] == ["ff" * 32]


def test_ancestry_complete_reaches_pinned_genesis(w):
    assert history.ancestry(w.lookup(w.p_bytes, w.x_bytes), w.x) == {
        "status": "valid", "reason": None, "missing": [], "state": None}


def test_chain_to_different_genesis_with_right_origin_is_invalid(w):
    other_bytes = make_genesis("11")
    other = oid(other_bytes)
    # Carries the pinned origin, but its parent chain ends at another genesis.
    forged = w.event("c", other, add_device("carol", "c2"))
    lookup = history.build_lookup([other_bytes, forged], w.g_bytes)
    result = history.ancestry(lookup, oid(forged))
    assert result["status"] == "invalid"
    assert result["reason"] == "ancestry reaches a different genesis"
    assert history.validate_original(lookup, oid(forged))["status"] == "invalid"


def test_cycle_is_invalid(w):
    a = {"id": "01" * 32, "body": {"kind": "ordinary", "parents": ["02" * 32]}}
    b = {"id": "02" * 32, "body": {"kind": "ordinary", "parents": ["01" * 32]}}
    lookup = history.Lookup({a["id"]: a, b["id"]: b}, w.g, w.g_bytes)
    assert history.ancestry(lookup, a["id"])["reason"] == "ancestry has a cycle"


def test_device_added_on_other_branch_cannot_sign_here(w):
    # b2 exists only on the Y branch; signing on the X branch must fail at X's parent state.
    z = w.event("b2", w.x, add_device("bob", "x"))
    lookup = w.lookup(w.p_bytes, w.x_bytes, w.y_bytes, z)
    assert history.validate_original(lookup, w.y)["status"] == "valid"
    result = history.validate_original(lookup, oid(z))
    assert (result["status"], result["reason"]) == ("invalid", "signer is not a device")
    on_y = w.event("b2", w.y, add_device("bob", "x"))
    assert history.validate_original(w.lookup(w.p_bytes, w.y_bytes, on_y), oid(on_y))["status"] == "valid"


def test_removed_member_device_signing_after_removal_is_invalid(w):
    late = w.event("b", w.x, add_device("bob", "x"))
    result = history.validate_original(w.lookup(w.p_bytes, w.x_bytes, late), oid(late))
    assert (result["status"], result["reason"]) == ("invalid", "signer is not a device")


def test_event_whose_parent_is_an_approval_is_invalid(w):
    u = w.approval("a", w.p)
    child = w.event("c", oid(u), add_device("carol", "x"))
    lookup = w.lookup(w.p_bytes, u, child)
    assert history.validate_original(lookup, oid(u))["status"] == "valid"
    result = history.validate_original(lookup, oid(child))
    assert (result["status"], result["reason"]) == ("invalid", "parent is an approval")


def test_approval_by_non_device_is_invalid_and_valid_one_keeps_state(w):
    bad = w.approval("x", w.p)
    assert history.validate_original(w.lookup(w.p_bytes, bad), oid(bad))["reason"] == "signer is not a device"
    good = w.approval("a", w.p)
    result = history.validate_original(w.lookup(w.p_bytes, good), oid(good))
    assert result["status"] == "valid"
    assert devices(result["state"]) == [
        ("alice", "laptop"), ("bob", "laptop"), ("carol", "laptop"), ("carol", "phone")]


def test_validate_original_refuses_other_kinds(w):
    assert history.validate_original(w.lookup(), w.g)["reason"] == "not an ordinary event or approval"


def test_lookup_resolves_event_only_present_as_copy_in_reconciliation(w):
    u, v = w.approval("a", w.p), w.approval("c", w.p)
    r = w.reconciliation([w.x], [w.y], [w.x_bytes, w.y_bytes], [u, v])
    lookup = w.lookup(w.p_bytes, r)  # no standalone X or Y
    assert history.validate_original(lookup, w.y)["status"] == "valid"
    assert oid(u) in lookup.objects and oid(v) in lookup.objects
    assert history.ancestry(lookup, w.x)["status"] == "valid"


def test_state_at_reconciliation_applies_adopted_removal(w):
    r = w.reconciliation([w.x], [w.y], [w.x_bytes, w.y_bytes])
    result = history.state_at(w.lookup(w.p_bytes, r), oid(r))
    assert result["status"] == "valid"
    assert names(result["state"]) == {"alice": "Alice", "carol": "Carol"}
    assert devices(result["state"]) == [("alice", "laptop"), ("carol", "laptop"), ("carol", "phone")]


def test_state_at_reconciliation_adopting_only_the_addition_keeps_bob(w):
    r = w.reconciliation([w.y], [w.x], [w.x_bytes, w.y_bytes])
    result = history.state_at(w.lookup(w.p_bytes, r), oid(r))
    assert names(result["state"]) == {"alice": "Alice", "bob": "Bob", "carol": "Carol"}
    assert ("bob", "phone") in devices(result["state"])


def test_state_builds_on_a_reconciliation_ancestor(w):
    r = w.reconciliation([w.x], [w.y], [w.x_bytes, w.y_bytes])
    child = w.event("c2", oid(r), add_device("carol", "x", "tablet"))
    result = history.state_at(w.lookup(w.p_bytes, r, child), oid(child))
    assert devices(result["state"]) == [
        ("alice", "laptop"), ("carol", "laptop"), ("carol", "phone"), ("carol", "tablet")]


def test_reconciliation_with_failing_adopted_step_is_invalid_not_partial(w):
    r = w.reconciliation([w.x, w.y], [], [w.x_bytes, w.y_bytes])
    result = history.state_at(w.lookup(w.p_bytes, r), oid(r))
    assert result["status"] == "invalid"
    assert result["reason"] == "replay fails: signer is not a device"
    assert result["state"] is None


def test_reconciliation_adopting_uncarried_event_is_invalid(w):
    r = w.reconciliation([w.x], [w.y], [w.y_bytes])
    result = history.state_at(w.lookup(w.p_bytes, r), oid(r))
    assert (result["status"], result["reason"]) == ("invalid", "adopted event is not carried")
