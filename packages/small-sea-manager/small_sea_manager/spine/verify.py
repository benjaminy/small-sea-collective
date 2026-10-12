"""Check an exported bundle from nothing but its bytes and the team's origin.

    python -m small_sea_manager.spine.verify --origin ID BUNDLE [--json]

The origin (the genesis ID) arrives out of band; the bundle cannot vouch for itself.
`verify` runs the same evaluator a device runs, on the bundle's objects alone,
and returns:

  status:         "valid", "invalid", or "incomplete"
  check, reason:  for a failure, the first failed check (or None) and why
  missing:        IDs needed to decide, when incomplete
  tip:            the bundle's tip
  chain:          every object from the genesis to the tip, each {event, description}
  base, branches, conflict, reconciliation:
                  about the latest reconciliation on the tip's path, or None without one.
                  branches lists its kept events in replay order, then its
                  rejected events in path order, each
                  {event, description, valid_at_parent, state_after};
                  conflict says why the branches did not simply merge, and what each
                  rejected operation does independently against the state after the
                  kept events (a reason, or None if it would still apply).
                  This does not explain the signer's choice or check whether an
                  event could be adopted without its ancestors.
                  reconciliation is {id, signer, adopted, rejected}
  final_state:    the state tables at the tip
  scope:          what the verifier could not check

Exit status is 0 when the bundle is valid and 1 otherwise.
"""

import argparse
import json
import sys

from . import encoding, explain, history, policy, reconcile

SCOPE = ("This checks only the history in the bundle. It cannot say what any device "
         "received, or whether other branches exist.")


def _outcome(status, check=None, reason=None, missing=(), tip=None):
    return {"status": status, "check": check, "reason": reason, "missing": list(missing),
            "tip": tip, "chain": None, "base": None, "branches": None, "conflict": None,
            "reconciliation": None, "final_state": None, "scope": SCOPE}


def verify(bundle_bytes: bytes, origin: str) -> dict:
    try:
        bundle = encoding.decode_bundle(bundle_bytes)
        encoded = [bytes.fromhex(h) for h in encoding.decode_canonical(bundle_bytes)["objects"]]
    except encoding.EncodingError as e:
        return _outcome("invalid", "core", f"bundle does not decode: {e}")
    tip = bundle["tip"]
    if bundle["origin"] != origin:
        return _outcome("invalid", "origin", "bundle belongs to another team", tip=tip)
    genesis = [data for data, obj in zip(encoded, bundle["objects"]) if obj["id"] == origin]
    if not genesis:
        return _outcome("incomplete", None, "bundle lacks the genesis", [origin], tip)
    try:
        lookup = history.build_lookup(encoded, genesis[0])
    except encoding.EncodingError as e:
        return _outcome("invalid", "core", f"genesis does not decode: {e}", tip=tip)

    evaluator = reconcile.Evaluator(lookup)
    found = evaluator.result(tip)
    failure, chain = history._chain(lookup, tip)
    if found["status"] != "valid":
        if failure is None:  # report the first object on the path that is not valid
            found = next(r for r in map(evaluator.result, (o["id"] for o in chain))
                         if r["status"] != "valid")
        return _outcome(found["status"], found.get("check"), found["reason"],
                        found["missing"], tip)

    def state_before(obj):
        parents = obj["body"].get("parents")
        return evaluator.result(parents[0])["state"] if parents else None

    out = _outcome("valid", tip=tip)
    out["chain"] = [{"event": o["id"], "description": explain.describe(o, state_before(o))}
                    for o in chain]
    out["final_state"] = found["state"]
    reconciliations = [o for o in chain if o["body"]["kind"] == "reconciliation"]
    if reconciliations:
        out.update(_reconciliation(lookup, evaluator, reconciliations[-1], state_before))
    return out


def _reconciliation(lookup, evaluator, r, state_before):
    payload = r["body"]["payload"]
    base = r["body"]["parents"][0]
    base_state = evaluator.result(base)["state"]
    copies = {c["id"] for c in r["copies"]}
    order = []  # covered events in path order, each once; then kept ones first
    for tip in payload["tips"]:
        for obj in history._chain(lookup, tip)[1]:
            if obj["id"] in copies and obj["id"] not in order:
                order.append(obj["id"])
    order = payload["adopted"] + [e for e in order if e not in payload["adopted"]]
    events = [lookup.objects[e] for e in order]

    _, reason = policy.merge(base_state, events)
    after_kept, state = {}, evaluator.result(r["id"])["state"]
    for event in events:
        if event["id"] in payload["rejected"]:
            body = event["body"]
            _, after_kept[event["id"]] = policy.apply_ordinary(
                state, body["author"], body["payload"])

    return {
        "base": base,
        "branches": [{
            "event": e["id"],
            "description": explain.describe(e, state_before(e)),
            "valid_at_parent": evaluator.result(e["id"])["status"] == "valid",
            "state_after": evaluator.result(e["id"])["state"],
        } for e in events],
        "conflict": {"reason": reason or "the branches merge; the rejections were a choice",
                     "rejected_after_kept": after_kept},
        "reconciliation": {"id": r["id"], "signer": explain.who(base_state, r["body"]["author"]),
                           "adopted": payload["adopted"], "rejected": payload["rejected"]},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check an exported spine bundle.")
    parser.add_argument("--origin", required=True, help="the team's genesis ID, learned out of band")
    parser.add_argument("bundle", help="path to the bundle file")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    with open(args.bundle, "rb") as f:
        result = verify(f.read(), args.origin)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else explain.explain(result))
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    sys.exit(main())
