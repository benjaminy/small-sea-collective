"""A device's view of the Constitution, recomputed from the objects it holds.

Pure: `compute_view` takes encoded objects and the pinned genesis, and stores nothing.
It follows slice 1 plan section 4 in four separate steps, so that suppression
and head selection never depend on each other:
1. validate every stored object;
2. suppress the events covered by every valid reconciliation;
3. pick heads among valid, unsuppressed objects;
4. classify the heads and decide what this device's Manager may publish.

With more than one head, Constitution publishing is paused: the Manager publishes
no ordinary events until the heads are resolved. Apps are not affected.

Only stored (standalone) objects are validated and can become heads.
Copies and approvals carried inside a reconciliation serve only as ancestors.
"""

from . import encoding, history, policy, reconcile

ALL_KINDS = ["approval", "ordinary", "reconciliation"]
RESOLVING_KINDS = ["approval", "reconciliation"]


def _disposition(lookup, object_id, memo):
    """history's result for one object, except that reconciliations on its ancestry are checked.

    history.state_at trusts any reconciliation on the path; the view does not:
    an object built on an invalid or incomplete reconciliation is itself invalid or incomplete.
    """
    if object_id in memo:
        return memo[object_id]
    kind = lookup.objects[object_id]["body"]["kind"]
    if kind == "genesis":
        result = history.state_at(lookup, object_id)
    elif kind == "reconciliation":
        result = reconcile.check_reconciliation(lookup, object_id)
    else:
        result = history.validate_original(lookup, object_id)
    if result["status"] == "valid":
        _, chain = history._chain(lookup, object_id)
        for ancestor in chain[:-1]:
            if ancestor["body"]["kind"] != "reconciliation":
                continue
            found = _disposition(lookup, ancestor["id"], memo)
            if found["status"] != "valid":
                word = "an invalid" if found["status"] == "invalid" else "a pending"
                result = history._result(
                    found["status"], f"built on {word} reconciliation", found["missing"]
                )
                break
    memo[object_id] = result
    return result


def _parent(lookup, object_id):
    return lookup.objects[object_id]["body"].get("parents", [None])[0]


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

    objects:           stored object ID -> {"status": "valid" | "invalid" | "pending", "reason", "missing"}
    heads:             sorted head IDs
    classification:    "followed", "conflict", or "split awaiting join"
    base:              nearest common ancestor of the heads, or None when followed
    acting_state:      state tables the device acts on (the head's, or the base's)
    may_publish:       sorted kinds the Manager may publish to the Constitution
    conflicting_heads: the heads when classification is "conflict", else []
    """
    lookup = history.build_lookup(encoded_objects, genesis_bytes)
    stored = sorted({encoding.decode_object(data)["id"] for data in encoded_objects})
    memo = {}

    def kind(object_id):
        return lookup.objects[object_id]["body"]["kind"]

    # 1. Validate.
    objects = {}
    for object_id in stored:
        result = _disposition(lookup, object_id, memo)
        status = "pending" if result["status"] == "incomplete" else result["status"]
        objects[object_id] = {"status": status, "reason": result["reason"],
                              "missing": result["missing"]}
    valid = {object_id for object_id in stored if objects[object_id]["status"] == "valid"}

    # 2. Suppress the covered events of every valid reconciliation.
    suppressed = set()
    for object_id in valid:
        if kind(object_id) == "reconciliation":
            proposal = lookup.objects[object_id]["body"]["payload"]["proposal"]
            suppressed |= set(proposal["adopted"]) | set(proposal["rejected"])

    # 3. Heads: valid, unsuppressed, not approvals, and no valid child other than approvals.
    parents = {_parent(lookup, o) for o in valid if kind(o) != "approval"}
    candidates = (valid | {lookup.genesis_id}) - suppressed
    heads = sorted(o for o in candidates if kind(o) != "approval" and o not in parents)

    # 4. Classify.
    if len(heads) == 1:
        state = _disposition(lookup, heads[0], memo)["state"]
        return _view(objects, heads, "followed", None, state, ALL_KINDS)
    chains = [[obj["id"] for obj in history._chain(lookup, head)[1]] for head in heads]
    # Every chain starts at genesis; once two chains differ they never meet again.
    depth = sum(1 for column in zip(*chains) if len(set(column)) == 1)
    base = chains[0][depth - 1]
    base_state = _disposition(lookup, base, memo)["state"]
    branch = sorted({object_id for chain in chains for object_id in chain[depth:]})
    if (any(kind(object_id) == "reconciliation" for object_id in branch)
            or policy.compatibility(base_state, [lookup.objects[o] for o in branch]) != "compatible"):
        return _view(objects, heads, "conflict", base, base_state, RESOLVING_KINDS)
    return _view(objects, heads, "split awaiting join", base, base_state, [])
