"""Object lookup, ancestry to the pinned genesis, and state at an object.

Pure: everything works over a `Lookup` built from encoded objects, never storage.
Every check returns a result dict with the same four keys:
  status:  "valid", "invalid", or "incomplete"
  reason:  None when valid, else a short string
  missing: IDs needed to decide (non-empty only when incomplete)
  state:   the state tables after the object (only for state_at / validate_original when valid)
"""

from dataclasses import dataclass

from . import encoding, policy


@dataclass
class Lookup:
    objects: dict  # object ID -> decoded object
    genesis_id: str
    genesis_bytes: bytes


def _result(status, reason=None, missing=(), state=None):
    return {"status": status, "reason": reason, "missing": list(missing), "state": state}


def build_lookup(encoded_objects, genesis_bytes: bytes) -> Lookup:
    """Decode objects and index them by ID, including copies and approvals inside reconciliations.

    A standalone object wins over a retained copy of the same ID.
    """
    genesis = encoding.decode_genesis(genesis_bytes)
    objects = {genesis["id"]: genesis}
    decoded = [encoding.decode_object(data) for data in encoded_objects]
    for obj in decoded:
        objects.setdefault(obj["id"], obj)
    for obj in decoded:
        for held in obj.get("copies", []) + obj.get("approvals", []):
            objects.setdefault(held["id"], held)
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
    """Valid when single parent links reach the pinned genesis; otherwise invalid or incomplete."""
    failure, _ = _chain(lookup, object_id)
    return failure or _result("valid")


def state_at(lookup: Lookup, object_id: str) -> dict:
    """State tables after an object, replaying every step from the pinned genesis.

    A reconciliation is assumed to have passed the reconcile checks; replay still refuses on failure.
    """
    failure, chain = _chain(lookup, object_id)
    if failure:
        return failure
    state = policy.initial_state(lookup.genesis_bytes)
    for obj in chain[1:]:
        body = obj["body"]
        if body["kind"] == "ordinary":
            state, reason = policy.apply_ordinary(state, body["author"], body["payload"])
            if reason:
                return _result("invalid", reason)
        elif body["kind"] == "approval":
            if not policy.approval_valid(state, body["author"]):
                return _result("invalid", "signer is not a device")
        else:
            copies = {c["id"]: c for c in obj["copies"]}
            for adopted_id in body["payload"]["proposal"]["adopted"]:
                step = copies.get(adopted_id)
                if step is None:
                    return _result("invalid", "adopted event is not carried")
                state, reason = policy.apply_ordinary(
                    state, step["body"]["author"], step["body"]["payload"]
                )
                if reason:
                    return _result("invalid", f"replay fails: {reason}")
    return _result("valid", state=state)


def validate_original(lookup: Lookup, object_id: str) -> dict:
    """Check one ordinary event or approval at its own parent's state; state is the state after it."""
    obj = lookup.objects.get(object_id)
    if obj is None:
        return _result("incomplete", "missing object", [object_id])
    if obj["body"]["kind"] not in ("ordinary", "approval"):
        return _result("invalid", "not an ordinary event or approval")
    return state_at(lookup, object_id)
