"""Micro tests for one device's store: receipt, restart, choices, publish guard.

Story: G lists Alice (a), Bob (b), Carol (c); P = Carol adds c2;
X = Alice removes Bob (parent P); Y = Bob adds b2 (parent P);
R is Alice's reconciliation "adopt X, reject Y", published by device a.
Expected values are literals; object IDs are translated to story names.
"""

import hashlib
import json

import pytest

from small_sea_manager.spine import encoding, history, reconcile
from small_sea_manager.spine.device import Device, Refused

TIME = 1_760_000_000

NAMES = ["a", "b", "c", "c2", "b2", "c3", "a2", "c4"]
SEEDS = {name: bytes([n]) * 32 for n, name in enumerate(NAMES, 1)}
KEY = {name: encoding.public_key(seed) for name, seed in SEEDS.items()}

ALL = ["ordinary", "reconciliation"]
RESOLVING = ["reconciliation"]
P_TABLES = {"alice": ["a"], "bob": ["b"], "carol": ["c", "c2"]}
Y_TABLES = {"alice": ["a"], "bob": ["b", "b2"], "carol": ["c", "c2"]}
R_TABLES = {"alice": ["a"], "carol": ["c", "c2"]}
OK = ("valid", None)


def oid(encoded):
    return encoding.decode_object(encoded)["id"]


def add(member, name):
    return {"op": "add_device", "member_id": member, "device_key": KEY[name], "label": name}


def tables(state):
    out = {member: [] for member in state["members"]}
    for row in state["devices"].values():
        out[row["member_id"]].append(row["label"])
    return {member: sorted(labels) for member, labels in out.items()}


def genesis(nonce="00"):
    return encoding.make_genesis(nonce * 32, [
        {"member_id": "alice", "name": "Alice"},
        {"member_id": "bob", "name": "Bob"},
        {"member_id": "carol", "name": "Carol"},
    ], [
        {"device_key": KEY["a"], "member_id": "alice", "label": "a"},
        {"device_key": KEY["b"], "member_id": "bob", "label": "b"},
        {"device_key": KEY["c"], "member_id": "carol", "label": "c"},
    ], time=TIME)


# ---- a second valid signature over the same body (RFC 8032, chosen nonce) ----

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_BY = 4 * pow(5, _P - 2, _P) % _P


def _recover_x(y):
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return _P - x if x & 1 else x


_B = (_recover_x(_BY), _BY, 1, _recover_x(_BY) * _BY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p, s = _add(p, p), s >> 1
    return q


def _encode_point(p):
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def resign(seed, encoded, nonce=7):
    """The same event with a different, valid signature."""
    env = json.loads(encoded)
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a = (a & ((1 << 254) - 8)) | (1 << 254)
    message = b"spine-v1 sig\0" + encoding.canonical(env["body"])
    r_point = _encode_point(_mul(nonce, _B))
    k = int.from_bytes(hashlib.sha512(
        r_point + bytes.fromhex(env["body"]["author"]) + message).digest(), "little")
    env["sig"] = (r_point + ((nonce + k * a) % _L).to_bytes(32, "little")).hex()
    out = encoding.canonical(env)
    encoding.decode_event(out)  # still valid
    assert out != encoded
    return out


# ---- the world ----

class World:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.name = {}
        self.g_bytes = genesis()
        self.g = self.named("G", self.g_bytes)
        self.P = self.event("P", "c", self.g, add("carol", "c2"))
        self.X = self.event("X", "a", self.id("P"), {"op": "remove_member", "member_id": "bob"})
        self.Y = self.event("Y", "b", self.id("P"), add("bob", "b2"))
        payload = encoding.make_reconciliation_payload(
            [self.id("X"), self.id("Y")], [self.id("X")], [self.id("Y")], [self.X, self.Y])
        self.R = self.event("R", "a", self.id("P"), payload, "reconciliation")

    def named(self, name, encoded):
        self.name[oid(encoded)] = name
        return oid(encoded)

    def id(self, name):
        return next(k for k, v in self.name.items() if v == name)

    def event(self, name, seed, parent, payload, kind="ordinary"):
        encoded = encoding.make_event(SEEDS[seed], self.g, kind, parent, payload, time=TIME)
        self.named(name, encoded)
        return encoded

    def device(self, label, *objects, file=None):
        dev = Device.create(self.tmp_path / (file or f"{label}.db"), SEEDS[label], self.g_bytes)
        for encoded in objects:
            dev.receive(encoded)
        return dev

    def reopen(self, dev):
        path = dev.engine.url.database
        dev.close()
        return Device(path)

    def summary(self, v):
        n = lambda ids: sorted(self.name.get(i, "?") for i in ids)
        return {
            "objects": {self.name.get(i, "?"): (d["status"], d["reason"])
                        for i, d in v["objects"].items()},
            "heads": n(v["heads"]),
            "classification": v["classification"],
            "tables": tables(v["acting_state"]),
            "may_publish": v["may_publish"],
            "contested": n(v["contested"]),
        }

    def view(self, dev):
        return self.summary(dev.view())

    def receipt(self, r):
        return (self.name.get(r["object_id"], r["object_id"]), r["disposition"], r["reason"])


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


CONFLICT = {"objects": {"P": OK, "X": OK, "Y": OK}, "heads": ["X", "Y"],
            "classification": "conflict", "tables": P_TABLES, "may_publish": RESOLVING,
            "contested": []}


# ---- receipt ----

def test_receipts_record_each_arrival(w):
    c = w.device("c")
    got = [w.receipt(c.receive(e)) for e in (w.Y, w.P, w.X, w.X)]
    assert got == [("Y", "pending", "missing ancestor"), ("P", "valid", None),
                   ("X", "valid", None), ("X", "duplicate", None)]
    assert [w.receipt(r) for r in c.receipts()] == got


def test_garbage_foreign_genesis_and_wrong_origin_are_refused(w):
    c = w.device("c")
    other = genesis("11")
    stray = encoding.make_event(SEEDS["c"], oid(other), "ordinary", oid(other),
                                add("carol", "c2"), time=TIME)
    assert c.receive(b"not json")["disposition"] == "refused"
    assert c.receive(other)["reason"] == "not the pinned genesis"
    assert c.receive(stray)["reason"] == "origin is not the pinned genesis"
    assert c.receive(w.g_bytes)["disposition"] == "duplicate"
    assert w.view(c)["objects"] == {}


def test_a_different_envelope_for_a_known_id_is_refused(w):
    c = w.device("c", w.P, w.Y)
    assert w.receipt(c.receive(resign(SEEDS["b"], w.Y))) == ("Y", "refused", "envelope conflict")
    assert c.get(w.id("Y")) == w.Y


def test_a_copy_with_a_different_envelope_is_logged_and_r_still_counts(w):
    other_y = resign(SEEDS["b"], w.Y)
    payload = encoding.make_reconciliation_payload(
        [w.id("X"), w.id("Y")], [w.id("X")], [w.id("Y")], [w.X, other_y])
    r_other = w.event("R'", "a", w.id("P"), payload, "reconciliation")
    a = w.device("a", w.P, w.X, w.Y)
    assert w.receipt(a.receive(r_other)) == ("R'", "valid", None)
    assert [w.receipt(r) for r in a.receipts()][-2:] == [
        ("Y", "refused", f"envelope conflict (copy inside {w.id("R'")})"),
        ("R'", "valid", None)]
    assert a.get(w.id("Y")) == w.Y
    with pytest.raises(Refused, match="same envelope"):
        a.forget_standalone(w.id("Y"))
    assert a.get(w.id("Y")) == w.Y
    assert a.receive(w.Y)["disposition"] == "duplicate"
    a.receive(w.R)
    a.forget_standalone(w.id("Y"))
    a = w.reopen(a)
    assert a.get(w.id("Y")) == w.Y
    assert a.receive(w.Y)["disposition"] == "duplicate"


def test_first_carried_envelope_wins_even_when_later_container_sorts_first(w):
    other_y = resign(SEEDS["b"], w.Y)
    payload = encoding.make_reconciliation_payload(
        [w.id("X"), w.id("Y")], [w.id("X")], [w.id("Y")], [w.X, other_y])
    other_r = w.event("Rother", "a", w.id("P"), payload, "reconciliation")
    first, later = sorted([w.R, other_r], key=oid, reverse=True)
    expected_y = next(bytes.fromhex(cp) for cp in json.loads(first)["body"]["payload"]["copies"]
                      if oid(bytes.fromhex(cp)) == w.id("Y"))
    c = w.device("c", w.P, first)
    assert c.get(w.id("Y")) == expected_y
    c.receive(later)
    c = w.reopen(c)
    assert c.get(w.id("Y")) == expected_y
    assert c.receive(expected_y)["disposition"] == "duplicate"


def test_arrival_order_does_not_change_the_view(w):
    one = w.device("c", w.X, w.Y, w.P, file="one.db")
    two = w.device("c", w.Y, w.P, w.X, w.X, file="two.db")
    assert w.view(one) == w.view(two) == CONFLICT


# ---- restart and the remembered choices ----

def test_restart_during_the_pause_gives_the_same_view(w):
    c = w.device("c", w.P, w.Y, w.X)
    before = w.view(c)
    assert before == CONFLICT
    assert w.view(w.reopen(c)) == before


def test_followed_head_survives_restart_and_makes_the_owner_choose(w):
    c = w.device("c", w.P, w.Y)
    assert w.view(c)["classification"] == "followed"
    c = w.reopen(c)
    assert w.receipt(c.receive(w.X)) == ("X", "valid", None)
    c.receive(w.R)
    assert w.view(c) == {
        "objects": {"P": OK, "X": OK, "Y": OK, "R": OK}, "heads": ["R"],
        "classification": "owner must choose", "tables": Y_TABLES,
        "may_publish": RESOLVING, "contested": ["R"]}
    assert w.view(w.reopen(c))["contested"] == ["R"]


def test_arrival_alone_records_the_followed_head(w):
    c = w.device("c", w.P, w.Y, w.X, w.R)  # nobody asks for a view in between
    assert w.name[c.followed()] == "Y"
    assert w.view(c)["classification"] == "owner must choose"


def test_published_lists_what_this_device_published_in_order(w):
    c = w.device("c", w.P, w.X, w.Y, w.R)
    c3 = c.publish(add("carol", "c3"))
    c4 = c.publish(add("carol", "c4"))
    assert c.published() == [c3, c4]
    assert w.reopen(c).published() == [c3, c4]


def test_accepting_follows_r_and_survives_restart(w):
    c = w.device("c", w.P, w.Y)
    c.view()
    c.receive(w.X)
    c.receive(w.R)
    c.accept(w.id("R"))
    expected = {"objects": {"P": OK, "X": OK, "Y": OK, "R": OK}, "heads": ["R"],
                "classification": "followed", "tables": R_TABLES, "may_publish": ALL,
                "contested": []}
    assert w.view(c) == expected
    c = w.reopen(c)
    assert w.view(c) == expected
    assert w.name[c.followed()] == "R"


def test_refusing_returns_to_the_conflict_and_survives_restart(w):
    b = w.device("b", w.P, w.Y)
    b.view()
    b.receive(w.X)
    b.receive(w.R)
    b.refuse(w.id("R"))
    expected = dict(CONFLICT, objects={"P": OK, "X": OK, "Y": OK,
                                       "R": ("invalid", "refused by this device's owner")})
    assert w.view(b) == expected
    assert w.view(w.reopen(b)) == expected


def test_only_a_contested_reconciliation_can_be_accepted_or_refused(w):
    a = w.device("a", w.P, w.X, w.Y, w.R)
    with pytest.raises(Refused):
        a.accept(w.id("R"))
    with pytest.raises(Refused):
        a.refuse(w.id("X"))


# ---- publishing ----

def test_paused_device_refuses_an_ordinary_event(w):
    c = w.device("c", w.P, w.Y, w.X)
    with pytest.raises(Refused, match="paused: conflict"):
        c.publish(add("carol", "c3"))
    assert w.view(c) == CONFLICT


def test_publish_refuses_an_operation_invalid_at_the_head(w):
    c = w.device("c", w.P)
    with pytest.raises(Refused, match="would not be valid here"):
        c.publish(add("bob", "c3"))
    assert w.view(c)["heads"] == ["P"]


def test_publishing_r_gathers_the_copies_and_follows_it(w):
    a = w.device("a", w.P, w.X, w.Y)
    r = a.publish_reconciliation([w.id("X"), w.id("Y")], [w.id("X")], [w.id("Y")])
    w.named("R", r)
    body = encoding.decode_event(r)
    assert w.name[body["body"]["parents"][0]] == "P"
    assert sorted(w.name[cp["id"]] for cp in body["copies"]) == ["X", "Y"]
    assert w.view(a) == {"objects": {"P": OK, "X": OK, "Y": OK, "R": OK}, "heads": ["R"],
                         "classification": "followed", "tables": R_TABLES,
                         "may_publish": ALL, "contested": []}
    c = w.device("c", w.P, w.Y)
    c.view()
    c.receive(w.X)
    assert w.receipt(c.receive(r)) == ("R", "valid", None)
    assert w.view(c)["classification"] == "owner must choose"


def test_paused_device_may_publish_a_reconciliation(w):
    c = w.device("c", w.P, w.Y)
    c.view()
    assert c.followed() == w.id("Y")
    c.receive(w.X)
    r = c.publish_reconciliation([w.id("X"), w.id("Y")], [w.id("X")], [w.id("Y")])
    w.named("Rc", r)
    assert w.view(c)["heads"] == ["Rc"]
    assert w.view(c)["classification"] == "followed"


def test_publishing_an_invalid_reconciliation_is_refused(w):
    a = w.device("a", w.P, w.X, w.Y)
    with pytest.raises(Refused, match="would not be valid here"):
        a.publish_reconciliation([w.id("X"), w.id("Y")], [w.id("X"), w.id("Y")], [])
    assert w.view(a) == CONFLICT


@pytest.mark.parametrize("tip_names, anchor_name", [
    ([], None), (["X"], None), (["X", "X"], None),
    (["X", "missing"], None), (["X", "Y"], "P"),
])
def test_reconciliation_draft_refuses_bad_tips_without_changing_store(w, tip_names, anchor_name):
    a = w.device("a", w.P, w.X, w.Y)
    tips = ["ff" * 32 if name == "missing" else w.id(name) for name in tip_names]
    with pytest.raises(Refused):
        a.publish_reconciliation(tips, [], [], anchor=w.id(anchor_name) if anchor_name else None)
    assert w.view(a) == CONFLICT


def test_an_event_on_r_is_followed_by_its_receiver(w):
    c = w.device("c", w.P, w.X, w.Y, w.R)
    c3 = c.publish(add("carol", "c3"))
    w.named("c3", c3)
    a = w.device("a", w.P, w.X, w.Y, w.R, c3)
    assert w.view(a)["heads"] == ["c3"]
    assert w.name[a.followed()] == "c3"
    assert w.view(a)["tables"] == {"alice": ["a"], "carol": ["c", "c2", "c3"]}


def test_publishing_the_join_follows_it(w):
    a2 = w.event("a2", "a", w.id("P"), add("alice", "a2"))
    c3 = w.event("c3", "c", w.id("P"), add("carol", "c3"))
    c = w.device("c", w.P, a2, c3)
    assert w.view(c)["classification"] == "split awaiting join"
    with pytest.raises(Refused, match="paused"):
        c.publish(add("carol", "c4"))
    join = c.publish_join()
    w.named("J", join)
    assert w.name[c.followed()] == "J"
    assert w.view(c)["heads"] == ["J"]
    assert w.view(c)["classification"] == "followed"


# ---- retained copies and export ----

def test_forgotten_standalones_resolve_from_r_after_restart(w):
    c = w.device("c", w.P, w.X, w.Y, w.R)
    before = w.view(c)
    c.forget_standalone(w.id("X"))
    c.forget_standalone(w.id("Y"))
    c = w.reopen(c)
    assert w.view(c) == before
    assert c.get(w.id("Y")) == w.Y
    with pytest.raises(Refused):
        c.forget_standalone(w.id("P"))


def test_export_bundle_holds_the_chain_to_genesis_and_no_key(w):
    c = w.device("c", w.P, w.X, w.Y, w.R)
    bundle = c.export_bundle()
    decoded = encoding.decode_bundle(bundle)
    assert w.name[decoded["tip"]] == "R"
    assert sorted(w.name[o["id"]] for o in decoded["objects"]) == ["G", "P", "R"]
    for h in json.loads(bundle)["objects"]:
        assert SEEDS["c"].hex() not in bytes.fromhex(h).decode("ascii")


def test_export_needs_a_tip_with_several_heads(w):
    c = w.device("c", w.P, w.X, w.Y)
    with pytest.raises(Refused, match="several heads"):
        c.export_bundle()
    assert encoding.decode_bundle(c.export_bundle(w.id("X")))["tip"] == w.id("X")


@pytest.mark.parametrize("continuation, expected_tables", [
    ("join", {"alice": ["a", "a2"], "bob": ["b"], "carol": ["c", "c2", "c3"]}),
    ("ordinary", {"alice": ["a", "a2", "c4"], "bob": ["b"], "carol": ["c", "c2", "c3"]}),
    ("reconciliation", {"alice": ["a", "a2"], "bob": ["b"], "carol": ["c", "c2", "c3"]}),
])
def test_export_includes_evidence_for_joins_in_the_history(w, continuation, expected_tables):
    a2 = w.event("a2", "a", w.id("P"), add("alice", "a2"))
    c3 = w.event("c3", "c", w.id("P"), add("carol", "c3"))
    a = w.device("a", w.P, a2, c3)
    join = a.publish_join()
    if continuation == "ordinary":
        a.publish(add("alice", "c4"))
    elif continuation == "reconciliation":
        cut = w.event("cut", "b", w.id("P"), {"op": "remove_member", "member_id": "carol"})
        a.receive(cut)
        a.publish_reconciliation([oid(join), oid(cut)], [], [oid(cut)], anchor=oid(join))
    bundle = json.loads(a.export_bundle())
    objects = [bytes.fromhex(h) for h in bundle["objects"]]
    result = reconcile.Evaluator(history.build_lookup(objects, w.g_bytes)).result(bundle["tip"])
    assert result["status"] == "valid"
    assert tables(result["state"]) == expected_tables


def test_publishing_an_anchored_reconciliation_settles_a_late_event(w):
    late = w.event("c3", "c", w.id("P"), add("carol", "c3"))
    a = w.device("a", w.P, w.X, w.Y, w.R, late)
    assert w.view(a)["heads"] == ["R", "c3"]
    r2 = a.publish_reconciliation([w.id("R"), w.id("c3")], [w.id("c3")], [], anchor=w.id("R"))
    w.named("R2", r2)
    decoded = encoding.decode_event(r2)
    assert w.name[decoded["body"]["parents"][0]] == "R"
    assert [w.name[cp["id"]] for cp in decoded["copies"]] == ["c3"]
    assert w.view(a)["heads"] == ["R2"]
    assert w.view(a)["tables"] == {"alice": ["a"], "carol": ["c", "c2", "c3"]}
