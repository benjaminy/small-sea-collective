"""Validated history: the state after each object, including the reconciliation checks.

`Evaluator` is the one notion of "state at an object". It judges an object only
after its parent: an object built on an invalid or pending object is itself
invalid or pending, and a reconciliation's state comes only from passing the
checks in slice 1 plan section 4. It remembers each result, so one evaluation
applies each ordinary operation once.

`check_reconciliation` returns history's result shape plus a "check" key:
the name of the first failed check ("core", "shape", "base", "partition",
"original", "closure", "replay", "authority"), or None when R is valid.
A valid result carries the state after R.

Check 1 (core) is mostly done before R reaches this module.
Every object in a Lookup comes from `encoding.decode_object`, which refuses
non-canonical bytes and bad signatures, computes IDs itself, and refuses copies,
approvals, or a proposal whose origin differs from R's.
What decoding cannot know is the pinned genesis, so the Evaluator compares
every object's origin with it.
"""

from . import encoding, history, policy


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
        if body["origin"] != self.lookup.genesis_id:
            return history._result("invalid", "origin is not the pinned genesis")
        parent = self.lookup.objects.get(body["parents"][0])
        if parent is not None and parent["body"]["kind"] == "approval":
            return history._result("invalid", "parent is an approval")
        if below["status"] != "valid":
            return _inherit(parent, below) if parent else below
        state = below["state"]
        if body["kind"] == "approval":
            if not policy.approval_valid(state, body["author"]):
                return history._result("invalid", "signer is not a device")
            return history._result("valid", state=state)
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
        c = body["payload"]["proposal"]
        tips, adopted, rejected = c["resolved_tips"], c["adopted"], c["rejected"]
        if body["author"] != c["signer"]:
            return _fail("shape", "author is not the proposal's signer")
        if body["parents"] != [c["base"]]:
            return _fail("shape", "parent is not the proposal's base")
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
        base = chains[0][depth - 1]
        if base != c["base"]:
            return _fail("base", "declared base is not the nearest common ancestor")
        at_base = self.result(base)
        if at_base["status"] != "valid":
            word = "invalid" if at_base["status"] == "invalid" else "pending"
            return _fail("base", f"base is {word}: {at_base['reason']}",
                         at_base["status"], at_base["missing"])
        base_state = at_base["state"]

        # 4. Partition. covered maps each event after the base on a tip's path to its parent.
        # A resolved tip equal to the base contributes an empty path
        # (decision 2 in after-t3-decisions.md: R may name any published tip).
        covered = {eid: lookup.objects[eid]["body"]["parents"][0]
                   for ch in chains for eid in ch[depth:]}
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

        # 8. Authority, judged entirely by the base state.
        approvals = r["approvals"]
        if any(a["body"]["parents"] != [base] for a in approvals):
            return _fail("authority", "an approval does not parent the base")
        if body["author"] not in base_state["devices"]:
            return _fail("authority", "signer is not a device at the base")
        digest = encoding.proposal_digest(c)
        if not policy.reconciliation_authorized(base_state, base, body["author"], digest, approvals):
            return _fail("authority", "approvals do not reach quorum")

        return _result(None, "valid", state=state)


def check_reconciliation(lookup: history.Lookup, r_id: str) -> dict:
    r = lookup.objects.get(r_id)
    if r is None:
        return _fail("core", "missing object", "incomplete", [r_id])
    if r["body"]["kind"] != "reconciliation":
        return _fail("shape", "not a reconciliation")
    return Evaluator(lookup).result(r_id)
