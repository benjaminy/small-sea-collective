"""Validated history: the state after each object, including the reconciliation checks.

`Evaluator` is the one notion of "state at an object". It judges an object only
after its parent: an object built on an invalid or pending object is itself
invalid or pending, and a reconciliation's state comes only from passing the
checks in README section 4. It remembers each result, so one evaluation
applies each ordinary operation once.

`check_reconciliation` returns history's result shape plus a "check" key:
the name of the first failed check ("core", "shape", "base", "partition",
"original", "closure", "replay", "authority"), or None when R is valid.
A valid result carries the state after R.

R's parent is its base, in one of two forms:
- the tips' nearest common ancestor; R covers every event after it on a tip's path;
- one of the tips (an anchored reconciliation, for an event that arrives beside an
  earlier reconciliation). The anchor's history after the nearest common ancestor
  must hold a reconciliation or a join; an anchor that is only ordinary events
  could itself be rejected by a sibling decision, and two such anchors could
  reject each other. R takes the anchor's history as it stands and covers only
  the other tips' events that the anchor's history does not already include.
  An adopted event of a reconciliation or join on that history counts as included;
  a tip whose path holds an event such a reconciliation rejected makes R invalid.
Either way, R replays its adopted events on the base state.

`Evaluator` also judges joins (`_check_join`): unsigned objects that reject
nothing and need no authority, valid when `policy.merge` says the covered
events merge. Its failures use the reconciliation check names, plus "merge".
A join carries no copies, so it is judged only from events the device holds on
their own; with one missing, the join is incomplete.

Check 1 (core) is mostly done before R reaches this module.
Every object in a Lookup comes from `encoding.decode_object`, which refuses
non-canonical bytes and bad signatures, computes IDs itself, and refuses copies
whose origin differs from R's.
What decoding cannot know is the pinned genesis, so the Evaluator compares
every object's origin with it.
"""

from . import history, policy


def _result(check, status, reason=None, missing=(), state=None):
    out = history._result(status, reason, missing, state)
    out["check"] = check
    return out


def _fail(check, reason, status="invalid", missing=()):
    return _result(check, status, reason, missing)


def _inherit(parent, below):
    """The result of an object whose parent's result `below` is not valid."""
    if parent["body"]["kind"] == "reconciliation":
        word = "an invalid" if below["status"] == "invalid" else "a pending"
        return history._result(below["status"], f"built on {word} reconciliation", below["missing"])
    return history._result(below["status"], below["reason"], below["missing"])


class Evaluator:
    """Validated results for the objects in one Lookup, each computed once."""

    def __init__(self, lookup: history.Lookup):
        self.lookup = lookup
        self.memo = {}  # object ID -> result

    def result(self, object_id: str) -> dict:
        """Walk up to an object already judged (or genesis), then judge downward."""
        objects = self.lookup.objects
        if object_id in self.memo:
            return self.memo[object_id]
        if object_id not in objects:
            return history._result("incomplete", "missing object", [object_id])
        path, on_path, current, below = [], set(), object_id, None
        while current not in self.memo:
            if current in on_path:
                below = history._result("invalid", "ancestry has a cycle")
                break
            obj = objects.get(current)
            if obj is None:
                below = history._result("incomplete", "missing ancestor", [current])
                break
            path.append(current)
            on_path.add(current)
            if obj["body"]["kind"] == "genesis":
                break
            current = obj["body"]["parents"][0]
        else:
            below = self.memo[current]
        for oid in reversed(path):
            self.memo[oid] = self._judge(objects[oid], below)
            below = self.memo[oid]
        return self.memo[object_id]

    def _judge(self, obj, below):
        """Judge one object given its parent's result (None for a genesis)."""
        body = obj["body"]
        if body["kind"] == "genesis":
            if obj["id"] != self.lookup.genesis_id:
                return history._result("invalid", "ancestry reaches a different genesis")
            return history._result("valid", state=policy.initial_state(self.lookup.genesis_bytes))
        if body["kind"] == "reconciliation":
            return self._check(obj)
        if body["kind"] == "join":
            return self._check_join(obj)
        if body["origin"] != self.lookup.genesis_id:
            return history._result("invalid", "origin is not the pinned genesis")
        parent = self.lookup.objects.get(body["parents"][0])
        if below["status"] != "valid":
            return _inherit(parent, below) if parent else below
        state = below["state"]
        state, reason = policy.apply_ordinary(state, body["author"], body["payload"])
        if reason:
            return history._result("invalid", reason)
        return history._result("valid", state=state)

    def _check(self, r):
        """The reconciliation checks, in order; the first failure names its check."""
        lookup, body = self.lookup, r["body"]

        # 1. Core
        if body["origin"] != lookup.genesis_id:
            return _fail("core", "origin is not the pinned genesis")

        # 2. Shape
        p = body["payload"]
        tips, adopted, rejected = p["tips"], p["adopted"], p["rejected"]
        if len(set(tips)) < 2 or len(set(tips)) != len(tips):
            return _fail("shape", "fewer than two distinct resolved tips")

        # 3. Base: walk each tip to genesis. The lookup holds R's copies, so
        # parent links are followed through them and then the device's own objects.
        chains = []
        for tip in tips:
            failure, chain = history._chain(lookup, tip)
            if failure:
                return _fail("base", failure["reason"], failure["status"], failure["missing"])
            chains.append([obj["id"] for obj in chain])
        depth = 0  # length of the common prefix; every chain starts at genesis
        while all(len(ch) > depth and ch[depth] == chains[0][depth] for ch in chains):
            depth += 1
        base = body["parents"][0]
        anchored = base != chains[0][depth - 1]
        if anchored and base not in tips:
            return _fail("base", "parent is neither the tips' nearest common ancestor nor a tip")
        if anchored and any(tip in chain for tip in tips for chain in chains if chain[-1] != tip):
            return _fail("shape", "a tip lies on another tip's path")
        if anchored and all(lookup.objects[oid]["body"]["kind"] == "ordinary"
                            for oid in chains[tips.index(base)][depth:]):
            return _fail("base", "the anchor's history holds no reconciliation or join")
        at_base = self.result(base)
        if at_base["status"] != "valid":
            word = "invalid" if at_base["status"] == "invalid" else "pending"
            return _fail("base", f"base is {word}: {at_base['reason']}",
                         at_base["status"], at_base["missing"])
        base_state = at_base["state"]

        # 4. Partition. covered maps each event after the nearest common ancestor on
        # a tip's path to its parent, leaving out what the anchor's history includes.
        # A resolved tip equal to the base contributes an empty path
        # (README section 4: R may name any published tip).
        included = set()
        if anchored:
            included, rejected_there = history_events(lookup, chains[tips.index(base)][depth:])
            if any(eid in rejected_there for ch in chains for eid in ch[depth:]):
                return _fail("partition", "a tip builds on an event the anchor's history rejected")
        covered = {eid: lookup.objects[eid]["body"]["parents"][0]
                   for ch in chains for eid in ch[depth:] if eid not in included}
        copies = {e["id"]: e for e in r["copies"]}
        if set(copies) != set(covered):
            return _fail("partition", "copies are not exactly the covered events")
        if any(e["body"]["kind"] != "ordinary" for e in copies.values()):
            return _fail("partition", "a copy is not an ordinary event")
        listed = set(adopted) | set(rejected)
        if len(adopted) + len(rejected) != len(listed):
            return _fail("partition", "an event is listed twice")
        if listed != set(covered):
            return _fail("partition", "adopted and rejected do not match the covered events")

        # 5. Original validity
        for eid in sorted(copies):
            found = self.result(eid)
            if found["status"] != "valid":
                return _fail("original", f"a copy is invalid at its parent: {found['reason']}",
                             found["status"], found["missing"])

        # 6. Closure. Every covered event is adopted or rejected, so checking each
        # adopted event's parent covers all of its covered ancestors.
        if any(covered[eid] in set(rejected) for eid in adopted):
            return _fail("closure", "an adopted event descends from a rejected event")

        # 7. Replay from the base state, in the order given.
        position = {eid: i for i, eid in enumerate(adopted)}
        if any(position.get(covered[eid], -1) > i for i, eid in enumerate(adopted)):
            return _fail("replay", "an adopted event comes before its parent")
        state = base_state
        for eid in adopted:
            step = copies[eid]["body"]
            if step["kind"] != "ordinary":
                return _fail("replay", "a replay step is not an ordinary event")
            state, reason = policy.apply_ordinary(state, step["author"], step["payload"])
            if reason:
                return _fail("replay", f"replay fails: {reason}")

        # 8. Authority: one member decides, so the signer need only be a device at the base.
        if body["author"] not in base_state["devices"]:
            return _fail("authority", "signer is not a device at the base")

        return _result(None, "valid", state=state)


    def _check_join(self, j):
        """The join checks, named like the reconciliation checks they mirror."""
        lookup, body = self.lookup, j["body"]
        if body["origin"] != lookup.genesis_id:
            return _fail("core", "origin is not the pinned genesis")
        tips = body["payload"]["tips"]
        if len(tips) < 2:
            return _fail("shape", "fewer than two tips")

        chains = []
        for tip in tips:
            failure, chain = history._chain(lookup, tip)
            if failure:
                return _fail("base", failure["reason"], failure["status"], failure["missing"])
            chains.append([obj["id"] for obj in chain])
        if any(tip in chain for tip in tips for chain in chains if chain[-1] != tip):
            return _fail("shape", "a tip lies on another tip's path")
        depth = sum(1 for column in zip(*chains) if len(set(column)) == 1)
        base = chains[0][depth - 1]
        if body["parents"] != [base]:
            return _fail("base", "parent is not the tips' nearest common ancestor")
        at_base = self.result(base)
        if at_base["status"] != "valid":
            word = "invalid" if at_base["status"] == "invalid" else "pending"
            return _fail("base", f"base is {word}: {at_base['reason']}",
                         at_base["status"], at_base["missing"])

        covered = sorted({eid for chain in chains for eid in chain[depth:]})
        if any(lookup.objects[eid]["body"]["kind"] != "ordinary" for eid in covered):
            return _fail("partition", "a covered object is not an ordinary event")
        for eid in covered:
            found = self.result(eid)
            if found["status"] != "valid":
                return _fail("original", f"a covered event is invalid at its parent: {found['reason']}",
                             found["status"], found["missing"])

        state, reason = policy.merge(at_base["state"], [lookup.objects[eid] for eid in covered])
        if reason:
            return _fail("merge", f"events do not merge: {reason}")
        return _result(None, "valid", state=state)


def history_events(lookup: history.Lookup, chain: list) -> tuple[set, set]:
    """What a chain of object IDs includes, and what it rejected.

    Included: the chain's objects, the events its reconciliations adopted, and the
    events its joins covered. Rejected: the events its reconciliations rejected.
    """
    included, rejected = set(), set()
    for object_id in chain:
        included.add(object_id)
        obj = lookup.objects[object_id]
        if obj["body"]["kind"] == "reconciliation":
            included |= set(obj["body"]["payload"]["adopted"])
            rejected |= set(obj["body"]["payload"]["rejected"])
        elif obj["body"]["kind"] == "join":
            included |= set(covered_by_join(lookup, obj))
    return included, rejected


def covered_by_join(lookup: history.Lookup, j: dict) -> list:
    """The events a join covers: everything after its parent on a tip's path."""
    out = set()
    for tip in j["body"]["payload"]["tips"]:
        for obj in reversed(history._chain(lookup, tip)[1]):
            if obj["id"] == j["body"]["parents"][0]:
                break
            out.add(obj["id"])
    return sorted(out)


def check_reconciliation(lookup: history.Lookup, r_id: str) -> dict:
    r = lookup.objects.get(r_id)
    if r is None:
        return _fail("core", "missing object", "incomplete", [r_id])
    if r["body"]["kind"] != "reconciliation":
        return _fail("shape", "not a reconciliation")
    return Evaluator(lookup).result(r_id)
