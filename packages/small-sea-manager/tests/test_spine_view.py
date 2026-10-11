"""Micro tests for a device's view: validate, suppress, heads, classify.

Story: G lists Alice (a), Bob (b), Carol (c); P = Carol adds c2;
X = Alice removes Bob (parent P); Y = Bob adds b2 (parent P);
U and V are approvals by a and c of the proposal "adopt X, reject Y";
R is the reconciliation built from them.
Each device's label is its own name, so tables read as {member: [labels]}.
Expected values are literals; object IDs are translated to these story names.
"""

import pytest

from small_sea_manager.spine import encoding, view

NAMES = ["a", "b", "c", "c2", "b2", "b3", "b4", "c3", "c4", "a2", "a3"]
SEEDS = {name: bytes([n]) * 32 for n, name in enumerate(NAMES, 1)}
KEY = {name: encoding.public_key(seed) for name, seed in SEEDS.items()}

ALL = ["approval", "ordinary", "reconciliation"]
RESOLVING = ["approval", "reconciliation"]
P_TABLES = {"alice": ["a"], "bob": ["b"], "carol": ["c", "c2"]}
R_TABLES = {"alice": ["a"], "carol": ["c", "c2"]}


def oid(encoded):
    return encoding.decode_object(encoded)["id"]


def add(member, name):
    return {"op": "add_device", "member_id": member, "device_key": KEY[name], "label": name}


def remove(member):
    return {"op": "remove_member", "member_id": member}


def tables(state):
    out = {member: [] for member in state["members"]}
    for row in state["devices"].values():
        out[row["member_id"]].append(row["label"])
    return {member: sorted(labels) for member, labels in out.items()}


class World:
    def __init__(self):
        self.name = {}
        self.g_bytes = encoding.make_genesis("00" * 32, [
            {"member_id": "alice", "name": "Alice"},
            {"member_id": "bob", "name": "Bob"},
            {"member_id": "carol", "name": "Carol"},
        ], [
            {"device_key": KEY["a"], "member_id": "alice", "label": "a"},
            {"device_key": KEY["b"], "member_id": "bob", "label": "b"},
            {"device_key": KEY["c"], "member_id": "carol", "label": "c"},
        ])
        self.g = self.named("G", self.g_bytes)
        self.P = self.event("P", "c", self.g, add("carol", "c2"))
        self.X = self.event("X", "a", self.id("P"), remove("bob"))
        self.Y = self.event("Y", "b", self.id("P"), add("bob", "b2"))
        self.C = self.proposal(adopted=["X"], rejected=["Y"])
        self.U, self.V = self.approvals(self.C, {"U": "a", "V": "c"})
        self.R = self.r("R", self.C, [self.X, self.Y], [self.U, self.V])

    def named(self, name, encoded):
        self.name[oid(encoded)] = name
        return oid(encoded)

    def id(self, name):
        return next(k for k, v in self.name.items() if v == name)

    def event(self, name, seed, parent, payload, kind="ordinary"):
        encoded = encoding.make_event(SEEDS[seed], self.g, kind, parent, payload)
        self.named(name, encoded)
        return encoded

    def proposal(self, adopted, rejected, tips=("X", "Y"), signer="a"):
        return {
            "format": "spine-proposal-v1", "origin": self.g, "base": self.id("P"),
            "signer": KEY[signer], "resolved_tips": sorted(self.id(t) for t in tips),
            "adopted": [self.id(n) for n in adopted],
            "rejected": sorted(self.id(n) for n in rejected),
        }

    def approvals(self, proposal, by):
        digest = encoding.proposal_digest(proposal)
        return [self.event(name, seed, proposal["base"], {"approves": digest}, "approval")
                for name, seed in by.items()]

    def r(self, name, proposal, copies, approvals, signer="a"):
        payload = encoding.make_reconciliation_payload(proposal, copies, approvals)
        return self.event(name, signer, proposal["base"], payload, "reconciliation")

    def view(self, *objects):
        return self.summary(view.compute_view(list(objects), self.g_bytes))

    def summary(self, v):
        """Translate IDs to story names and state to tables; keep everything else."""
        n = lambda ids: [self.name[i] for i in ids]
        return {
            "objects": {self.name[i]: (d["status"], d["reason"], n(d["missing"]))
                        for i, d in v["objects"].items()},
            "heads": sorted(n(v["heads"])),
            "classification": v["classification"],
            "base": self.name[v["base"]] if v["base"] else None,
            "tables": tables(v["acting_state"]),
            "may_publish": v["may_publish"],
            "conflicting_heads": sorted(n(v["conflicting_heads"])),
        }


@pytest.fixture
def w():
    return World()


OK = ("valid", None, [])


def followed(objects, head, tables_):
    return {"objects": objects, "heads": [head], "classification": "followed", "base": None,
            "tables": tables_, "may_publish": ALL, "conflicting_heads": []}


def conflict(objects, heads, tables_=P_TABLES, base="P"):
    return {"objects": objects, "heads": heads, "classification": "conflict", "base": base,
            "tables": tables_, "may_publish": RESOLVING, "conflicting_heads": heads}


# ---- followed ----

def test_p_only_is_followed(w):
    assert w.view(w.P) == followed({"P": OK}, "P", P_TABLES)


def test_y_alone_is_followed_and_accepted(w):
    assert w.view(w.P, w.Y) == followed(
        {"P": OK, "Y": OK}, "Y", {"alice": ["a"], "bob": ["b", "b2"], "carol": ["c", "c2"]})


def test_approval_child_and_invalid_child_leave_p_followed(w):
    bad = w.event("bad", "b2", w.id("P"), add("bob", "b3"))  # b2 is not a device at P
    assert w.view(w.P, w.U, bad) == followed(
        {"P": OK, "U": OK, "bad": ("invalid", "signer is not a device", [])}, "P", P_TABLES)


# ---- conflicts and splits ----

XY_OBJECTS = {"P": OK, "X": OK, "Y": OK}


def test_x_and_y_conflict_at_p(w):
    assert w.view(w.P, w.X, w.Y) == conflict(XY_OBJECTS, ["X", "Y"])


def test_approvals_are_never_heads(w):
    assert w.view(w.P, w.X, w.Y, w.U, w.V) == conflict(
        {**XY_OBJECTS, "U": OK, "V": OK}, ["X", "Y"])


def test_x_and_y_pending_without_p_then_conflict(w):
    pending = ("pending", "missing ancestor", ["P"])
    assert w.view(w.X, w.Y) == followed(
        {"X": pending, "Y": pending}, "G", {"alice": ["a"], "bob": ["b"], "carol": ["c"]})
    assert w.view(w.X, w.Y, w.P) == conflict(XY_OBJECTS, ["X", "Y"])


def test_compatible_additions_split_awaiting_join(w):
    a2 = w.event("A2", "a", w.id("P"), add("alice", "a2"))
    c3 = w.event("C3", "c", w.id("P"), add("carol", "c3"))
    assert w.view(w.P, a2, c3) == {
        "objects": {"P": OK, "A2": OK, "C3": OK}, "heads": ["A2", "C3"],
        "classification": "split awaiting join", "base": "P", "tables": P_TABLES,
        "may_publish": [], "conflicting_heads": []}


def test_seven_concurrent_additions_are_unsupported_so_conflict(w):
    adds = [("a", "alice", "a2"), ("a", "alice", "a3"), ("b", "bob", "b2"), ("b", "bob", "b3"),
            ("b", "bob", "b4"), ("c", "carol", "c3"), ("c", "carol", "c4")]
    events = [w.event(f"+{name}", seed, w.id("P"), add(member, name)) for seed, member, name in adds]
    names = sorted(f"+{name}" for _, _, name in adds)
    assert w.view(w.P, *events) == conflict({"P": OK, **{n: OK for n in names}}, names)


# ---- reconciliation ----

def test_valid_r_is_followed(w):
    assert w.view(w.P, w.X, w.Y, w.R) == followed(
        {"P": OK, "X": OK, "Y": OK, "R": OK}, "R", R_TABLES)


def test_y_first_arriving_after_r_never_becomes_a_head(w):
    assert w.view(w.P, w.R) == followed({"P": OK, "R": OK}, "R", R_TABLES)
    assert w.view(w.P, w.R, w.Y) == followed({"P": OK, "R": OK, "Y": OK}, "R", R_TABLES)


def test_competing_reconciliations_stay_visible(w):
    c2 = w.proposal(adopted=["Y"], rejected=["X"], signer="b")
    approvals = w.approvals(c2, {"U2": "b", "V2": "c"})
    r2 = w.r("R2", c2, [w.X, w.Y], approvals, signer="b")
    assert w.view(w.P, w.X, w.Y, w.R, r2) == conflict(
        {"P": OK, "X": OK, "Y": OK, "R": OK, "R2": OK}, ["R", "R2"])


def test_unrelated_head_beside_reconciled_split(w):
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    assert w.view(w.P, w.X, w.Y, w.R, z) == conflict(
        {"P": OK, "X": OK, "Y": OK, "R": OK, "Z": OK}, ["R", "Z"])


def test_new_child_of_rejected_y_stays_visible(w):
    y2 = w.event("Y2", "b2", w.id("Y"), add("bob", "b3"))
    assert w.view(w.P, w.X, w.Y, w.R, y2) == conflict(
        {"P": OK, "X": OK, "Y": OK, "R": OK, "Y2": OK}, ["R", "Y2"])


def test_event_on_invalid_reconciliation_is_invalid(w):
    bad_r = w.r("Rbad", w.C, [w.X, w.Y], [w.U])  # one approval: below quorum
    z = w.event("Zbad", "c", w.id("Rbad"), add("carol", "c3"))
    assert w.view(w.P, w.X, w.Y, bad_r, z) == conflict({
        **XY_OBJECTS,
        "Rbad": ("invalid", "approvals do not reach quorum", []),
        "Zbad": ("invalid", "built on an invalid reconciliation", []),
    }, ["X", "Y"])


def test_event_on_pending_reconciliation_is_pending(w):
    # R carries only X, so Y (a resolved tip) is needed to find the base.
    c = w.proposal(adopted=["X"], rejected=["Y"])
    r = w.r("Rpart", c, [w.X], w.approvals(c, {"Up": "a", "Vp": "c"}))
    z = w.event("Zpart", "c", w.id("Rpart"), add("carol", "c3"))
    assert w.view(w.P, w.X, r, z) == followed({
        "P": OK, "X": OK,
        "Rpart": ("pending", "missing ancestor", ["Y"]),
        "Zpart": ("pending", "built on a pending reconciliation", ["Y"]),
    }, "X", {"alice": ["a"], "carol": ["c", "c2"]})


# ---- arrival order ----

def test_arrival_order_does_not_matter(w):
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    expected = conflict({"P": OK, "X": OK, "Y": OK, "U": OK, "V": OK, "R": OK, "Z": OK},
                        ["R", "Z"])
    objects = [w.P, w.X, w.Y, w.U, w.V, w.R, z]
    for order in (objects, objects[::-1], [w.R, z, w.V, w.P, w.Y, w.U, w.X]):
        assert w.view(*order) == expected
