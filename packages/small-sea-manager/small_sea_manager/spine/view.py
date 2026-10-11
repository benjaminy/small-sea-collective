"""A device's view of the Constitution, recomputed from the objects it holds.

Pure: `compute_view` takes encoded objects and the pinned genesis, and stores nothing.
It follows slice 1 plan section 4 in four separate steps, so that suppression
and head selection never depend on each other:
1. validate every held object;
2. suppress the events covered by every valid reconciliation;
3. pick heads among valid, unsuppressed objects;
4. classify the heads and decide what this device's Manager may publish.

With more than one head, Constitution publishing is paused: the Manager publishes
no ordinary events until the heads are resolved. Apps are not affected.

A device holds every object it stored and every copy and approval carried inside
a stored reconciliation, valid or not; each gets a disposition as if received alone.
An object that builds on an event rejected by a valid reconciliation is invalid
(owner decision, after-t3-decisions.md), so it is kept as evidence but never blocks.
"""

from . import history, policy, reconcile

ALL_KINDS = ["approval", "ordinary", "reconciliation"]
RESOLVING_KINDS = ["approval", "reconciliation"]


def _view(objects, heads, classification, base, state, may_publish):
    return {
        "objects": objects,
        "heads": heads,
        "classification": classification,
        "base": base,
        "acting_state": state,
        "may_publish": may_publish,
        "conflicting_heads": heads if classification == "conflict" else [],
    }


def compute_view(encoded_objects, genesis_bytes: bytes) -> dict:
    """Return the view as a plain dict.

    objects:           held object ID -> {"status": "valid" | "invalid" | "pending", "reason", "missing"}
    heads:             sorted head IDs
    classification:    "followed", "conflict", or "split awaiting join"
    base:              nearest common ancestor of the heads, or None when followed
    acting_state:      state tables the device acts on (the head's, or the base's)
    may_publish:       sorted kinds the Manager may publish to the Constitution
    conflicting_heads: the heads when classification is "conflict", else []
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
            for rejected in lookup.objects[object_id]["body"]["payload"]["proposal"]["rejected"]:
                rejecters.setdefault(rejected, []).append(object_id)

    evaluator = reconcile.Evaluator(lookup)
    memo = {}
    tainted = {}  # object ID -> whether a valid reconciliation rejected it or one of its ancestors

    def disposition(object_id):
        """The Evaluator's result, except that an object building on a rejected event is invalid.

        Recursion ends: a valid reconciliation rejects only events after its base,
        so judging it looks only at objects closer to genesis than the rejected event.
        """
        if object_id not in memo:
            result = evaluator.result(object_id)
            if (result["status"] == "valid" and kind(object_id) != "genesis"
                    and rejected_here_or_before(parent(object_id))):
                result = history._result("invalid", "builds on a rejected event")
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

    # 1. Validate.
    objects = {}
    for object_id in held:
        result = disposition(object_id)
        status = "pending" if result["status"] == "incomplete" else result["status"]
        objects[object_id] = {"status": status, "reason": result["reason"],
                              "missing": result["missing"]}
    valid = {object_id for object_id in held if objects[object_id]["status"] == "valid"}

    # 2. Suppress the covered events of every valid reconciliation.
    suppressed = set()
    for object_id in valid:
        if kind(object_id) == "reconciliation":
            proposal = lookup.objects[object_id]["body"]["payload"]["proposal"]
            suppressed |= set(proposal["adopted"]) | set(proposal["rejected"])

    # 3. Heads: valid, unsuppressed, not approvals, and no valid child other than approvals.
    parents = {parent(o) for o in valid if kind(o) != "approval"}
    candidates = (valid | {lookup.genesis_id}) - suppressed
    heads = sorted(o for o in candidates if kind(o) != "approval" and o not in parents)

    # 4. Classify.
    if len(heads) == 1:
        state = disposition(heads[0])["state"]
        return _view(objects, heads, "followed", None, state, ALL_KINDS)
    chains = [[obj["id"] for obj in history._chain(lookup, head)[1]] for head in heads]
    # Every chain starts at genesis; once two chains differ they never meet again.
    depth = sum(1 for column in zip(*chains) if len(set(column)) == 1)
    base = chains[0][depth - 1]
    base_state = disposition(base)["state"]
    branch = sorted({object_id for chain in chains for object_id in chain[depth:]})
    if (any(kind(object_id) == "reconciliation" for object_id in branch)
            or policy.compatibility(base_state, [lookup.objects[o] for o in branch]) != "compatible"):
        return _view(objects, heads, "conflict", base, base_state, RESOLVING_KINDS)
    return _view(objects, heads, "split awaiting join", base, base_state, [])
