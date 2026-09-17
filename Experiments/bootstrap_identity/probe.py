"""Two identity-authority policies over one authenticated exchange.

A research model, not a Small Sea implementation. Real Ed25519 signatures and
SHA-256 commitments; no network, no database, no wire protocol.

Three claims are kept apart everywhere in this file:

  KEY_SIGNED        a particular key signed a particular statement
  LOCAL_RECOGNITION this device treats a successor as the same enduring identity
  TEAM_AUTHORITY    a team recognizes that successor for some authority

Neither policy here ever returns TEAM_AUTHORITY.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

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


DELEGATION = b"voyage/identity-delegation/v1\x00"
RESPONSE = b"voyage/identity-response/v1\x00"
HUMAN = b"voyage/local-human-decision/v1\x00"

LOCAL_RECOGNITION = "local-recognition"


@dataclass(frozen=True)
class Comparison:
    """Built by the simulated human channel, never from delivered data."""
    commitment: str
    matched: bool


@dataclass
class Exchange:
    request: dict
    response: dict
    snapshot: bytes
    comparison: Comparison


@dataclass
class Verdict:
    stage: str
    outcome: str            # accept | pause | reject
    reason: str
    claim: str | None = None          # highest claim established, if any
    actor: str | None = None          # who made the decision
    scope: str | None = None          # what the decision covers
    prior_evidence: list = field(default_factory=list)   # refs it cites
    missing_proof: list = field(default_factory=list)    # what it does not prove
    preserved: list = field(default_factory=list)        # competing evidence kept
    decision_record: dict | None = None
    others_may_refuse: str | None = None


# --------------------------------------------------------------------------
# Shared exchange authentication (identical for both policies)
# --------------------------------------------------------------------------

def offer(introducer, anchor_pub, request, snapshot, chain_ids):
    raw = encode(snapshot)
    response = sign(introducer, RESPONSE, {
        "version": 1,
        "request": copy.deepcopy(request),     # binds the fresh newcomer key
        "introducer": public(introducer),
        "anchor": anchor_pub,
        "chain": list(chain_ids),
        "snapshot_digest": digest(raw),
    })
    return Exchange(copy.deepcopy(request), response, raw,
                    Comparison(digest(encode(response)), True))


def authenticate(exchange, *, require_comparison=True):
    """B1/B2: bind the complete exchange, the fresh newcomer key and the snapshot.

    Returns None when the exchange is authentic, else a rejecting Verdict.
    """
    response = exchange.response
    payload = response["payload"]
    if require_comparison:
        if not exchange.comparison.matched:
            return Verdict("B1", "pause", "no recorded human comparison",
                           missing_proof=["independent confirmation of the exchange"])
        if digest(encode(response)) != exchange.comparison.commitment:
            return Verdict("B1", "reject", "independently compared exchange differs",
                           missing_proof=["this exchange was never the compared one"])
    if payload["request"] != exchange.request:
        return Verdict("B1", "reject", "response names another local request or newcomer key")
    if not authentic(payload["introducer"], RESPONSE, response):
        return Verdict("B1", "reject", "response signature")
    if digest(exchange.snapshot) != payload["snapshot_digest"]:
        return Verdict("B2", "reject", "snapshot bytes differ from the compared digest")
    return None


# --------------------------------------------------------------------------
# Policy DA: durable anchor with retained signed delegation
# --------------------------------------------------------------------------

def durable_anchor(exchange, *, require_comparison=True):
    bad = authenticate(exchange, require_comparison=require_comparison)
    if bad:
        return bad
    payload = exchange.response["payload"]
    snapshot = json.loads(exchange.snapshot)
    records = {digest(encode(r)): r for r in snapshot["delegations"]}

    missing = [i for i in payload["chain"] if i not in records]
    if missing:
        return Verdict("B4", "pause", "retained delegation evidence absent",
                       prior_evidence=[payload["anchor"]], missing_proof=missing,
                       preserved=sorted(records),
                       others_may_refuse="any link from the anchor to this device")

    # Walk anchor -> ... -> introducer. Every hop must be signed by the previous holder.
    holder = payload["anchor"]
    cited = []
    for event_id in payload["chain"]:
        record = records[event_id]
        if not authentic(holder, DELEGATION, record):
            return Verdict("B4", "reject", "delegation not signed by the current holder",
                           prior_evidence=cited, missing_proof=[event_id],
                           preserved=sorted(records))
        link = record["payload"]
        if link["delegator"] != holder:
            return Verdict("B4", "reject", "delegation names another delegator",
                           prior_evidence=cited, missing_proof=[event_id])
        if link["scope"] != "identity-device":
            return Verdict("B5", "reject", f"delegation scope {link['scope']!r} is not identity-device",
                           prior_evidence=cited,
                           missing_proof=["an identity-device delegation"])
        cited.append(event_id)
        holder = link["delegate"]

    if holder != payload["introducer"]:
        return Verdict("B4", "reject", "chain does not reach the compared introducer",
                       prior_evidence=cited)

    # Conflicting successors: two live delegations of the same scope from one delegator.
    conflicts = {}
    for event_id, record in records.items():
        link = record["payload"]
        if link["scope"] != "identity-device" or not authentic(link["delegator"], DELEGATION, record):
            continue
        conflicts.setdefault((link["delegator"], link["supersedes"]), []).append(event_id)
    forked = sorted(sorted(ids) for ids in conflicts.values() if len(ids) > 1)
    if forked:
        return Verdict("B4", "pause", "conflicting successor claims from one delegator",
                       prior_evidence=cited, preserved=sorted({i for group in forked for i in group}),
                       missing_proof=["which successor the person actually authorized"],
                       others_may_refuse="either successor, until the person resolves the fork")

    return Verdict("B4", "accept", "delegation chain reaches the adopted anchor",
                   claim=LOCAL_RECOGNITION, actor="newcomer device policy DA",
                   scope="identity-device introduction only",
                   prior_evidence=[payload["anchor"]] + cited,
                   missing_proof=["that the anchor's current holder is the original person",
                                  "any team authority"],
                   preserved=sorted(records),
                   others_may_refuse="this device's enrollment in any particular team")


# --------------------------------------------------------------------------
# Policy SD: explicit, recorded human authorization of the compared sibling
# --------------------------------------------------------------------------

def sibling_delegation(exchange, decision, operator_key, *, require_comparison=True):
    bad = authenticate(exchange, require_comparison=require_comparison)
    if bad:
        return bad
    payload = exchange.response["payload"]

    if not authentic(operator_key, HUMAN, decision):
        return Verdict("H1", "reject", "local decision record not signed by this operator")
    d = decision["payload"]
    if d["exchange"] != digest(encode(exchange.response)):
        return Verdict("H1", "reject", "decision names another complete exchange")
    if d["attempt"] != exchange.request["attempt"] or d["newcomer"] != exchange.request["newcomer"]:
        return Verdict("H1", "reject", "decision authorizes another attempt or another device")
    if d["sibling"] != payload["introducer"]:
        return Verdict("H1", "reject", "decision names a different sibling key")
    if d["scope"] != "identity-device":
        return Verdict("H1", "reject", f"decision scope {d['scope']!r} is not identity-device")

    return Verdict("H1", "accept", "human authorized this sibling for this scope",
                   claim=LOCAL_RECOGNITION, actor=f"operator {operator_key[:8]}",
                   scope="identity-device introduction on this device only",
                   prior_evidence=d["prior_evidence"],
                   decision_record=copy.deepcopy(decision),
                   missing_proof=["continuity with any earlier key",
                                  "that the sibling's key is uncompromised",
                                  "any team authority"],
                   preserved=[f"exchange:{digest(encode(exchange.response))}"],
                   others_may_refuse="this recognition entirely; it is local and cites no history")


# --------------------------------------------------------------------------
# Independent audit: does not reuse either policy's acceptance logic
# --------------------------------------------------------------------------

def audit(exchange, verdict, ground_truth):
    """Re-verify retained evidence from raw bytes and compare to what really happened."""
    snapshot = json.loads(exchange.snapshot)
    valid, invalid = [], []
    for record in snapshot.get("delegations", []):
        signer = record["payload"]["delegator"]
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(signer)).verify(
                bytes.fromhex(record["signature"]), DELEGATION + encode(record["payload"]))
            valid.append(digest(encode(record)))
        except InvalidSignature:
            invalid.append(digest(encode(record)))
    return {
        "retained_records": len(snapshot.get("delegations", [])),
        "self_verified_signatures": sorted(valid),
        "broken_signatures": sorted(invalid),
        "anchor_is_attacker_generated": ground_truth["anchor_forged"],
        "introducer_key_stolen": ground_truth["key_stolen"],
        "policy_outcome": verdict.outcome,
        "policy_claim": verdict.claim,
        # The whole point: signature validity and honesty are different facts.
        "accepted_despite_forged_root": verdict.outcome == "accept" and ground_truth["anchor_forged"],
        "accepted_despite_stolen_key": verdict.outcome == "accept" and ground_truth["key_stolen"],
    }


TRUTH = {"anchor_forged": False, "key_stolen": False}


def scenarios():
    keys = {n: Ed25519PrivateKey.generate() for n in
            ("first", "sibling", "successor", "rival", "newcomer", "operator", "attacker")}
    out = {}

    def request(entry="sibling", attempt="attempt-a", newcomer="newcomer"):
        return {"version": 1, "attempt": attempt, "entry": entry,
                "newcomer": public(keys[newcomer])}

    def delegate(delegator, delegate_, scope="identity-device", supersedes="first"):
        return sign(keys[delegator], DELEGATION, {
            "version": 1, "delegator": public(keys[delegator]),
            "delegate": public(keys[delegate_]), "scope": scope, "supersedes": supersedes,
        })

    def snap(*records):
        return {"version": 1, "delegations": list(records)}

    def check(name, verdict, outcome, reason, truth=TRUTH, exchange=None):
        assert (verdict.outcome, verdict.reason) == (outcome, reason), (name, verdict)
        row = dict(verdict.__dict__)
        if exchange is not None:
            row["independent_audit"] = audit(exchange, verdict, truth)
        out[name] = row
        return verdict

    anchor = public(keys["first"])

    # 1. Valid control, durable anchor: first device delegates to the sibling.
    link = delegate("first", "sibling")
    ctl = offer(keys["sibling"], anchor, request(), snap(link), [digest(encode(link))])
    check("da_valid_control", durable_anchor(ctl), "accept",
          "delegation chain reaches the adopted anchor", exchange=ctl)

    # 2. Valid control, sibling delegation: operator signs a scoped local decision.
    plain = offer(keys["sibling"], anchor, request(), snap(), [])
    decision = sign(keys["operator"], HUMAN, {
        "version": 1, "attempt": "attempt-a", "newcomer": public(keys["newcomer"]),
        "sibling": public(keys["sibling"]), "scope": "identity-device",
        "exchange": digest(encode(plain.response)), "prior_evidence": [],
        "basis": "I recognized this person in a video call; I have no key-continuity proof.",
    })
    check("sd_valid_control", sibling_delegation(plain, decision, public(keys["operator"])),
          "accept", "human authorized this sibling for this scope", exchange=plain)

    # 3. First device lost, chain retained: the private key is gone, the evidence is not.
    hop1, hop2 = delegate("first", "sibling"), delegate("sibling", "successor", supersedes="sibling")
    lost = offer(keys["successor"], anchor, request(),
                 snap(hop1, hop2), [digest(encode(hop1)), digest(encode(hop2))])
    check("da_first_device_lost_chain_retained", durable_anchor(lost), "accept",
          "delegation chain reaches the adopted anchor", exchange=lost)

    # 4. Chain absent: same claim, no retained evidence.
    gone = offer(keys["successor"], anchor, request(), snap(), [digest(encode(hop1))])
    check("da_chain_absent", durable_anchor(gone), "pause",
          "retained delegation evidence absent", exchange=gone)
    # The same situation under SD: a fresh human decision, explicitly not a history proof.
    gone_sd = offer(keys["successor"], anchor, request(), snap(), [])
    d4 = sign(keys["operator"], HUMAN, {
        "version": 1, "attempt": "attempt-a", "newcomer": public(keys["newcomer"]),
        "sibling": public(keys["successor"]), "scope": "identity-device",
        "exchange": digest(encode(gone_sd.response)), "prior_evidence": [anchor],
        "basis": "Recognized in person after key loss. No delegation evidence survives.",
    })
    v4 = check("sd_chain_absent_human_recognition",
               sibling_delegation(gone_sd, d4, public(keys["operator"])), "accept",
               "human authorized this sibling for this scope", exchange=gone_sd)
    assert "continuity with any earlier key" in v4.missing_proof
    assert v4.claim == LOCAL_RECOGNITION       # never a historical proof

    # 5. Compromised but authenticated sibling: a real, valid chain in the wrong hands.
    stolen = offer(keys["sibling"], anchor, request(), snap(link), [digest(encode(link))])
    check("da_compromised_authenticated_sibling", durable_anchor(stolen), "accept",
               "delegation chain reaches the adopted anchor",
               truth={"anchor_forged": False, "key_stolen": True}, exchange=stolen)
    assert out["da_compromised_authenticated_sibling"]["independent_audit"]["accepted_despite_stolen_key"]

    # 6. Conflicting successor claims: two live successors of the same predecessor.
    rival = delegate("sibling", "rival", supersedes="sibling")
    fork = offer(keys["successor"], anchor, request(),
                 snap(hop1, hop2, rival), [digest(encode(hop1)), digest(encode(hop2))])
    v6 = check("da_conflicting_successors", durable_anchor(fork), "pause",
               "conflicting successor claims from one delegator", exchange=fork)
    assert {digest(encode(hop2)), digest(encode(rival))} <= set(v6.preserved)

    # 7. Fabricated self-rooted snapshot: internally perfect, wrong root.
    forged_link = delegate("attacker", "attacker")
    forged = offer(keys["attacker"], public(keys["attacker"]), request(),
                   snap(forged_link), [digest(encode(forged_link))])
    forged.comparison = ctl.comparison            # human compared the real exchange
    check("da_fabricated_self_rooted", durable_anchor(forged), "reject",
          "independently compared exchange differs",
          truth={"anchor_forged": True, "key_stolen": False}, exchange=forged)
    # Weakened baseline: drop only the independent comparison. The forgery is accepted.
    v7 = check("weakened_baseline_accepts_forgery",
               durable_anchor(forged, require_comparison=False), "accept",
               "delegation chain reaches the adopted anchor",
               truth={"anchor_forged": True, "key_stolen": False}, exchange=forged)
    assert out["weakened_baseline_accepts_forgery"]["independent_audit"]["accepted_despite_forged_root"]
    assert v7.claim == LOCAL_RECOGNITION

    # 8. Scope misuse: a berth-scoped delegation offered as identity-device evidence.
    misuse = delegate("first", "sibling", scope="berth-commit-signing")
    scoped = offer(keys["sibling"], anchor, request(), snap(misuse), [digest(encode(misuse))])
    check("da_scope_misuse", durable_anchor(scoped), "reject",
          "delegation scope 'berth-commit-signing' is not identity-device", exchange=scoped)
    # Same under SD: the human decision itself is scoped, and team scope is refused.
    d8 = sign(keys["operator"], HUMAN, {
        "version": 1, "attempt": "attempt-a", "newcomer": public(keys["newcomer"]),
        "sibling": public(keys["sibling"]), "scope": "team-authority", "basis": "overreach",
        "exchange": digest(encode(plain.response)), "prior_evidence": [],
    })
    check("sd_scope_misuse", sibling_delegation(plain, d8, public(keys["operator"])),
          "reject", "decision scope 'team-authority' is not identity-device", exchange=plain)

    changed_view = offer(keys["sibling"], anchor, request(), snap(link), [digest(encode(link))])
    check("sd_decision_for_another_exchange",
          sibling_delegation(changed_view, decision, public(keys["operator"])),
          "reject", "decision names another complete exchange", exchange=changed_view)
    assert v4.decision_record == d4
    assert v4.prior_evidence == [anchor]

    # 9. Paired claimants with indistinguishable signed input.
    legit = offer(keys["sibling"], anchor, request(), snap(link), [digest(encode(link))])
    thief = copy.deepcopy(legit)                 # an attacker holding the same private key
    assert encode(thief.response) == encode(legit.response)
    a = durable_anchor(legit)
    b = durable_anchor(thief)
    assert (a.outcome, a.reason) == (b.outcome, b.reason) == ("accept",
           "delegation chain reaches the adopted anchor")
    out["paired_claimant_and_stolen_key"] = {
        "inputs_identical": True,
        "legitimate_verdict": a.outcome,
        "stolen_key_verdict": b.outcome,
        "distinguishing_evidence_available_to_the_model": [],
        "note": "No signature check can separate these. Only out-of-band human"
                " recognition or an independent endorsement can, and that is a new"
                " decision, not a proof about the past.",
    }

    # Cross-cutting invariant: no policy in this file ever grants team authority.
    for name, row in out.items():
        assert row.get("claim") in (None, LOCAL_RECOGNITION), name
    return out


if __name__ == "__main__":
    print(json.dumps(scenarios(), indent=2, sort_keys=True))
