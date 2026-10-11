"""The reconciliation checks from slice 1 plan section 4, in order.

`check_reconciliation` returns history's result shape plus a "check" key:
the name of the first failed check ("core", "shape", "base", "partition",
"original", "closure", "replay", "authority"), or None when R is valid.
A valid result carries the state after R.

Check 1 (core) is mostly done before R reaches this module.
Every object in a Lookup comes from `encoding.decode_object`, which refuses
non-canonical bytes and bad signatures, computes IDs itself, and refuses copies,
approvals, or a proposal whose origin differs from R's.
What decoding cannot know is the pinned genesis, so check 1 here compares R's
origin with it.
"""

from . import encoding, history, policy


def _result(check, status, reason=None, missing=(), state=None):
    out = history._result(status, reason, missing, state)
    out["check"] = check
    return out


def _fail(check, reason, status="invalid", missing=()):
    return _result(check, status, reason, missing)


def check_reconciliation(lookup: history.Lookup, r_id: str) -> dict:
    r = lookup.objects.get(r_id)
    if r is None:
        return _fail("core", "missing object", "incomplete", [r_id])
    body = r["body"]

    # 1. Core
    if body.get("origin") != lookup.genesis_id:
        return _fail("core", "origin is not the pinned genesis")

    # 2. Shape
    if body["kind"] != "reconciliation":
        return _fail("shape", "not a reconciliation")
    c = body["payload"]["proposal"]
    tips, adopted, rejected = c["resolved_tips"], c["adopted"], c["rejected"]
    if body["author"] != c["signer"]:
        return _fail("shape", "author is not the proposal's signer")
    if body["parents"] != [c["base"]]:
        return _fail("shape", "parent is not the proposal's base")
    if len(set(tips)) < 2 or len(set(tips)) != len(tips):
        return _fail("shape", "fewer than two distinct resolved tips")

    # 3. Base: walk each tip to genesis through R's copies, then the lookup.
    copies = {e["id"]: e for e in r["copies"]}
    ext = history.Lookup({**lookup.objects, **copies}, lookup.genesis_id, lookup.genesis_bytes)
    chains = []
    for tip in tips:
        failure, chain = history._chain(ext, tip)
        if failure:
            return _fail("base", failure["reason"], failure["status"], failure["missing"])
        chains.append([obj["id"] for obj in chain])
    depth = 0  # length of the common prefix; every chain starts at genesis
    while all(len(ch) > depth and ch[depth] == chains[0][depth] for ch in chains):
        depth += 1
    base = chains[0][depth - 1]
    if base != c["base"]:
        return _fail("base", "declared base is not the nearest common ancestor")
    at_base = history.state_at(ext, base)
    if at_base["status"] != "valid":
        return _fail("base", f"base is not valid: {at_base['reason']}",
                     at_base["status"], at_base["missing"])
    base_state = at_base["state"]

    # 4. Partition. covered maps each event after the base on a tip's path to its parent.
    covered = {eid: ext.objects[eid]["body"]["parents"][0] for ch in chains for eid in ch[depth:]}
    if any(tip not in covered for tip in tips):
        return _fail("partition", "a resolved tip is not after the base")
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
        result = history.validate_original(ext, eid)
        if result["status"] != "valid":
            return _fail("original", f"a copy is invalid at its parent: {result['reason']}",
                         result["status"], result["missing"])

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
