"""Micro tests for the reconciliation checks.

Story: G lists Alice (a), Bob (b), Carol (c); P = Carol adds c2;
X = Alice removes Bob (parent P); Y = Bob adds b2 (parent P).
Every R below gets a freshly computed digest, fresh approvals, and a fresh
signature unless a test says otherwise, so quorum cannot mask the check under test.
"""

import pytest

from small_sea_manager.spine import encoding, history, reconcile

NAMES = ["a", "b", "c", "c2", "b2", "b3", "c3"]
SEEDS = {name: bytes([n]) * 32 for n, name in enumerate(NAMES, 1)}
KEY = {name: encoding.public_key(seed) for name, seed in SEEDS.items()}


def oid(encoded):
    return encoding.decode_object(encoded)["id"]


def add_device(member, name, label="phone"):
    return {"op": "add_device", "member_id": member, "device_key": KEY[name], "label": label}


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


class World:
    def __init__(self):
        self.g_bytes = make_genesis()
        self.g = oid(self.g_bytes)
        self.p_bytes = self.event("c", self.g, add_device("carol", "c2"))
        self.p = oid(self.p_bytes)
        self.x_bytes = self.event("a", self.p, {"op": "remove_member", "member_id": "bob"})
        self.x = oid(self.x_bytes)
        self.y_bytes = self.event("b", self.p, add_device("bob", "b2"))
        self.y = oid(self.y_bytes)

    def event(self, seed, parent, payload, kind="ordinary", origin=None):
        return encoding.make_event(SEEDS[seed], origin or self.g, kind, parent, payload)

    def proposal(self, adopted, rejected, tips=None, base=None, signer="a"):
        return {
            "format": "spine-proposal-v1", "origin": self.g, "base": base or self.p,
            "signer": KEY[signer], "resolved_tips": sorted(tips or [self.x, self.y]),
            "adopted": adopted, "rejected": sorted(rejected),
        }

    def approvals(self, proposal, approvers=("a", "c"), parent=None):
        digest = encoding.proposal_digest(proposal)
        return [self.event(s, parent or proposal["base"], {"approves": digest}, "approval")
                for s in approvers]

    def r(self, proposal, copies, approvals=None, author=None):
        """Sign R for this proposal; approvals default to fresh ones from a and c."""
        if approvals is None:
            approvals = self.approvals(proposal)
        payload = encoding.make_reconciliation_payload(proposal, list(copies), list(approvals))
        signer = author or next(k for k, v in KEY.items() if v == proposal["signer"])
        return self.event(signer, proposal["base"], payload, "reconciliation")

    def check(self, r, *extra):
        lookup = history.build_lookup([self.p_bytes, *extra, r], self.g_bytes)
        return reconcile.check_reconciliation(lookup, oid(r))


@pytest.fixture
def w():
    return World()


def names(state):
    return sorted(state["members"])


def devices(state):
    return sorted((v["member_id"], v["label"]) for v in state["devices"].values())


def verdict(result):
    return result["status"], result["check"], result["reason"]


# ---- valid reconciliations ----

def test_valid_r_adopts_removal_and_rejects_addition(w):
    r = w.r(w.proposal([w.x], [w.y]), [w.x_bytes, w.y_bytes])
    result = w.check(r, w.x_bytes, w.y_bytes)
    assert verdict(result) == ("valid", None, None)
    assert result["missing"] == []
    assert names(result["state"]) == ["alice", "carol"]
    assert devices(result["state"]) == [("alice", "laptop"), ("carol", "laptop"), ("carol", "phone")]
    after = reconcile.Evaluator(history.build_lookup([w.p_bytes, r], w.g_bytes)).result(oid(r))
    assert names(after["state"]) == ["alice", "carol"]
    assert devices(after["state"]) == [("alice", "laptop"), ("carol", "laptop"), ("carol", "phone")]


def test_bob_signs_and_approves_his_own_removal(w):
    # Authority comes from the base, where Bob is still a member.
    c = w.proposal([w.x], [w.y], signer="b")
    r = w.r(c, [w.x_bytes, w.y_bytes], w.approvals(c, ("b", "c")))
    result = w.check(r)
    assert verdict(result) == ("valid", None, None)
    assert names(result["state"]) == ["alice", "carol"]


# ---- core and shape ----

def test_r_with_another_origin_fails_core(w):
    other = oid(make_genesis("11"))
    c = dict(w.proposal([], []), origin=other)
    payload = encoding.make_reconciliation_payload(c, [], [])
    r = w.event("a", w.p, payload, "reconciliation", origin=other)
    assert verdict(w.check(r)) == ("invalid", "core", "origin is not the pinned genesis")


def test_author_other_than_proposal_signer_fails_shape(w):
    r = w.r(w.proposal([w.x], [w.y]), [w.x_bytes, w.y_bytes], author="c")
    assert verdict(w.check(r)) == ("invalid", "shape", "author is not the proposal's signer")


def test_parent_other_than_proposal_base_fails_shape(w):
    c = w.proposal([w.x], [w.y])
    payload = encoding.make_reconciliation_payload(c, [w.x_bytes, w.y_bytes], w.approvals(c))
    r = w.event("a", w.g, payload, "reconciliation")
    assert verdict(w.check(r)) == ("invalid", "shape", "parent is not the proposal's base")


def test_single_resolved_tip_fails_shape(w):
    r = w.r(w.proposal([w.x], [], tips=[w.x]), [w.x_bytes])
    assert verdict(w.check(r)) == ("invalid", "shape", "fewer than two distinct resolved tips")


# ---- base ----

def test_declared_base_g_rejecting_p_fails_base(w):
    c = w.proposal([], [w.p, w.x, w.y], base=w.g)
    r = w.r(c, [w.p_bytes, w.x_bytes, w.y_bytes])
    assert verdict(w.check(r)) == (
        "invalid", "base", "declared base is not the nearest common ancestor")


def test_r_reaching_lookup_without_p_is_incomplete(w):
    r = w.r(w.proposal([w.x], [w.y]), [w.x_bytes, w.y_bytes])
    lookup = history.build_lookup([r], w.g_bytes)
    result = reconcile.check_reconciliation(lookup, oid(r))
    assert (result["status"], result["check"]) == ("incomplete", "base")
    assert result["missing"] == [w.p]


def on_base(w, base_r):
    """R1 resolves two children of base_r: adopt K1 (Carol adds c3), reject K2 (Alice adds b3)."""
    base = oid(base_r)
    k1 = w.event("c", base, add_device("carol", "c3", "tablet"))
    k2 = w.event("a", base, add_device("alice", "b3", "tablet"))
    c = w.proposal([oid(k1)], [oid(k2)], tips=[oid(k1), oid(k2)], base=base)
    return w.r(c, [k1, k2]), k1, k2


def test_unauthorized_reconciliation_as_base_fails_base(w):
    r0 = w.r(w.proposal([w.x], [w.y]), [w.x_bytes, w.y_bytes], approvals=[])
    r1, k1, k2 = on_base(w, r0)
    assert verdict(w.check(r1, r0, k1, k2)) == (
        "invalid", "base", "base is invalid: approvals do not reach quorum")


def test_pending_reconciliation_as_base_makes_r_incomplete(w):
    r0 = w.r(w.proposal([w.x], [w.y]), [w.x_bytes])  # Y is neither carried nor held
    r1, k1, k2 = on_base(w, r0)
    result = w.check(r1, r0, k1, k2)
    assert verdict(result) == ("incomplete", "base", "base is pending: missing ancestor")
    assert result["missing"] == [w.y]


# ---- partition ----

def test_event_both_adopted_and_rejected_fails_partition(w):
    r = w.r(w.proposal([w.x], [w.x, w.y]), [w.x_bytes, w.y_bytes])
    assert verdict(w.check(r)) == ("invalid", "partition", "an event is listed twice")


def test_tip_neither_adopted_nor_rejected_fails_partition(w):
    r = w.r(w.proposal([w.x], []), [w.x_bytes, w.y_bytes])
    assert verdict(w.check(r)) == (
        "invalid", "partition", "adopted and rejected do not match the covered events")


def test_extra_copy_outside_branches_fails_partition(w):
    z = w.event("c", w.p, add_device("carol", "c3", "tablet"))
    r = w.r(w.proposal([w.x], [w.y]), [w.x_bytes, w.y_bytes, z])
    assert verdict(w.check(r)) == (
        "invalid", "partition", "copies are not exactly the covered events")


def test_resolved_tip_at_the_base_is_allowed(w):
    # P is an ancestor of X, so the derived base is P itself and P contributes no covered events.
    # Allowed: R may name any published tip (decision 2, after-t3-decisions.md).
    r = w.r(w.proposal([w.x], [], tips=[w.p, w.x]), [w.x_bytes])
    result = w.check(r)
    assert verdict(result) == ("valid", None, None)
    assert names(result["state"]) == ["alice", "carol"]


def test_approval_carried_as_a_copy_fails_partition(w):
    u = w.event("c", w.p, {"approves": "ab" * 32}, "approval")
    r = w.r(w.proposal([w.x], [oid(u)], tips=[w.x, oid(u)]), [w.x_bytes, u])
    assert verdict(w.check(r)) == ("invalid", "partition", "a copy is not an ordinary event")


# ---- original validity ----

def test_copy_invalid_at_its_own_parent_fails_original(w):
    # b2 does not exist at P, so it cannot sign there, even in a rejected branch.
    bad = w.event("b2", w.p, add_device("bob", "b3"))
    c = w.proposal([w.x], [oid(bad)], tips=[w.x, oid(bad)])
    r = w.r(c, [w.x_bytes, bad])
    assert verdict(w.check(r)) == (
        "invalid", "original", "a copy is invalid at its parent: signer is not a device")


# ---- closure and replay, on a two-event branch Y1 -> Y2 ----

@pytest.fixture
def branch(w):
    """Y1 = Bob adds b2; Y2 = Bob (b) adds b3 on Y1, which also works without Y1."""
    y1 = w.y_bytes
    y2 = w.event("b", w.y, add_device("bob", "b3", "tablet"))
    return y1, y2


def test_rejecting_y1_but_adopting_y2_fails_closure(w, branch):
    y1, y2 = branch
    c = w.proposal([oid(y2)], [w.x, oid(y1)], tips=[w.x, oid(y2)])
    r = w.r(c, [w.x_bytes, y1, y2])
    assert verdict(w.check(r)) == (
        "invalid", "closure", "an adopted event descends from a rejected event")


def test_adopting_child_before_parent_fails_replay(w, branch):
    y1, y2 = branch
    c = w.proposal([oid(y2), oid(y1)], [w.x], tips=[w.x, oid(y2)])
    r = w.r(c, [w.x_bytes, y1, y2])
    assert verdict(w.check(r)) == ("invalid", "replay", "an adopted event comes before its parent")


def test_adopting_both_x_and_y_fails_replay(w):
    r = w.r(w.proposal([w.x, w.y], []), [w.x_bytes, w.y_bytes])
    assert verdict(w.check(r)) == ("invalid", "replay", "replay fails: signer is not a device")


# ---- authority ----

def test_stale_approvals_on_edited_proposal_fail_authority(w):
    original = w.proposal([w.x], [w.y])
    stale = w.approvals(original)
    assert verdict(w.check(w.r(original, [w.x_bytes, w.y_bytes], stale)))[0] == "valid"
    edited = w.proposal([w.y], [w.x])
    r = w.r(edited, [w.x_bytes, w.y_bytes], stale)
    assert verdict(w.check(r)) == ("invalid", "authority", "approvals do not reach quorum")


def test_one_members_approval_fails_authority(w):
    c = w.proposal([w.x], [w.y])
    r = w.r(c, [w.x_bytes, w.y_bytes], w.approvals(c, ("a",)))
    assert verdict(w.check(r)) == ("invalid", "authority", "approvals do not reach quorum")


def test_two_devices_of_one_member_count_once(w):
    c = w.proposal([w.x], [w.y])
    r = w.r(c, [w.x_bytes, w.y_bytes], w.approvals(c, ("c", "c2")))
    assert verdict(w.check(r)) == ("invalid", "authority", "approvals do not reach quorum")


def test_r_signed_by_device_absent_at_base_fails_authority(w):
    r = w.r(w.proposal([w.x], [w.y], signer="b2"), [w.x_bytes, w.y_bytes])
    assert verdict(w.check(r)) == ("invalid", "authority", "signer is not a device at the base")


def test_approval_not_parenting_the_base_fails_authority(w):
    # a and c alone reach quorum; the extra approval on G must still be refused.
    c = w.proposal([w.x], [w.y])
    approvals = w.approvals(c) + w.approvals(c, ("c2",), parent=w.g)
    r = w.r(c, [w.x_bytes, w.y_bytes], approvals)
    assert verdict(w.check(r)) == ("invalid", "authority", "an approval does not parent the base")
