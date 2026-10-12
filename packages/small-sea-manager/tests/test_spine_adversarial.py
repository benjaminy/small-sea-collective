"""Adversarial and view micro tests for the spine, written from the slice 1 plan.

Sources: small_sea_manager/spine/README.md, sections 3, 4, and 5, and section 6
(children of rejected events are invalid; copies inside any reconciliation get
their own dispositions, and ancestry may pass through them).

Story: G lists Alice (a), Bob (b), Carol (c); P = Carol adds c2;
X = Alice removes Bob (parent P); Y = Bob adds b2 (parent P);
R resolves "adopt X, reject Y", signed by a alone.
Expected values are literals; object IDs are translated to story names.
"""

import hashlib
import itertools
import random
import time

import pytest

from small_sea_manager.spine import encoding, history, policy, reconcile, view

TIME = 1_760_000_000  # the author's claimed time; no check reads it

ALL = ["ordinary", "reconciliation"]
RESOLVING = ["reconciliation"]
P_TABLES = {"alice": ["a"], "bob": ["b"], "carol": ["c", "c2"]}
R_TABLES = {"alice": ["a"], "carol": ["c", "c2"]}


def seed(name):
    return hashlib.sha256(name.encode()).digest()


def key(name):
    return encoding.public_key(seed(name))


def add(member, name):
    return {"op": "add_device", "member_id": member, "device_key": key(name), "label": name}


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
            {"device_key": key("a"), "member_id": "alice", "label": "a"},
            {"device_key": key("b"), "member_id": "bob", "label": "b"},
            {"device_key": key("c"), "member_id": "carol", "label": "c"},
        ], time=TIME)
        self.g = self.named("G", self.g_bytes)
        self.P = self.event("P", "c", self.g, add("carol", "c2"))
        self.X = self.event("X", "a", self.id("P"), remove("bob"))
        self.Y = self.event("Y", "b", self.id("P"), add("bob", "b2"))
        self.C = self.prop("P", ["X", "Y"], ["X"], ["Y"], "a")
        self.R = self.r("R", self.C, [self.X, self.Y], "a")

    def named(self, name, encoded):
        self.name[encoding.decode_object(encoded)["id"]] = name
        return encoding.decode_object(encoded)["id"]

    def id(self, name):
        return next(k for k, v in self.name.items() if v == name)

    def event(self, name, who, parent, payload, kind="ordinary"):
        encoded = encoding.make_event(seed(who), self.g, kind, parent, payload, time=TIME)
        self.named(name, encoded)
        return encoded

    def prop(self, base, tips, adopted, rejected, signer):
        """What a reconciliation will say, as IDs; base becomes its parent."""
        return {
            "base": self.id(base), "signer": signer,
            "tips": [self.id(t) for t in tips], "adopted": [self.id(n) for n in adopted],
            "rejected": [self.id(n) for n in rejected],
        }

    def r(self, name, proposal, copies, signer):
        payload = encoding.make_reconciliation_payload(
            proposal["tips"], proposal["adopted"], proposal["rejected"], copies)
        return self.event(name, signer, proposal["base"], payload, "reconciliation")

    def lookup(self, *objects):
        return history.build_lookup(list(objects), self.g_bytes)

    def check(self, r_name, *objects):
        """Run the reconciliation checks on the named R given the held objects."""
        found = reconcile.check_reconciliation(self.lookup(*objects), self.id(r_name))
        return found["status"], found["check"]

    def view(self, *objects):
        v = view.compute_view(list(objects), self.g_bytes)
        n = lambda ids: sorted(self.name[i] for i in ids)
        return {
            "status": {self.name[i]: d["status"] for i, d in v["objects"].items()},
            "reason": {self.name[i]: d["reason"] for i, d in v["objects"].items()},
            "heads": n(v["heads"]),
            "classification": v["classification"],
            "base": self.name[v["base"]] if v["base"] else None,
            "tables": tables(v["acting_state"]),
            "may_publish": v["may_publish"],
        }


@pytest.fixture
def w():
    return World()


def valid(*names):
    return {n: "valid" for n in names}


def second_conflict(w):
    """From R: Carol removes Alice (CA) while Alice adds a3 (AA)."""
    ca = w.event("CA", "c", w.id("R"), remove("alice"))
    aa = w.event("AA", "a", w.id("R"), add("alice", "a3"))
    return ca, aa


# ---- structure of what a reconciliation may carry ----

def test_reconciliation_carried_as_a_copy_is_invalid_partition(w):
    # README s4 check 4 (slice 1: copies must be ordinary): R1 as a copy inside R3
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    c3 = w.prop("P", ["R", "Z"], ["Z"], ["R"], "a")
    r3 = w.r("R3", c3, [w.R, z], "a")
    assert w.check("R3", w.P, w.X, w.Y, w.R, z, r3) == ("invalid", "partition")
    v = w.view(w.P, w.X, w.Y, w.R, z, r3)
    assert v["status"]["R3"] == "invalid"
    assert v["heads"] == ["R", "Z"]
    assert v["classification"] == "conflict"
    assert v["tables"] == P_TABLES


# ---- competing and layered reconciliations ----

def test_overlapping_reconciliations_stay_a_conflict(w):
    # README s4 View: competing valid reconciliations stay visible and blocking
    wev = w.event("W", "c", w.id("P"), add("carol", "c3"))
    c2 = w.prop("P", ["Y", "W"], ["Y", "W"], [], "c")
    r2 = w.r("R2", c2, [w.Y, wev], "c")
    v = w.view(w.P, w.X, w.Y, wev, w.R, r2)
    assert v["status"] == valid("P", "X", "Y", "W", "R", "R2")
    assert v["heads"] == ["R", "R2"]
    assert v["classification"] == "conflict"
    assert v["base"] == "P"
    assert v["tables"] == P_TABLES
    assert v["may_publish"] == RESOLVING


def test_second_conflict_after_a_resolved_one(w):
    # README s4 check 3 and s5: base may be a valid reconciliation; authority is judged at R
    ca, aa = second_conflict(w)
    before = w.view(w.P, w.X, w.Y, w.R, ca, aa)
    assert before["heads"] == ["AA", "CA"]
    assert before["classification"] == "conflict"
    assert before["base"] == "R"
    assert before["tables"] == R_TABLES
    assert before["may_publish"] == RESOLVING

    c2 = w.prop("R", ["AA", "CA"], ["CA"], ["AA"], "c")
    r2 = w.r("R2", c2, [aa, ca], "c")
    after = w.view(w.P, w.X, w.Y, w.R, ca, aa, r2)
    assert after["status"] == valid("P", "X", "Y", "R", "CA", "AA", "R2")
    assert after["heads"] == ["R2"]
    assert after["classification"] == "followed"
    assert after["tables"] == {"carol": ["c", "c2"]}
    assert after["may_publish"] == ALL


def test_second_resolution_adopting_the_other_side(w):
    # README s4 check 7: replay from the base state R, adopting AA and rejecting CA
    ca, aa = second_conflict(w)
    c2 = w.prop("R", ["AA", "CA"], ["AA"], ["CA"], "a")
    r2 = w.r("R2", c2, [aa, ca], "a")
    v = w.view(w.P, w.X, w.Y, w.R, ca, aa, r2)
    assert v["heads"] == ["R2"]
    assert v["tables"] == {"alice": ["a", "a3"], "carol": ["c", "c2"]}


def test_second_resolution_signed_by_member_removed_at_base_fails(w):
    # README s5: the signer must be a device at the base
    ca, aa = second_conflict(w)
    c2 = w.prop("R", ["AA", "CA"], ["CA"], ["AA"], "b")
    r2 = w.r("R2", c2, [aa, ca], "b")
    assert w.check("R2", w.P, w.X, w.Y, w.R, ca, aa, r2) == ("invalid", "authority")


# ---- unresolved and rejected events ----

def test_sibling_of_x_and_y_at_the_base_stays_visible_and_blocking(w):
    # README s4 View: unrelated heads beside a reconciled split stay blocking
    z = w.event("Z", "c", w.id("P"), add("carol", "c3"))
    v = w.view(w.P, w.X, w.Y, w.R, z)
    assert v["heads"] == ["R", "Z"]
    assert v["classification"] == "conflict"
    assert v["may_publish"] == RESOLVING


def test_child_of_an_adopted_event_is_not_suppressed(w):
    # README s4 View step 3: only covered events are suppressed; X2 builds on adopted X
    x2 = w.event("X2", "a", w.id("X"), add("alice", "a2"))
    v = w.view(w.P, w.X, w.Y, w.R, x2)
    assert v["status"]["X2"] == "valid"
    assert v["heads"] == ["R", "X2"]
    assert v["classification"] == "conflict"


def test_rejected_member_signing_on_top_of_r_is_invalid(w):
    # README s5: signer must be a device in the state at the parent; b2 is gone after R
    late = w.event("Late", "b2", w.id("R"), add("bob", "b3"))
    v = w.view(w.P, w.X, w.Y, w.R, late)
    assert v["status"]["Late"] == "invalid"
    assert v["heads"] == ["R"]
    assert v["classification"] == "followed"
    assert v["tables"] == R_TABLES


def test_children_of_rejected_events_inside_a_deeper_rejection_are_invalid(w):
    # decision 1: children of events a valid reconciliation rejected are invalid
    y2 = w.event("Y2", "b2", w.id("Y"), add("bob", "b3"))
    c = w.prop("P", ["X", "Y2"], ["X"], ["Y", "Y2"], "a")
    r9 = w.r("R9", c, [w.X, w.Y, y2], "a")
    y3 = w.event("Y3", "b2", w.id("Y2"), add("bob", "b4"))
    v = w.view(w.P, w.X, w.Y, y2, r9, y3)
    assert v["status"]["R9"] == "valid"
    assert v["status"]["Y3"] == "invalid"
    assert v["reason"]["Y3"] == "builds on a rejected event"
    assert v["heads"] == ["R9"]
    assert v["tables"] == R_TABLES


def test_copy_inside_invalid_reconciliation_gets_its_own_disposition(w):
    # decision 1: ancestry passes through a copy held only in an invalid R
    bad_r = w.r("Rbad", w.C, [w.X, w.Y], "b2")  # b2 is not a device at P
    child = w.event("Child", "b2", w.id("Y"), add("bob", "b3"))
    v = w.view(w.P, bad_r, child)
    assert v["status"]["Rbad"] == "invalid"
    assert v["status"]["Y"] == "valid"
    assert v["status"]["Child"] == "valid"
    assert v["heads"] == ["Child", "X"]


def test_copy_inside_valid_reconciliation_gets_its_own_disposition_even_standalone_absent(w):
    # decision (copies carried inside any reconciliation get dispositions)
    v = w.view(w.P, w.R)
    assert v["status"] == valid("P", "X", "Y", "R")


# ---- ancestor tips ----

def r_with_ancestor_tip(w, adopted):
    x2 = w.event("X2", "a", w.id("X"), add("alice", "a2"))
    c = w.prop("P", ["X", "Y", "X2"], adopted, [n for n in ["X", "Y", "X2"] if n not in adopted], "a")
    r = w.r("R3", c, [w.X, w.Y, x2], "a")
    return x2, r


def test_resolved_tip_that_is_an_ancestor_of_another_tip_is_allowed(w):
    # decision 2: tips may be ancestors of other tips
    x2, r = r_with_ancestor_tip(w, ["X", "X2"])
    v = w.view(w.P, w.X, w.Y, x2, r)
    assert v["status"]["R3"] == "valid"
    assert v["heads"] == ["R3"]
    assert v["tables"] == {"alice": ["a", "a2"], "carol": ["c", "c2"]}


def test_adopting_a_child_before_its_parent_fails_replay(w):
    # README s4 check 7: adopted respects ancestry
    x2, r = r_with_ancestor_tip(w, ["X2", "X"])
    assert w.check("R3", w.P, w.X, w.Y, x2, r) == ("invalid", "replay")


# ---- arrival order ----

def story_with_children(w):
    c3 = w.event("C3", "c", w.id("R"), add("carol", "c3"))
    y2 = w.event("Y2", "b2", w.id("Y"), add("bob", "b3"))
    return [w.P, w.X, w.Y, w.R, c3, y2]


def test_every_arrival_order_of_a_whole_story_gives_one_view(w):
    # README s4 (view): arrival order does not matter
    objects = story_with_children(w)
    for order in itertools.permutations(objects):
        v = w.view(*order)
        assert v["status"] == {
            "P": "valid", "X": "valid", "Y": "valid",
            "R": "valid", "C3": "valid", "Y2": "invalid"}
        assert v["reason"]["Y2"] == "builds on a rejected event"
        assert v["heads"] == ["C3"]
        assert v["classification"] == "followed"
        assert v["tables"] == {"alice": ["a"], "carol": ["c", "c2", "c3"]}
        assert v["may_publish"] == ALL


def test_arrival_orders_of_the_second_conflict_story_agree(w):
    # README s4 (view): arrival order does not matter, across a second reconciliation
    ca, aa = second_conflict(w)
    c2 = w.prop("R", ["AA", "CA"], ["CA"], ["AA"], "c")
    r2 = w.r("R2", c2, [aa, ca], "c")
    objects = [w.P, w.X, w.Y, w.R, ca, aa, r2]
    rng = random.Random(7)
    orders = [objects[::-1]] + [rng.sample(objects, len(objects)) for _ in range(150)]
    for order in orders:
        v = w.view(*order)
        assert v["heads"] == ["R2"]
        assert v["classification"] == "followed"
        assert v["tables"] == {"carol": ["c", "c2"]}


# ---- envelope tampering ----

def tampered_reconciliation(w, mutate_copy=None, extra_copy=None):
    """Sign an R over w.C whose embedded bytes are altered.

    The lists are sorted by the IDs of the unaltered objects, so without a
    mutation R is valid; an altered object's own decoding fails first.
    """
    copies = sorted([bytes(w.X), bytes(w.Y)] + ([extra_copy] if extra_copy else []),
                    key=lambda b: encoding.decode_object(b)["id"])
    if mutate_copy:
        i = copies.index(bytes(w.Y))
        copies[i] = mutate_copy(copies[i])
    payload = {
        "tips": sorted(w.C["tips"]), "adopted": w.C["adopted"], "rejected": w.C["rejected"],
        "copies": [c.hex() for c in copies],
    }
    return encoding.make_event(seed("a"), w.g, "reconciliation", w.C["base"], payload, time=TIME)


def test_untampered_control_decodes_and_is_valid(w):
    # the helper itself must build a valid R, or the tamper tests below prove nothing
    control = tampered_reconciliation(w)
    decoded = encoding.decode_object(control)
    assert sorted(w.name[c["id"]] for c in decoded["copies"]) == ["X", "Y"]
    w.named("Rctl", control)
    assert w.check("Rctl", w.P, control) == ("valid", None)


def test_copy_with_altered_label_fails_decoding(w):
    # README s3: a copy's signature covers its body; altered bytes must not decode
    with pytest.raises(encoding.EncodingError, match="bad signature"):
        tampered_reconciliation(w, mutate_copy=lambda b: b.replace(b'"b2"', b'"b3"'))


def test_copy_with_flipped_signature_byte_fails_decoding(w):
    # README s3: signatures verify
    def flip(b):
        i = b.rindex(b'"sig":"') + len(b'"sig":"')
        return b[:i] + (b"0" if b[i:i + 1] != b"0" else b"1") + b[i + 1:]
    with pytest.raises(encoding.EncodingError, match="bad signature"):
        tampered_reconciliation(w, mutate_copy=flip)


def test_copy_with_trailing_whitespace_fails_decoding(w):
    # README s3: re-encoding must equal the received bytes
    with pytest.raises(encoding.EncodingError, match="not canonical bytes"):
        tampered_reconciliation(w, mutate_copy=lambda b: b + b" ")


def test_duplicate_copy_in_a_reconciliation_fails_decoding(w):
    # README s3: each list of encoded objects is sorted by ID, so a duplicate is refused
    with pytest.raises(encoding.EncodingError, match="copies not strictly sorted by ID"):
        tampered_reconciliation(w, extra_copy=bytes(w.Y))


def test_copy_from_another_origin_fails_decoding(w):
    # README s3: copies carry R's origin
    foreign = encoding.make_event(seed("a"), "ff" * 32, "ordinary", w.id("P"), remove("bob"), time=TIME)
    with pytest.raises(encoding.EncodingError, match="copies: wrong origin"):
        tampered_reconciliation(w, extra_copy=foreign)


def test_bundle_with_altered_object_fails_decoding(w):
    # README s3: bundle objects are decoded with the same strict rules
    good = encoding.make_bundle(w.g, w.id("P"), [w.g_bytes, w.P])
    assert encoding.decode_bundle(good)["tip"] == w.id("P")
    # objects are hex strings, so flip a hex digit inside the first object
    i = good.index(b'"objects":["') + len(b'"objects":["') + 40
    flipped = good[:i] + (b"0" if good[i:i + 1] != b"0" else b"1") + good[i + 1:]
    with pytest.raises(encoding.EncodingError):
        encoding.decode_bundle(flipped)


# ---- performance ----

def test_long_linear_chain_applies_each_operation_once(w, monkeypatch):
    # README s2 (view recomputed on demand): cost must grow linearly with history,
    # so one view applies each of the 301 ordinary operations (P and 300 more) exactly once.
    parent, events = w.id("P"), [w.P]
    for i in range(300):
        name = f"k{i}"
        ev = w.event(name, "c", parent, add("carol", name))
        events.append(ev)
        parent = w.id(name)
    calls = []
    real = policy.apply_ordinary
    monkeypatch.setattr(policy, "apply_ordinary", lambda *args: calls.append(1) or real(*args))
    start = time.perf_counter()
    v = view.compute_view(events, w.g_bytes)
    elapsed = time.perf_counter() - start
    assert v["classification"] == "followed"
    assert v["heads"] == [parent]
    assert len(v["acting_state"]["devices"]) == 304
    assert len(calls) == 301
    assert elapsed < 5.0  # generous sanity bound; the call count is the real check


def six_event_split(w):
    a_chain, c_chain = [], []
    parent = w.g
    for i in range(1, 4):
        a_chain.append(w.event(f"A{i}", "a", parent, add("alice", f"a{i}")))
        parent = w.id(f"A{i}")
    parent = w.g
    for i in range(1, 4):
        c_chain.append(w.event(f"C{i}", "c", parent, add("carol", f"c{i}")))
        parent = w.id(f"C{i}")
    return a_chain, c_chain


def test_six_event_split_view_is_quick_and_awaits_join(w):
    # README s5: six branch events are the compatibility limit and still supported
    a_chain, c_chain = six_event_split(w)
    start = time.perf_counter()
    v = w.view(*a_chain, *c_chain)
    elapsed = time.perf_counter() - start
    assert v["classification"] == "split awaiting join"
    assert v["heads"] == ["A3", "C3"]
    assert v["base"] == "G"
    assert v["may_publish"] == ["join"]
    assert elapsed < 5.0


def test_reconciliation_covering_six_events_is_quick(w):
    # README s4 check 7: replay of six covered events
    a_chain, c_chain = six_event_split(w)
    names = ["A1", "A2", "A3", "C1", "C2", "C3"]
    c = w.prop("G", ["A3", "C3"], names, [], "a")
    r = w.r("R6", c, a_chain + c_chain, "a")
    start = time.perf_counter()
    v = w.view(*a_chain, *c_chain, r)
    elapsed = time.perf_counter() - start
    assert v["heads"] == ["R6"]
    assert v["classification"] == "followed"
    assert v["tables"] == {
        "alice": ["a", "a1", "a2", "a3"], "bob": ["b"], "carol": ["c", "c1", "c2", "c3"]}
    assert elapsed < 5.0
