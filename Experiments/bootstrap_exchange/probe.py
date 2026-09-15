"""A small exchange model, not the Small Sea wire protocol or authority engine."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def public(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign(key, domain, payload):
    return {"payload": payload, "signature": key.sign(domain + encode(payload)).hex()}


def authentic(key_hex, domain, record):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)).verify(
            bytes.fromhex(record["signature"]), domain + encode(record["payload"])
        )
    except InvalidSignature:
        return False
    return True


GRANT = b"voyage/grant/v1\x00"
RESPONSE = b"voyage/response/v1\x00"


@dataclass(frozen=True)
class Comparison:
    # Constructed by the simulated trusted channel, never from delivery data.
    commitment: str
    matched: bool


@dataclass
class Verdict:
    stage: str
    reason: str
    delivered: object = None
    reconstructed: object = None


@dataclass
class Exchange:
    request: dict
    response: dict
    snapshot: bytes
    comparison: Comparison


def offer(introducer, authority, request, snapshot, frontier):
    raw = encode(snapshot)
    response = sign(introducer, RESPONSE, {
        "version": 1, "request": copy.deepcopy(request),
        "introducer": public(introducer), "anchor": public(authority),
        "frontier": sorted(frontier), "snapshot_digest": digest(raw),
    })
    return Exchange(copy.deepcopy(request), response, raw,
                    Comparison(digest(encode(response)), True))


def inspect(exchange, *, require_comparison=True):
    response = exchange.response
    payload = response["payload"]
    if require_comparison:
        if not exchange.comparison.matched:
            return Verdict("B1", "pause: comparison missing")
        if digest(encode(response)) != exchange.comparison.commitment:
            return Verdict("B1", "reject: independently compared exchange differs")
    if payload["request"] != exchange.request:
        return Verdict("B1", "reject: response names another local request")
    if not authentic(payload["introducer"], RESPONSE, response):
        return Verdict("B1", "reject: response signature")
    if digest(exchange.snapshot) != payload["snapshot_digest"]:
        return Verdict("B2", "reject: snapshot bytes differ")
    snapshot = json.loads(exchange.snapshot)
    events = {digest(encode(event)): event for event in snapshot["events"]}
    missing = sorted(set(payload["frontier"]) - events.keys())
    if missing:
        return Verdict("B4", "pause: selected evidence missing", missing)
    projection = []
    for event_id in payload["frontier"]:
        event = events[event_id]
        if not authentic(payload["anchor"], GRANT, event):
            return Verdict("B4", "reject: grant not signed by pinned anchor")
        grant = event["payload"]
        if grant["origin"] != exchange.request["origin"]:
            return Verdict("B3", "reject: technical origin differs")
        projection.append({key: grant[key] for key in ("subject", "berth", "purpose")})
    projection.sort(key=encode)
    delivered = sorted(snapshot["projection"], key=encode)
    if delivered != projection:
        return Verdict("B6", "pause: projection differs on evidence", delivered, projection)
    return Verdict("B6", "agrees under single-anchor grant policy", delivered, projection)


def scenarios():
    # Keys are disposable and random. The scenario outcomes do not depend on them.
    introducer, authority, newcomer, attacker = [Ed25519PrivateKey.generate() for _ in range(4)]
    request = {"version": 1, "attempt": "attempt-a", "origin": "team-a",
               "newcomer": public(newcomer), "entry": "invitation"}
    grant = sign(authority, GRANT, {
        "version": 1, "origin": "team-a", "subject": public(newcomer),
        "berth": "notes", "purpose": "commit-signing",
    })
    projected = {key: grant["payload"][key] for key in ("subject", "berth", "purpose")}
    snapshot = {"version": 1, "events": [grant], "projection": [projected]}
    frontier = [digest(encode(grant))]
    control = offer(introducer, authority, request, snapshot, frontier)
    outputs = {}

    def check(name, exchange, stage, reason, **kwargs):
        verdict = inspect(exchange, **kwargs)
        assert (verdict.stage, verdict.reason) == (stage, reason), (name, verdict)
        outputs[name] = verdict.__dict__
        return verdict

    check("valid", control, "B6", "agrees under single-anchor grant policy")
    altered = copy.deepcopy(control)
    altered.snapshot += b"\n"
    # Even a semantically identical snapshot is not the exact offered bytes.
    assert json.loads(altered.snapshot) == json.loads(control.snapshot)
    check("changed_bytes", altered, "B2", "reject: snapshot bytes differ")

    false_state = copy.deepcopy(snapshot)
    false_state["projection"][0]["berth"] = "finances"
    dishonest = offer(introducer, authority, request, false_state, frontier)
    verdict = check("authentic_false_projection", dishonest, "B6", "pause: projection differs on evidence")
    assert verdict.delivered[0]["berth"] == "finances"
    assert verdict.reconstructed[0]["berth"] == "notes"

    substitute_grant = sign(attacker, GRANT, grant["payload"])
    substitute = offer(attacker, attacker, request,
                       {"version": 1, "events": [substitute_grant], "projection": [projected]},
                       [digest(encode(substitute_grant))])
    substitute.comparison = control.comparison
    check("self_consistent_substitution", substitute, "B1", "reject: independently compared exchange differs")
    check("naive_self_consistency_baseline", substitute, "B6",
          "agrees under single-anchor grant policy", require_comparison=False)

    wrong_anchor = offer(introducer, authority, request,
                         {"version": 1, "events": [substitute_grant], "projection": [projected]},
                         [digest(encode(substitute_grant))])
    check("authenticated_wrong_anchor_grant", wrong_anchor, "B4", "reject: grant not signed by pinned anchor")
    missing = offer(introducer, authority, request,
                    {"version": 1, "events": [], "projection": [projected]}, frontier)
    check("authenticated_missing_evidence", missing, "B4", "pause: selected evidence missing")
    unchecked = copy.deepcopy(control)
    unchecked.comparison = Comparison(control.comparison.commitment, False)
    check("missing_comparison", unchecked, "B1", "pause: comparison missing")
    wrong_request = copy.deepcopy(control)
    wrong_request.request["attempt"] = "attempt-b"
    check("another_attempt", wrong_request, "B1", "reject: response names another local request")

    # Independent possession of a second event establishes that omission occurred.
    # The newcomer gets no reference to it, so its checks cannot discover it.
    concealed = sign(authority, GRANT, {**grant["payload"], "subject": public(attacker)})
    assert digest(encode(concealed)) not in frontier
    check("authentic_undisclosed_grant", control, "B6", "agrees under single-anchor grant policy")
    return outputs


if __name__ == "__main__":
    print(json.dumps(scenarios(), indent=2, sort_keys=True))
