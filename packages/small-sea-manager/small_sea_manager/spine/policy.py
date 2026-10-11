"""Pure state changes and authority rules for the fixed spine-demo-1 policy."""

from copy import deepcopy

from . import encoding


def initial_state(genesis_bytes: bytes) -> dict:
    """Build keyed tables from a canonically decoded genesis."""
    body = encoding.decode_genesis(genesis_bytes)["body"]
    return {
        "members": {row["member_id"]: dict(row) for row in body["members"]},
        "devices": {row["device_key"]: dict(row) for row in body["devices"]},
        "policy": deepcopy(body["policy"]),
    }


def _copy(state: dict) -> dict:
    """An independent copy; rows are flat dicts of strings, so dict() copies them fully."""
    return {
        "members": {key: dict(row) for key, row in state["members"].items()},
        "devices": {key: dict(row) for key, row in state["devices"].items()},
        "policy": deepcopy(state["policy"]),
    }


def apply_ordinary(state: dict, signer: str, payload: dict) -> tuple[dict | None, str | None]:
    """Return a fresh state, or (None, reason) when the operation is refused."""
    device = state["devices"].get(signer)
    if device is None:
        return None, "signer is not a device"
    actor = device["member_id"]
    op = payload["op"]
    if op == "add_device":
        if payload["member_id"] != actor:
            return None, "cannot add a device for another member"
        if payload["device_key"] in state["devices"]:
            return None, "device key already exists"
        if not encoding.public_key_allowed(payload["device_key"]):
            return None, "disallowed device key"
        result = _copy(state)
        row = {key: payload[key] for key in ("device_key", "member_id", "label")}
        result["devices"][row["device_key"]] = row
        return result, None
    if op == "remove_member":
        target = payload["member_id"]
        if target == actor:
            return None, "cannot remove self"
        if target not in state["members"]:
            return None, "member does not exist"
        result = _copy(state)
        del result["members"][target]
        result["devices"] = {
            key: row for key, row in result["devices"].items()
            if row["member_id"] != target
        }
        return result, None
    return None, "unknown operation"


def approval_valid(state: dict, signer: str) -> bool:
    """An approval is valid at its parent state if its signer is a device there."""
    return signer in state["devices"]


def compatibility(base_state: dict, events: list[dict]) -> str:
    """Try every order allowed by the parent links of at most six branch events."""
    if len(events) > 6:
        return "unsupported"
    by_id = {event["id"]: event for event in events}
    final_state = None
    found_order = False

    def visit(state, remaining):
        nonlocal final_state, found_order
        if not remaining:
            found_order = True
            if final_state is None:
                final_state = state
            return state == final_state
        for event_id in remaining:
            event = by_id[event_id]
            parent = event["body"]["parents"][0]
            if parent in remaining:
                continue
            next_state, reason = apply_ordinary(
                state, event["body"]["author"], event["body"]["payload"]
            )
            if reason or not visit(next_state, remaining - {event_id}):
                return False
        return True

    return "compatible" if visit(base_state, set(by_id)) and found_order else "incompatible"


def reconciliation_authorized(
    base_state: dict, base_id: str, signer: str, digest: str, approvals: list[dict]
) -> bool:
    """Count distinct base members whose valid approvals name this base and digest."""
    if signer not in base_state["devices"]:
        return False
    members = set()
    for approval in approvals:
        body = approval["body"]
        device = base_state["devices"].get(body["author"])
        if (device is not None and body["kind"] == "approval"
                and body["parents"] == [base_id]
                and body["payload"]["approves"] == digest):
            members.add(device["member_id"])
    return len(members) >= base_state["policy"]["settings"]["reconcile_quorum"]
