"""Micro tests for a device's view: validate, suppress, heads, classify.

Story: G lists Alice (a), Bob (b), Carol (c); P = Carol adds c2;
X = Alice removes Bob (parent P); Y = Bob adds b2 (parent P);
R is Alice's reconciliation "adopt X, reject Y"; nobody else signs it.
Each device's label is its own name, so tables read as {member: [labels]}.
Expected values are literals; object IDs are translated to these story names.
"""

import pytest

from small_sea_manager.spine import encoding, history, reconcile, view

TIME = 1_760_000_000  # the author's claimed time; no check reads it

NAMES = ["a", "b", "c", "c2", "b2", "b3", "b4", "c3", "c4", "a2", "a3"]
SEEDS = {name: bytes([n]) * 32 for n, name in enumerate(NAMES, 1)}
KEY = {name: encoding.public_key(seed) for name, seed in SEEDS.items()}

ALL = ["ordinary", "reconciliation"]
RESOLVING = ["reconciliation"]
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
        ], time=TIME)
        self.g = self.named("G", self.g_bytes)
        self.P = self.event("P", "c", self.g, add("carol", "c2"))
        self.X = self.event("X", "a", self.id("P"), remove("bob"))
        self.Y = self.event("Y", "b", self.id("P"), add("bob", "b2"))
        self.C = self.proposal(adopted=["X"], rejected=["Y"])
        self.R = self.r("R", self.C, [self.X, self.Y])

    def named(self, name, encoded):
        self.name[oid(encoded)] = name
        return oid(encoded)

    def id(self, name):
        return next(k for k, v in self.name.items() if v == name)

    def event(self, name, seed, parent, payload, kind="ordinary"):
        encoded = encoding.make_event(SEEDS[seed], self.g, kind, parent, payload, time=TIME)
        self.named(name, encoded)
        return encoded

    def proposal(self, adopted, rejected, tips=("X", "Y"), signer="a", base="P"):
        """What a reconciliation will say, by story name; base becomes its parent."""
        return {"base": base, "signer": signer, "tips": tips,
                "adopted": adopted, "rejected": rejected}

    def r(self, name, proposal, copies):
        ids = lambda names: [self.id(n) for n in names]
        payload = encoding.make_reconciliation_payload(
            ids(proposal["tips"]), ids(proposal["adopted"]), ids(proposal["rejected"]), copies)
        return self.event(name, proposal["signer"], self.id(proposal["base"]), payload,
                          "reconciliation")

    def view(self, *objects, followed=None, refused=()):
        """The summarized view; followed and refused are story names."""
        return self.summary(view.compute_view(
            list(objects), self.g_bytes, followed and self.id(followed),
            [self.id(n) for n in refused]))

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
            "contested": n(v["contested"]),
        }


@pytest.fixture
def w():
    return World()


OK = ("valid", None, [])


def followed(objects, head, tables_):
    return {"objects": objects, "heads": [head], "classification": "followed", "base": None,
            "tables": tables_, "may_publish": ALL, "conflicting_heads": [],
            "contested": []}


def conflict(objects, heads, tables_=P_TABLES, base="P"):
    return {"objects": objects, "heads": heads, "classification": "conflict", "base": base,
            "tables": tables_, "may_publish": RESOLVING, "conflicting_heads": heads,
            "contested": []}


# ---- followed ----

def test_p_only_is_followed(w):
    assert w.view(w.P) == followed({"P": OK}, "P", P_TABLES)


def test_y_alone_is_followed_and_accepted(w):
    assert w.view(w.P, w.Y) == followed(
        {"P": OK, "Y": OK}, "Y", {"alice": ["a"], "bob": ["b", "b2"], "carol": ["c", "c2"]})


def test_invalid_child_leaves_p_followed(w):
    bad = w.event("bad", "b2", w.id("P"), add("bob", "b3"))  # b2 is not a device at P
    assert w.view(w.P, bad) == followed(
        {"P": OK, "bad": ("invalid", "signer is not a device", [])}, "P", P_TABLES)


def test_foreign_origin_event_is_invalid_though_its_parent_reaches_g(w):
    # Xf names P as parent, so its chain reaches the pinned genesis; only origin is wrong.
    foreign = "ff" * 32
    xf = encoding.make_event(SEEDS["a"], foreign, "ordinary", w.id("P"), remove("bob"), time=TIME)
    w.named("Xf", xf)
    kid = w.event("Kid", "a", w.id("Xf"), add("alice", "a2"))
    origin = ("invalid", "origin is not the pinned genesis", [])
    assert w.view(w.P, xf, kid) == followed(
        {"P": OK, "Xf": origin, "Kid": origin}, "P", P_TABLES)


# ---- conflicts and splits ----

XY_OBJECTS = {"P": OK, "X": OK, "Y": OK}


def test_x_and_y_conflict_at_p(w):
    assert w.view(w.P, w.X, w.Y) == conflict(XY_OBJECTS, ["X", "Y"])


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
        "classification": "split awaiting join", "base": "P",
        "tables": {"alice": ["a", "a2"], "bob": ["b"], "carol": ["c", "c2", "c3"]},
        "may_publish": ["join"], "conflicting_heads": [], "contested": []}


def test_seven_concurrent_additions_are_too_many_to_check(w):
    adds = [("a", "alice", "a2"), ("a", "alice", "a3"), ("b", "bob", "b2"), ("b", "bob", "b3"),
            ("b", "bob", "b4"), ("c", "carol", "c3"), ("c", "carol", "c4")]
    events = [w.event(f"+{name}", seed, w.id("P"), add(member, name)) for seed, member, name in adds]
    names = sorted(f"+{name}" for _, _, name in adds)
    assert w.view(w.P, *events) == {
        "objects": {"P": OK, **{n: OK for n in names}}, "heads": names,
        "classification": "too many to check automatically", "base": "P", "tables": P_TABLES,
        "may_publish": RESOLVING, "conflicting_heads": [], "contested": []}


# ---- reconciliation ----
# A device holds the copies inside every reconciliation it stored,
# so X and Y get dispositions whenever R is held.

R_HELD = {"P": OK, "X": OK, "Y": OK, "R": OK}


def test_valid_r_is_followed(w):
    assert w.view(w.P, w.X, w.Y, w.R) == followed(R_HELD, "R", R_TABLES)


def test_y_first_arriving_after_r_never_becomes_a_head(w):
    assert w.view(w.P, w.R) == followed(R_HELD, "R", R_TABLES)
    assert w.view(w.P, w.R, w.Y) == followed(R_HELD, "R", R_TABLES)


def test_competing_reconciliations_stay_visible(w):
    r2 = w.r("R2", w.proposal(adopted=["Y"], rejected=["X"], signer="b"), [w.X, w.Y])
    assert w.view(w.P, w.X, w.Y, w.R, r2) == conflict({**R_HELD, "R2": OK}, ["R", "R2"])


def test_unrelated_head_beside_reconciled_split(w):
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    assert w.view(w.P, w.X, w.Y, w.R, z) == conflict({**R_HELD, "Z": OK}, ["R", "Z"])


def test_events_building_on_rejected_y_are_invalid_and_do_not_block(w):
    y2 = w.event("Y2", "b2", w.id("Y"), add("bob", "b3"))
    y3 = w.event("Y3", "b2", w.id("Y2"), add("bob", "b4"))
    rejected = ("invalid", "builds on a rejected event", [])
    assert w.view(w.P, w.X, w.Y, w.R, y2, y3) == followed(
        {**R_HELD, "Y2": rejected, "Y3": rejected}, "R", R_TABLES)


def test_check_reconciliation_accepts_r_on_rejected_y_that_the_view_refuses(w):
    # Known gap (spine README section 7): "builds on a rejected event" lives only in
    # the view, so check_reconciliation alone accepts R9, whose base Y is rejected by R.
    b2a = w.event("B2a", "b2", w.id("Y"), add("bob", "b3"))
    b2b = w.event("B2b", "b", w.id("Y"), add("bob", "b4"))
    c9 = w.proposal(adopted=["B2a", "B2b"], rejected=[], tips=("B2a", "B2b"), base="Y")
    r9 = w.r("R9", c9, [b2a, b2b])
    held = [w.P, w.X, w.Y, w.R, b2a, b2b, r9]
    lookup = history.build_lookup(held, w.g_bytes)
    assert reconcile.check_reconciliation(lookup, w.id("R9"))["status"] == "valid"
    rejected = ("invalid", "builds on a rejected event", [])
    assert w.view(*held) == followed({**R_HELD, "B2a": rejected, "B2b": rejected,
                                      "R9": rejected}, "R", R_TABLES)


def bad_reconciliation(w):
    """R's decision, signed by b3, which is not a device at the base."""
    return w.r("Rbad", w.proposal(adopted=["X"], rejected=["Y"], signer="b3"), [w.X, w.Y])


def test_event_on_invalid_reconciliation_is_invalid(w):
    bad_r = bad_reconciliation(w)
    z = w.event("Zbad", "c", w.id("Rbad"), add("carol", "c3"))
    assert w.view(w.P, w.X, w.Y, bad_r, z) == conflict({
        **XY_OBJECTS,
        "Rbad": ("invalid", "signer is not a device at the base", []),
        "Zbad": ("invalid", "built on an invalid reconciliation", []),
    }, ["X", "Y"])


def test_copies_inside_invalid_reconciliation_count_as_received(w):
    # Y is held only as a copy inside Rbad. Rbad is invalid, so nobody validly
    # rejected Y, and W (b2 adds b3 on Y) is judged like any other event.
    bad_r = bad_reconciliation(w)
    w_ = w.event("W", "b2", w.id("Y"), add("bob", "b3"))
    assert w.view(w.P, bad_r, w_) == conflict({
        **XY_OBJECTS, "W": OK,
        "Rbad": ("invalid", "signer is not a device at the base", []),
    }, ["W", "X"])


def test_event_on_pending_reconciliation_is_pending(w):
    # R carries only X, so Y (a resolved tip) is needed to find the base.
    # Bob's child would fail in the state Rpart proposes (Bob removed), but Rpart
    # is not yet judged, so the child waits with it instead of becoming invalid.
    r = w.r("Rpart", w.proposal(adopted=["X"], rejected=["Y"]), [w.X])
    z = w.event("Zpart", "c", w.id("Rpart"), add("carol", "c3"))
    zb = w.event("Zbob", "b", w.id("Rpart"), add("bob", "b3"))
    pending_child = ("pending", "built on a pending reconciliation", ["Y"])
    assert w.view(w.P, w.X, r, z, zb) == followed({
        "P": OK, "X": OK,
        "Rpart": ("pending", "missing ancestor", ["Y"]),
        "Zpart": pending_child, "Zbob": pending_child,
    }, "X", {"alice": ["a"], "carol": ["c", "c2"]})


def test_copies_nested_two_reconciliations_deep_get_dispositions(w):
    # Rbad (signed by a non-device) carries X and Y; Router carries Rbad, which fails partition.
    # Only P, Router, and W (b2 adds b3 on Y) are held standalone.
    bad_r = bad_reconciliation(w)
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    c = w.proposal(adopted=["Z"], rejected=["Rbad"], tips=("Rbad", "Z"))
    router = w.r("Router", c, [bad_r, z])
    w_ = w.event("W", "b2", w.id("Y"), add("bob", "b3"))
    assert w.view(w.P, router, w_) == conflict({
        **XY_OBJECTS, "Z": OK, "W": OK,
        "Rbad": ("invalid", "signer is not a device at the base", []),
        "Router": ("invalid", "a copy is not an ordinary event", []),
    }, ["W", "X", "Z"])


# ---- arrival order ----

def test_arrival_order_does_not_matter(w):
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    expected = conflict({**R_HELD, "Z": OK}, ["R", "Z"])
    objects = [w.P, w.X, w.Y, w.R, z]
    for order in (objects, objects[::-1], [w.R, z, w.P, w.Y, w.X]):
        assert w.view(*order) == expected


# ---- joins ----
# A2 = Alice adds a2 on P; B3 = Bob adds b3 on P. They do not interact.

JOINED = {"alice": ["a", "a2"], "bob": ["b", "b3"], "carol": ["c", "c2"]}


def join_of(w, *objects):
    return view.compute_view(list(objects), w.g_bytes)["join"]


def two_adds(w):
    return (w.event("A2", "a", w.id("P"), add("alice", "a2")),
            w.event("B3", "b", w.id("P"), add("bob", "b3")))


def test_alice_and_bob_each_add_a_device_and_a_join_resolves_it(w):
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    assert w.view(w.P, a2, b3, j) == followed(
        {"P": OK, "A2": OK, "B3": OK, "J": OK}, "J", JOINED)


def test_devices_holding_the_same_heads_build_the_same_join(w):
    a2, b3 = two_adds(w)
    assert join_of(w, w.P, a2, b3) == join_of(w, b3, w.P, a2)


def test_no_join_while_followed_or_in_conflict(w):
    a2, _ = two_adds(w)
    assert join_of(w, w.P, a2) is None
    assert join_of(w, w.P, w.X, w.Y) is None


def test_join_without_one_covered_event_is_pending(w):
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    v = w.view(w.P, a2, j)
    assert v["objects"]["J"] == ("pending", "missing ancestor", ["B3"])
    assert v["heads"] == ["A2"]


def test_join_of_interacting_events_is_invalid(w):
    j = encoding.make_join(w.g, w.id("P"), [w.id("X"), w.id("Y")])
    w.named("J", j)
    assert w.view(w.P, w.X, w.Y, j)["objects"]["J"] == (
        "invalid", "events do not merge: some order fails or the orders disagree", [])


def test_join_whose_parent_is_not_the_nearest_common_ancestor_is_invalid(w):
    a2, b3 = two_adds(w)
    j = encoding.make_join(w.g, w.g, [w.id("A2"), w.id("B3")])
    w.named("J", j)
    assert w.view(w.P, a2, b3, j)["objects"]["J"] == (
        "invalid", "parent is not the tips' nearest common ancestor", [])


def test_join_with_a_tip_on_another_tips_path_is_invalid(w):
    a2, _ = two_adds(w)
    j = encoding.make_join(w.g, w.g, [w.id("P"), w.id("A2")])
    w.named("J", j)
    assert w.view(w.P, a2, j)["objects"]["J"] == (
        "invalid", "a tip lies on another tip's path", [])


def test_join_covering_a_reconciliation_is_invalid(w):
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    j = encoding.make_join(w.g, w.id("P"), [w.id("R"), w.id("Z")])
    w.named("J", j)
    assert w.view(w.P, w.R, z, j)["objects"]["J"] == (
        "invalid", "a covered object is not an ordinary event", [])


def test_event_built_on_a_join_is_followed(w):
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    k = w.event("K", "b3", w.id("J"), add("bob", "b4"))
    assert w.view(w.P, a2, b3, j, k) == followed(
        {"P": OK, "A2": OK, "B3": OK, "J": OK, "K": OK}, "K",
        {**JOINED, "bob": ["b", "b3", "b4"]})


def test_late_child_of_a_joined_event_leads_to_a_larger_join_that_supersedes(w):
    # Bob adds b4 on B3 before he sees J. J counts as its tips, so the new join
    # names A2 and B4, covers A2, B3, and B4, and so supersedes J.
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    b4 = w.event("B4", "b3", w.id("B3"), add("bob", "b4"))
    held = {"P": OK, "A2": OK, "B3": OK, "J": OK, "B4": OK}
    v = w.view(w.P, a2, b3, j, b4)
    assert (v["classification"], v["heads"], v["may_publish"]) == (
        "split awaiting join", ["B4", "J"], ["join"])
    j2 = join_of(w, w.P, a2, b3, j, b4)
    assert encoding.decode_join(j2)["body"]["payload"]["tips"] == sorted([w.id("A2"), w.id("B4")])
    w.named("J2", j2)
    assert w.view(w.P, a2, b3, j, b4, j2) == followed(
        {**held, "J2": OK}, "J2", {**JOINED, "bob": ["b", "b3", "b4"]})


def test_three_way_split_joins_in_one_step(w):
    a2, b3 = two_adds(w)
    c3 = w.event("C3", "c", w.id("P"), add("carol", "c3"))
    j = join_of(w, w.P, a2, b3, c3)
    w.named("J", j)
    assert w.view(w.P, a2, b3, c3, j) == followed(
        {"P": OK, "A2": OK, "B3": OK, "C3": OK, "J": OK}, "J",
        {**JOINED, "carol": ["c", "c2", "c3"]})


def test_event_on_a_join_beside_a_late_child_stays_a_conflict(w):
    # No join resolves this: K builds on J while B4 is a late child of B3, which
    # J covers. A person settles it with an anchored reconciliation (see below).
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    k = w.event("K", "a", w.id("J"), add("alice", "a3"))
    b4 = w.event("B4", "b3", w.id("B3"), add("bob", "b4"))
    v = w.view(w.P, a2, b3, j, k, b4)
    assert (v["classification"], v["heads"]) == ("conflict", ["B4", "K"])
    assert join_of(w, w.P, a2, b3, j, k, b4) is None


def test_join_restating_events_a_reconciliation_rejected_is_invalid(w):
    # Codex critique A2: A2 and B3 do not interact, but R2 adopts A2 and rejects B3.
    # A relay builds the join of both from the old signatures; it must not
    # reopen R2's decision or stall the team.
    a2, b3 = two_adds(w)
    c = w.proposal(adopted=["A2"], rejected=["B3"], tips=("A2", "B3"))
    r2 = w.r("R2", c, [a2, b3])
    j = encoding.make_join(w.g, w.id("P"), [w.id("A2"), w.id("B3")])
    w.named("J", j)
    v = w.view(w.P, a2, b3, r2, j)
    assert (v["classification"], v["heads"], v["objects"]["J"]) == (
        "followed", ["R2"], ("invalid", "covers a rejected event", []))


def test_join_restating_events_a_reconciliation_adopted_is_suppressed(w):
    # R2 adopts both A2 and B3 (no rejection); the join of the same two events
    # reaches the same state and gives way to the person's reconciliation.
    a2, b3 = two_adds(w)
    c = w.proposal(adopted=["A2", "B3"], rejected=[], tips=("A2", "B3"))
    r2 = w.r("R2", c, [a2, b3])
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    v = w.view(w.P, a2, b3, r2, j)
    assert (v["classification"], v["heads"], v["objects"]["J"]) == ("followed", ["R2"], OK)


def test_join_on_an_earlier_base_supersedes_a_join_it_contains(w):
    # Codex critique A1: J joins A2 and B3 at P; Z adds c3 on G; K joins all at G.
    # K covers P, A2, B3, and Z, a strict superset of J's events, so J gives way
    # and the view never offers K again.
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    z = w.event("Z", "c", w.g, add("carol", "c3"))
    k = join_of(w, w.P, a2, b3, j, z)
    w.named("K", k)
    assert encoding.decode_join(k)["body"]["parents"] == [w.g]
    v = w.view(w.P, a2, b3, j, z, k)
    assert (v["classification"], v["heads"]) == ("followed", ["K"])
    assert join_of(w, w.P, a2, b3, j, z, k) is None


# ---- anchored reconciliations (F1: a late event beside a reconciliation) ----
# The anchored reconciliation's parent is one of its tips, whose history it takes
# as it stands; it decides only the other tips' events that history lacks.

def late_beside_r(w):
    """Carol adds c3 on P (Z) without having seen R; the heads are R and Z."""
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    return z


def test_late_event_beside_a_reconciliation_is_a_conflict(w):
    z = late_beside_r(w)
    v = w.view(w.P, w.X, w.Y, w.R, z)
    assert (v["classification"], v["heads"]) == ("conflict", ["R", "Z"])


def test_reconciliation_anchored_at_r_adopts_the_late_event(w):
    z = late_beside_r(w)
    r2 = w.r("R2", w.proposal(adopted=["Z"], rejected=[], tips=("R", "Z"), signer="c", base="R"), [z])
    assert w.view(w.P, w.X, w.Y, w.R, z, r2) == followed(
        {**R_HELD, "Z": OK, "R2": OK}, "R2", {"alice": ["a"], "carol": ["c", "c2", "c3"]})


def test_reconciliation_anchored_at_r_rejects_the_late_event_and_its_children(w):
    z = late_beside_r(w)
    z2 = w.event("Z2", "c", w.id("Z"), add("carol", "c4"))
    r2 = w.r("R2", w.proposal(adopted=[], rejected=["Z", "Z2"], tips=("R", "Z2"), base="R"), [z, z2])
    z3 = w.event("Z3", "c", w.id("Z2"), add("carol", "b3"))
    v = w.view(w.P, w.X, w.Y, w.R, z, z2, r2, z3)
    assert (v["classification"], v["heads"], v["tables"]) == ("followed", ["R2"], R_TABLES)
    assert v["objects"]["Z3"] == ("invalid", "builds on a rejected event", [])


def test_child_of_an_adopted_event_beside_r_needs_only_itself_decided(w):
    # spine README section 6, decision 6: Alice adds a2 on X before she sees R. X is part of
    # R's history, so the anchored reconciliation covers and carries only X2.
    x2 = w.event("X2", "a", w.id("X"), add("alice", "a2"))
    c = w.proposal(adopted=["X2"], rejected=[], tips=("R", "X2"), base="R")
    r2 = w.r("R2", c, [x2])
    assert w.view(w.P, w.X, w.Y, w.R, x2, r2) == followed(
        {**R_HELD, "X2": OK, "R2": OK}, "R2", {"alice": ["a", "a2"], "carol": ["c", "c2"]})
    carrying_x = w.r("R3", c, [w.X, x2])
    assert w.view(w.P, w.X, w.Y, w.R, x2, carrying_x)["objects"]["R3"] == (
        "invalid", "copies are not exactly the covered events", [])


def test_anchor_may_be_a_later_event_on_the_reconciliation(w):
    # Alice adds a3 on R (A3); Z arrives late beside both. The head is A3, so the
    # anchored reconciliation takes A3 as its parent; R is in A3's history.
    a3 = w.event("A3", "a", w.id("R"), add("alice", "a3"))
    z = late_beside_r(w)
    r2 = w.r("R2", w.proposal(adopted=["Z"], rejected=[], tips=("A3", "Z"), base="A3"), [z])
    assert w.view(w.P, w.X, w.Y, w.R, a3, z, r2) == followed(
        {**R_HELD, "A3": OK, "Z": OK, "R2": OK}, "R2",
        {"alice": ["a", "a3"], "carol": ["c", "c2", "c3"]})


def test_anchored_reconciliation_cannot_restore_what_the_anchor_rejected(w):
    # Y2 builds on Y, which R rejected; naming Y2 as a tip cannot bring it back.
    y2 = w.event("Y2", "b2", w.id("Y"), add("bob", "b3"))
    r2 = w.r("R2", w.proposal(adopted=["Y2"], rejected=[], tips=("R", "Y2"), base="R"), [y2])
    lookup = history.build_lookup([w.P, w.X, w.Y, w.R, y2, r2], w.g_bytes)
    found = reconcile.check_reconciliation(lookup, w.id("R2"))
    assert (found["status"], found["check"], found["reason"]) == (
        "invalid", "partition", "a tip builds on an event the anchor's history rejected")
    v = w.view(w.P, w.X, w.Y, w.R, y2, r2)
    assert (v["classification"], v["heads"]) == ("followed", ["R"])


def test_anchored_reconciliation_with_a_tip_on_the_anchors_path_is_invalid(w):
    r2 = w.r("R2", w.proposal(adopted=[], rejected=[], tips=("P", "R"), base="R"), [])
    assert w.view(w.P, w.X, w.Y, w.R, r2)["objects"]["R2"] == (
        "invalid", "a tip lies on another tip's path", [])


def test_member_removed_in_the_anchors_history_cannot_sign(w):
    # R removes Bob, so Bob is not a device at the anchor.
    z = late_beside_r(w)
    r2 = w.r("R2", w.proposal(adopted=[], rejected=["Z"], tips=("R", "Z"), signer="b", base="R"), [z])
    v = w.view(w.P, w.X, w.Y, w.R, z, r2)
    assert v["objects"]["R2"] == ("invalid", "signer is not a device at the base", [])
    assert v["heads"] == ["R", "Z"]


def test_anchored_reconciliation_settles_an_event_on_a_join_beside_a_late_child(w):
    # The gap from test_event_on_a_join_beside_a_late_child_stays_a_conflict:
    # K on J, B4 late on B3. Anchored at K, J's events count as K's history,
    # so the reconciliation covers only B4.
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    k = w.event("K", "a", w.id("J"), add("alice", "a3"))
    b4 = w.event("B4", "b3", w.id("B3"), add("bob", "b4"))
    r2 = w.r("R2", w.proposal(adopted=["B4"], rejected=[], tips=("B4", "K"), base="K"), [b4])
    v = w.view(w.P, a2, b3, j, k, b4, r2)
    assert (v["classification"], v["heads"], v["objects"]["R2"]) == ("followed", ["R2"], OK)
    assert v["tables"] == {**JOINED, "alice": ["a", "a2", "a3"], "bob": ["b", "b3", "b4"]}


# ---- a reconciliation that rejects what this device followed (spine README section 1) ----
# Carol's device followed Y before X arrived; Alice's R then rejects Y.

Y_TABLES = {"alice": ["a"], "bob": ["b", "b2"], "carol": ["c", "c2"]}


def must_choose(objects, heads, contested, tables_=Y_TABLES, base=None):
    return {"objects": objects, "heads": heads, "classification": "owner must choose",
            "base": base, "tables": tables_, "may_publish": RESOLVING,
            "conflicting_heads": [], "contested": contested}


def test_device_that_followed_a_rejected_event_does_not_follow_r(w):
    assert w.view(w.P, w.X, w.Y, w.R, followed="Y") == must_choose(R_HELD, ["R"], ["R"])


def test_device_that_followed_the_adopted_side_follows_r(w):
    assert w.view(w.P, w.X, w.Y, w.R, followed="X") == followed(R_HELD, "R", R_TABLES)


def test_device_that_followed_nothing_or_only_the_base_follows_r(w):
    assert w.view(w.P, w.X, w.Y, w.R) == followed(R_HELD, "R", R_TABLES)
    assert w.view(w.P, w.X, w.Y, w.R, followed="P") == followed(R_HELD, "R", R_TABLES)


def test_owner_accepts_r_by_following_it(w):
    c3 = w.event("C3", "c", w.id("R"), add("carol", "c3"))
    assert w.view(w.P, w.X, w.Y, w.R, c3, followed="R") == followed(
        {**R_HELD, "C3": OK}, "C3", {"alice": ["a"], "carol": ["c", "c2", "c3"]})


def test_later_events_on_r_do_not_end_the_choice(w):
    c3 = w.event("C3", "c", w.id("R"), add("carol", "c3"))
    assert w.view(w.P, w.X, w.Y, w.R, c3, followed="Y") == must_choose(
        {**R_HELD, "C3": OK}, ["C3"], ["R"])


def test_a_conflict_after_r_is_still_a_choice_first(w):
    # The device must not act on R's state (the conflict's base) before its owner chooses.
    ca = w.event("CA", "c", w.id("R"), remove("alice"))
    aa = w.event("AA", "a", w.id("R"), add("alice", "a3"))
    assert w.view(w.P, w.X, w.Y, w.R, ca, aa, followed="Y") == must_choose(
        {**R_HELD, "CA": OK, "AA": OK}, ["AA", "CA"], ["R"], base="R")


def test_owner_refuses_r_and_the_split_returns(w):
    c3 = w.event("C3", "c", w.id("R"), add("carol", "c3"))
    refused = ("invalid", "refused by this device's owner", [])
    assert w.view(w.P, w.X, w.Y, w.R, c3, followed="Y", refused=["R"]) == conflict(
        {**XY_OBJECTS, "R": refused,
         "C3": ("invalid", "builds on a refused reconciliation", [])}, ["X", "Y"])


def test_owner_refuses_r_and_follows_a_competing_reconciliation(w):
    # Carol objects with R2 (adopt Y, reject X). Her device follows R2; R stays refused.
    r2 = w.r("R2", w.proposal(adopted=["Y"], rejected=["X"], signer="c"), [w.X, w.Y])
    v = w.view(w.P, w.X, w.Y, w.R, r2, followed="Y", refused=["R"])
    assert (v["classification"], v["heads"], v["tables"]) == ("followed", ["R2"], Y_TABLES)
    assert v["objects"]["R"] == ("invalid", "refused by this device's owner", [])


def test_rollback_of_a_followed_event_without_a_split_is_a_choice(w):
    # Decision 2 lets R0 name the base P and Y as tips, so Alice can reject Y
    # with no competing event. Devices that followed Y must choose;
    # a device that never followed Y follows R0.
    r0 = w.r("R0", w.proposal(adopted=[], rejected=["Y"], tips=("P", "Y")), [w.Y])
    held = {"P": OK, "Y": OK, "R0": OK}
    assert w.view(w.P, w.Y, r0, followed="Y") == must_choose(held, ["R0"], ["R0"])
    assert w.view(w.P, w.Y, r0, followed="P") == followed(held, "R0", P_TABLES)


def test_anchored_reconciliation_rejecting_a_followed_late_event_is_a_choice(w):
    z = late_beside_r(w)
    r2 = w.r("R2", w.proposal(adopted=[], rejected=["Z"], tips=("R", "Z"), base="R"), [z])
    v = w.view(w.P, w.X, w.Y, w.R, z, r2, followed="Z")
    assert (v["classification"], v["heads"], v["contested"]) == (
        "owner must choose", ["R2"], ["R2"])
    assert v["tables"] == {"alice": ["a"], "bob": ["b"], "carol": ["c", "c2", "c3"]}


def test_device_that_followed_a_join_must_choose_when_r_rejects_a_joined_event(w):
    # J's events are not on J's own parent chain, but the device acted on them.
    a2, b3 = two_adds(w)
    j = join_of(w, w.P, a2, b3)
    w.named("J", j)
    r2 = w.r("R2", w.proposal(adopted=["A2"], rejected=["B3"], tips=("A2", "B3")), [a2, b3])
    v = w.view(w.P, a2, b3, j, r2, followed="J")
    assert (v["classification"], v["heads"], v["contested"], v["tables"]) == (
        "owner must choose", ["R2"], ["R2"], JOINED)


# Codex review of the anchored form (codex-p2-review q4, q5): an anchor whose
# history after the nearest common ancestor holds only ordinary events let two
# decisions reject each other's anchors, which crashed the view.

def anchored(w, name, base, tips, adopted, rejected, copies, signer="a"):
    return w.r(name, w.proposal(adopted, rejected, tips=tips, signer=signer, base=base), copies)


def test_anchor_with_only_ordinary_history_is_invalid(w):
    a2, b3 = two_adds(w)
    s = anchored(w, "S", "A2", ("A2", "B3"), ["B3"], [], [b3])
    assert w.view(w.P, a2, b3, s)["objects"]["S"] == (
        "invalid", "the anchor's history holds no reconciliation or join", [])


def test_opposite_ordinary_anchors_do_not_crash_the_view(w):
    a2, b3 = two_adds(w)
    s = anchored(w, "S", "A2", ("A2", "B3"), [], ["B3"], [b3])
    t = anchored(w, "T", "B3", ("A2", "B3"), [], ["A2"], [a2], signer="b")
    v = w.view(w.P, a2, b3, s, t)
    assert (v["classification"], v["heads"]) == ("split awaiting join", ["A2", "B3"])


def test_opposite_ordinary_anchors_after_r_do_not_crash_the_view(w):
    a = w.event("A", "a", w.id("R"), add("alice", "a2"))
    b = w.event("B", "c", w.id("R"), add("carol", "c3"))
    s = anchored(w, "S", "A", ("A", "B"), [], ["B"], [b])
    t = anchored(w, "T", "B", ("A", "B"), [], ["A"], [a], signer="c")
    v = w.view(w.P, w.R, a, b, s, t)
    assert (v["classification"], v["heads"]) == ("split awaiting join", ["A", "B"])


def test_only_reconciliations_can_be_refused(w):
    # Codex review (codex-p3-review q3): refusing an ordinary event could not stop a
    # join or reconciliation from applying it, so the view ignores such refusals.
    assert w.view(w.P, w.Y, refused=["Y"]) == followed({"P": OK, "Y": OK}, "Y", Y_TABLES)
