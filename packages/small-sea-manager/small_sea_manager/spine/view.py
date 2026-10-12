"""A device's view of the Constitution, recomputed from the objects it holds.

Pure: `compute_view` takes encoded objects, the pinned genesis, and two facts the
device's Manager keeps (what the device last followed, and what its owner refused),
and stores nothing.
It follows README section 4 in four separate steps, so that suppression
and head selection never depend on each other:
1. validate every held object;
2. suppress the events covered by every valid reconciliation or join, every
   join whose events another valid join also covers, with more, and every join
   whose events a valid reconciliation already decided;
3. pick heads among valid, unsuppressed objects;
4. classify the heads and decide what this device's Manager may publish.

A device never follows a reconciliation on its own if that reconciliation rejects
an event the device already followed (README section 1). The view says
"owner must choose" and names those reconciliations. The owner accepts one by
following it, which the Manager records as the new `followed`, or refuses it,
which the Manager records in `refused`. A refused reconciliation is invalid on
this device, and so is everything built on it, so the device no longer sees that
continuation of the team. Only reconciliations can be refused.

With more than one head, Constitution publishing is paused: the Manager publishes
no ordinary events until the heads are resolved. Apps are not affected.
When the heads' events merge, the view builds the join that resolves them, and
every device holding the same heads builds the same one. A join among the heads
counts as its tips, so a late child of a joined event leads to a larger join.

A device holds every object it stored and every copy carried inside
a stored reconciliation, valid or not; each gets a disposition as if received alone.
An object that builds on an event rejected by a valid reconciliation is invalid
(owner decision, README section 6), so it is kept as evidence but never blocks.
So is a join that covers such an event.
"""

from . import encoding, history, policy, reconcile

ALL_KINDS = ["ordinary", "reconciliation"]
RESOLVING_KINDS = ["reconciliation"]


def _view(objects, heads, classification, base, state, may_publish, join=None, contested=()):
    return {
        "objects": objects,
        "heads": heads,
        "classification": classification,
        "base": base,
        "acting_state": state,
        "may_publish": may_publish,
        "conflicting_heads": heads if classification == "conflict" else [],
        "join": join,
        "contested": list(contested),
    }


def compute_view(encoded_objects, genesis_bytes: bytes, followed=None, refused=()) -> dict:
    """Return the view as a plain dict.

    followed: the ID of the last head this device followed (a "followed" view's
              head, or the join it published), or None when it has followed
              nothing yet. The device holds it and its whole ancestry, and it was
              valid when followed. The Manager must not move it to a conflict's
              base: a later reconciliation that rejects what the device followed
              would then be followed without the owner choosing.
    refused:  IDs of reconciliations this device's owner refused; other IDs are ignored.

    objects:           held object ID -> {"status": "valid" | "invalid" | "pending", "reason", "missing"}
    heads:             sorted head IDs
    classification:    "followed", "conflict", "split awaiting join",
                       "too many to check automatically" (the events may not
                       interact, but there are more than policy.merge checks), or
                       "owner must choose" (see contested)
    base:              nearest common ancestor of the heads, or None with one head
    acting_state:      state tables the device acts on: the head's; the join's while
                       a split awaits one; followed's while the owner must choose;
                       otherwise the base's
    may_publish:       sorted kinds the Manager may publish to the Constitution
    conflicting_heads: the heads when classification is "conflict", else []
    join:              when a split awaits a join, the encoded join to publish, else None
    contested:         sorted IDs of the reconciliations on the heads' history that
                       reject an event followed's history includes (see
                       reconcile.history_events); non-empty exactly when the owner
                       must choose
    """
    lookup = history.build_lookup(encoded_objects, genesis_bytes)
    held = sorted(set(lookup.objects) - {lookup.genesis_id})

    def kind(object_id):
        return lookup.objects[object_id]["body"]["kind"]

    def parent(object_id):
        return lookup.objects[object_id]["body"].get("parents", [None])[0]

    rejecters = {}  # event ID -> held reconciliations that list it as rejected
    for object_id in held:
        if kind(object_id) == "reconciliation":
            for rejected in lookup.objects[object_id]["body"]["payload"]["rejected"]:
                rejecters.setdefault(rejected, []).append(object_id)

    evaluator = reconcile.Evaluator(lookup)
    memo = {}
    tainted = {}  # object ID -> whether a valid reconciliation rejected it or one of its ancestors
    under_refused = {}  # object ID -> whether it or one of its ancestors is a refused reconciliation

    def disposition(object_id):
        """The Evaluator's result, except that a refused reconciliation, an object
        building on one, and an object building on a rejected event are invalid.

        Recursion ends: a valid reconciliation rejects only events after its base,
        so judging it looks only at objects closer to genesis than the rejected event.
        """
        if object_id not in memo:
            result = evaluator.result(object_id)
            if (result["status"] == "valid" and kind(object_id) == "reconciliation"
                    and object_id in refused):
                result = history._result("invalid", "refused by this device's owner")
            elif (result["status"] == "valid" and kind(object_id) != "genesis"
                    and refused_here_or_before(parent(object_id))):
                result = history._result("invalid", "builds on a refused reconciliation")
            elif (result["status"] == "valid" and kind(object_id) != "genesis"
                    and rejected_here_or_before(parent(object_id))):
                result = history._result("invalid", "builds on a rejected event")
            elif (result["status"] == "valid" and kind(object_id) == "join"
                    and any(rejected_here_or_before(e)
                            for e in reconcile.covered_by_join(lookup, lookup.objects[object_id]))):
                result = history._result("invalid", "covers a rejected event")
            memo[object_id] = result
        return memo[object_id]

    def rejected_here_or_before(object_id):
        """Walk up to an object already known (or genesis), then fill in downward.

        Called only for ancestors of valid objects, so every parent is held.
        """
        path, current = [], object_id
        while current is not None and current not in tainted:
            path.append(current)
            current = parent(current)
        found = tainted.get(current, False)
        for oid in reversed(path):
            found = found or any(disposition(r)["status"] == "valid" for r in rejecters.get(oid, []))
            tainted[oid] = found
        return found

    def refused_here_or_before(object_id):
        """Like rejected_here_or_before, for the owner's refusals."""
        path, current = [], object_id
        while current is not None and current not in under_refused:
            path.append(current)
            current = parent(current)
        found = under_refused.get(current, False)
        for oid in reversed(path):
            found = found or (oid in refused and kind(oid) == "reconciliation")
            under_refused[oid] = found
        return found

    # 1. Validate.
    objects = {}
    for object_id in held:
        result = disposition(object_id)
        status = "pending" if result["status"] == "incomplete" else result["status"]
        objects[object_id] = {"status": status, "reason": result["reason"],
                              "missing": result["missing"]}
    valid = {object_id for object_id in held if objects[object_id]["status"] == "valid"}

    # 2. Suppress the covered events of every valid reconciliation and join;
    # every join whose events another valid join covers, with more; and every
    # join whose events a valid reconciliation already decided (a mechanical
    # restatement of a person's decision must not reopen it).
    suppressed = set()
    join_covers, r_covers = {}, []
    for object_id in valid:
        if kind(object_id) == "reconciliation":
            payload = lookup.objects[object_id]["body"]["payload"]
            r_covers.append(set(payload["adopted"]) | set(payload["rejected"]))
            suppressed |= r_covers[-1]
        elif kind(object_id) == "join":
            join_covers[object_id] = set(reconcile.covered_by_join(lookup, lookup.objects[object_id]))
            suppressed |= join_covers[object_id]
    for small, events in join_covers.items():
        if (any(events < other for other in join_covers.values())
                or any(events <= other for other in r_covers)):
            suppressed.add(small)

    # 3. Heads: valid, unsuppressed, and no valid child.
    parents = {parent(o) for o in valid}
    candidates = (valid | {lookup.genesis_id}) - suppressed
    heads = sorted(o for o in candidates if o not in parents)

    # 4. Classify.
    chains = [[obj["id"] for obj in history._chain(lookup, head)[1]] for head in heads]
    # Every chain starts at genesis; once two chains differ they never meet again.
    depth = sum(1 for column in zip(*chains) if len(set(column)) == 1)
    base = chains[0][depth - 1] if len(heads) > 1 else None
    contested = set()
    if followed is not None:
        seen, _ = reconcile.history_events(
            lookup, [obj["id"] for obj in history._chain(lookup, followed)[1]])
        for object_id in {o for chain in chains for o in chain}:
            if (kind(object_id) == "reconciliation"
                    and seen & set(lookup.objects[object_id]["body"]["payload"]["rejected"])):
                contested.add(object_id)
    if contested:
        state = evaluator.result(followed)["state"]
        return _view(objects, heads, "owner must choose", base, state, RESOLVING_KINDS,
                     contested=sorted(contested))
    if len(heads) == 1:
        state = disposition(heads[0])["state"]
        return _view(objects, heads, "followed", None, state, ALL_KINDS)
    base_state = disposition(base)["state"]
    tips = _join_tips(lookup, heads)
    tip_chains = [[obj["id"] for obj in history._chain(lookup, tip)[1]] for tip in tips]
    tip_depth = sum(1 for column in zip(*tip_chains) if len(set(column)) == 1)
    tip_base = tip_chains[0][tip_depth - 1]
    branch = sorted({object_id for chain in tip_chains for object_id in chain[tip_depth:]})
    if len(tips) < 2 or any(kind(object_id) != "ordinary" for object_id in branch):
        return _view(objects, heads, "conflict", base, base_state, RESOLVING_KINDS)
    merged, reason = policy.merge(disposition(tip_base)["state"],
                                  [lookup.objects[o] for o in branch])
    if reason == policy.TOO_MANY:
        return _view(objects, heads, "too many to check automatically", base, base_state,
                     RESOLVING_KINDS)
    if reason:
        return _view(objects, heads, "conflict", base, base_state, RESOLVING_KINDS)
    # The join is valid by construction, so the device acts on its state at once.
    join = encoding.make_join(lookup.genesis_id, tip_base, tips)
    return _view(objects, heads, "split awaiting join", base, merged, ["join"], join)


def _join_tips(lookup, heads):
    """The tips a join of these heads names: each join head counts as its own tips,
    and a tip that lies on another tip's path is dropped."""
    tips, pending = set(), list(heads)
    while pending:
        head = pending.pop()
        body = lookup.objects[head]["body"]
        if body["kind"] == "join":
            pending.extend(body["payload"]["tips"])
        else:
            tips.add(head)
    paths = {tip: {obj["id"] for obj in history._chain(lookup, tip)[1]} for tip in tips}
    return sorted(t for t in tips if not any(t in paths[o] for o in tips if o != t))
