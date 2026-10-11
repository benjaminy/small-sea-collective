"""Object lookup and ancestry to the pinned genesis.

Pure: everything works over a `Lookup` built from encoded objects, never storage.
The state after an object, which depends on the reconciliation checks, lives in
`reconcile.Evaluator`.
Every check returns a result dict with the same four keys:
  status:  "valid", "invalid", or "incomplete"
  reason:  None when valid, else a short string
  missing: IDs needed to decide (non-empty only when incomplete)
  state:   the state tables after the object (only from the Evaluator, when valid)
"""

from dataclasses import dataclass

from . import encoding


@dataclass
class Lookup:
    objects: dict  # object ID -> decoded object
    genesis_id: str
    genesis_bytes: bytes


def _result(status, reason=None, missing=(), state=None):
    return {"status": status, "reason": reason, "missing": list(missing), "state": state}


def build_lookup(encoded_objects, genesis_bytes: bytes) -> Lookup:
    """Decode objects and index them by ID, with every copy and approval carried
    inside a reconciliation, including reconciliations carried inside others.

    Each ID is indexed once. Standalone objects win; among carried objects,
    shallower ones win over deeper ones. Bodies with one ID are identical, so
    the choice affects only which signature bytes are kept.
    """
    genesis = encoding.decode_genesis(genesis_bytes)
    objects = {genesis["id"]: genesis}
    queue = [encoding.decode_object(data) for data in encoded_objects]
    for obj in queue:  # the queue grows as carried objects are found
        if obj["id"] not in objects:
            objects[obj["id"]] = obj
            queue.extend(obj.get("copies", []) + obj.get("approvals", []))
    return Lookup(objects, genesis["id"], genesis_bytes)


def _chain(lookup: Lookup, object_id: str):
    """Walk parent links to the pinned genesis. Returns (failure result or None, chain genesis-first)."""
    chain, seen, current = [], set(), object_id
    while True:
        obj = lookup.objects.get(current)
        if obj is None:
            return _result("incomplete", "missing ancestor", [current]), None
        if current in seen:
            return _result("invalid", "ancestry has a cycle"), None
        seen.add(current)
        chain.append(obj)
        if obj["body"]["kind"] == "genesis":
            if current != lookup.genesis_id:
                return _result("invalid", "ancestry reaches a different genesis"), None
            return None, chain[::-1]
        current = obj["body"]["parents"][0]
        parent = lookup.objects.get(current)
        if parent is not None and parent["body"]["kind"] == "approval":
            return _result("invalid", "parent is an approval"), None


def ancestry(lookup: Lookup, object_id: str) -> dict:
    """Valid when single parent links reach the pinned genesis; otherwise invalid or incomplete.

    This checks the shape of the links only; `reconcile.Evaluator` also checks
    each object's origin, operation, and reconciliation evidence.
    """
    failure, _ = _chain(lookup, object_id)
    return failure or _result("valid")
