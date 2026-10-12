"""Plain-terms descriptions of spine objects, states, and verifier results.

Pure. A description names people and devices from the state tables in force
before the object, so it says "Alice (device a) removes Bob" rather than keys.
Object IDs are shortened to their first eight hex digits.
"""


def short(object_id: str) -> str:
    return object_id[:8]


def who(state, device_key):
    device = (state or {}).get("devices", {}).get(device_key)
    if device is None:
        return f"an unlisted device ({short(device_key)})"
    member = state["members"][device["member_id"]]["name"]
    return f"{member} (device {device['label']})"


def _member(state, member_id):
    row = (state or {}).get("members", {}).get(member_id)
    return row["name"] if row else member_id


def describe(obj: dict, state_before) -> str:
    """One line for one object; state_before is the state at its parent (None for a genesis)."""
    body = obj["body"]
    kind = body["kind"]
    if kind == "genesis":
        return "the team begins with " + describe_state({
            "members": {m["member_id"]: m for m in body["members"]},
            "devices": {d["device_key"]: d for d in body["devices"]},
        })
    if kind == "join":
        return "join of " + ", ".join(short(t) for t in body["payload"]["tips"])
    actor = who(state_before, body["author"])
    payload = body["payload"]
    if kind == "reconciliation":
        keeps = ", ".join(short(e) for e in payload["adopted"]) or "nothing"
        rejects = ", ".join(short(e) for e in payload["rejected"]) or "nothing"
        return f"{actor} reconciles: keeps {keeps}; rejects {rejects}"
    if payload["op"] == "remove_member":
        return f"{actor} removes {_member(state_before, payload['member_id'])}"
    if payload["op"] == "add_device":
        return f"{actor} adds device {payload['label']}"
    return f"{actor} does {payload['op']}"


def describe_state(state) -> str:
    """Each member with their device labels, e.g. "Alice: a; Carol: c, c2"."""
    labels = {member_id: [] for member_id in state["members"]}
    for device in state["devices"].values():
        labels[device["member_id"]].append(device["label"])
    return "; ".join(
        f"{state['members'][m]['name']}: {', '.join(sorted(labels[m])) or 'no devices'}"
        for m in sorted(labels, key=lambda m: state["members"][m]["name"]))


def explain(result: dict) -> str:
    """The verifier's result as text for a person."""
    if result["status"] != "valid":
        lines = [f"{result['status'].upper()}: {result['reason']}"]
        if result["check"]:
            lines.append(f"Failed check: {result['check']}")
        if result["missing"]:
            lines.append("Missing: " + ", ".join(short(m) for m in result["missing"]))
        lines.append(result["scope"])
        return "\n".join(lines)

    lines = [f"VALID: the history up to {short(result['tip'])} checks out.", "", "History:"]
    lines += [f"  {short(step['event'])}  {step['description']}" for step in result["chain"]]
    r = result["reconciliation"]
    if r is not None:
        lines += ["", f"The latest reconciliation, {short(r['id'])}, settled a split after "
                      f"{short(result['base'])}. The branches did:"]
        for branch in result["branches"]:
            verdict = "kept" if branch["event"] in r["adopted"] else "rejected"
            lines.append(f"  {short(branch['event'])}  {branch['description']} ({verdict})")
            lines.append(f"            afterwards: {describe_state(branch['state_after'])}")
        lines.append(f"Merge check: {result['conflict']['reason']}")
        for event, reason in result["conflict"]["rejected_after_kept"].items():
            outcome = (f"cannot apply to the state produced by this reconciliation: {reason}"
                       if reason else "could still apply to the state produced by this reconciliation")
            lines.append(f"  {r['signer']} rejected {short(event)}. Its operation {outcome}.")
        if result["conflict"]["rejected_after_kept"]:
            lines += ["Each operation is checked independently, without applying other rejected events.",
                      "These checks do not explain the signer's reasons for rejecting events",
                      "or establish that an event could be adopted without its ancestors."]
        else:
            lines.append(f"{r['signer']} decided.")
    lines += ["", f"This history produces: {describe_state(result['final_state'])}",
              "A device following this history would use that state.",
              "Other devices may follow different histories or still be waiting for their owners to choose.",
              "", result["scope"]]
    return "\n".join(lines)
