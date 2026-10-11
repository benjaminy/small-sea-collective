"""Canonical bytes, strict decoding, IDs, signatures, and digests for spine objects.

Only "core checks" live here: format, IDs, signatures, and origins.
Policy, ancestry to genesis, and reconciliation validation belong to later modules.

Spec readings chosen here:
- Embedded objects (copies, approvals, bundle objects) are lowercase-hex strings of their encoded bytes.
- Lists that must be "sorted by ID" are strictly increasing, so duplicates are refused.
- Genesis `nonce` is 64 hex characters; `member_id`, `name`, and `label` are free strings.
- A genesis decodes to {"id", "body"}; a signed event to {"id", "body", "sig"}, plus
  decoded "copies" and "approvals" lists for reconciliations.
"""

import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

FORMAT = "spine-v1"
POLICY = {"name": "spine-demo-1", "settings": {"reconcile_quorum": 2}}


class EncodingError(ValueError):
    pass


def canonical(value) -> bytes:
    def check(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if not isinstance(k, str):
                    raise EncodingError("object keys must be strings")
                check(x)
        elif isinstance(v, list):
            for x in v:
                check(x)
        elif isinstance(v, str):
            pass
        elif type(v) is int:
            if v < 0:
                raise EncodingError("negative integer")
        else:
            raise EncodingError(f"disallowed value type {type(v).__name__}")

    check(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _no_dups(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise EncodingError("duplicate key")
    return dict(pairs)


def _refuse(x):
    raise EncodingError(f"disallowed number {x}")


def decode_canonical(data: bytes):
    try:
        value = json.loads(
            data.decode("ascii"),
            object_pairs_hook=_no_dups,
            parse_float=_refuse,
            parse_constant=_refuse,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise EncodingError(f"not JSON: {e}") from e
    if canonical(value) != data:
        raise EncodingError("not canonical bytes")
    return value


# ---- IDs, signatures, digest ----

def object_id(body) -> str:
    return hashlib.sha256(b"spine-v1 id\0" + canonical(body)).hexdigest()


def proposal_digest(proposal) -> str:
    return hashlib.sha256(b"spine-v1 proposal\0" + canonical(proposal)).hexdigest()


def public_key(seed: bytes) -> str:
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw().hex()


def sign(seed: bytes, body) -> str:
    return Ed25519PrivateKey.from_private_bytes(seed).sign(
        b"spine-v1 sig\0" + canonical(body)
    ).hex()


# Ed25519 y coordinates from libsodium 1.0.18's ge25519_has_small_order.
# Ignoring the sign bit covers both signs, including OpenSSL's x=0 aliases.
# https://github.com/jedisct1/libsodium/blob/1.0.18/src/libsodium/crypto_core/ed25519/ref10/ed25519_ref10.c#L966
_ED25519_P = 2**255 - 19
_SMALL_ORDER_Y = frozenset([
    0, 1, _ED25519_P - 1, _ED25519_P, _ED25519_P + 1,
    int.from_bytes(bytes.fromhex(
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05"), "little"),
    int.from_bytes(bytes.fromhex(
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a"), "little"),
])


def public_key_allowed(key: str) -> bool:
    """Require 32 bytes, canonical y, and no small-order point.

    Signature verification still checks curve membership and the signature.
    This is not a full prime-subgroup validation routine.
    """
    try:
        raw = bytes.fromhex(key)
    except ValueError:
        return False
    if len(raw) != 32:
        return False
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    return y < _ED25519_P and y not in _SMALL_ORDER_Y


def verify(author: str, body, sig: str) -> bool:
    if not public_key_allowed(author):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(author)).verify(
            bytes.fromhex(sig), b"spine-v1 sig\0" + canonical(body)
        )
        return True
    except (InvalidSignature, ValueError):
        return False


# ---- strict schemas ----
# A spec is: str, int, ("hex", n) (n None = any even length), a dict of specs
# (exact keys), [spec] (list), or a predicate function.

def hexs(n=None):
    return ("hex", n)


def _check(v, spec, path="value"):
    ok = True
    if spec is str:
        ok = isinstance(v, str)
    elif spec is int:
        ok = type(v) is int and v >= 0
    elif isinstance(spec, tuple):
        n = spec[1]
        ok = (
            isinstance(v, str)
            and re.fullmatch(r"[0-9a-f]*", v) is not None
            and (len(v) == n if n else len(v) % 2 == 0)
        )
    elif isinstance(spec, dict):
        if not isinstance(v, dict) or set(v) != set(spec):
            raise EncodingError(f"{path}: wrong or missing keys")
        for k, s in spec.items():
            _check(v[k], s, f"{path}.{k}")
    elif isinstance(spec, list):
        if not isinstance(v, list):
            raise EncodingError(f"{path}: expected list")
        for x in v:
            _check(x, spec[0], path)
    else:
        ok = spec(v)
    if not ok:
        raise EncodingError(f"{path}: bad value")


def _lit(x):
    return lambda v: v == x


H = hexs(64)
PROPOSAL_SPEC = {
    "format": _lit("spine-proposal-v1"),
    "origin": H, "base": H, "signer": H,
    "resolved_tips": [H], "adopted": [H], "rejected": [H],
}


def _sorted_by_id(items, what):
    ids = [i["id"] for i in items]
    if ids != sorted(set(ids)):
        raise EncodingError(f"{what} not strictly sorted by ID")


def _decode_embedded(hexes, origin, what):
    items = [decode_event(bytes.fromhex(h)) for h in hexes]
    _sorted_by_id(items, what)
    if any(i["body"]["origin"] != origin for i in items):
        raise EncodingError(f"{what}: wrong origin")
    return items


def _payload_spec(kind, payload):
    if kind == "approval":
        return {"approves": H}
    if kind == "reconciliation":
        return {"proposal": PROPOSAL_SPEC, "copies": [hexs()], "approvals": [hexs()]}
    op = payload.get("op") if isinstance(payload, dict) else None
    if op == "remove_member":
        return {"op": _lit(op), "member_id": str}
    if op == "add_device":
        return {"op": _lit(op), "member_id": str, "device_key": H, "label": str}
    raise EncodingError("unknown op")


def decode_genesis(data: bytes) -> dict:
    g = decode_canonical(data)
    _check(g, {
        "format": _lit(FORMAT), "kind": _lit("genesis"), "nonce": H,
        "policy": _lit(POLICY),
        "members": [{"member_id": str, "name": str}],
        "devices": [{"device_key": H, "member_id": str, "label": str}],
    })
    member_ids = [m["member_id"] for m in g["members"]]
    keys = [d["device_key"] for d in g["devices"]]
    if len(set(member_ids)) != len(member_ids) or len(set(keys)) != len(keys):
        raise EncodingError("duplicate member or device")
    if any(d["member_id"] not in member_ids for d in g["devices"]):
        raise EncodingError("device names unlisted member")
    if any(not public_key_allowed(key) for key in keys):
        raise EncodingError("disallowed device key")
    return {"id": object_id(g), "body": g}


def decode_event(data: bytes) -> dict:
    env = decode_canonical(data)
    _check(env, {"body": lambda v: isinstance(v, dict), "sig": hexs(128)})
    body = env["body"]
    kind = body.get("kind")
    if kind not in ("ordinary", "approval", "reconciliation"):
        raise EncodingError("unknown event kind")
    _check(body, {
        "format": _lit(FORMAT), "origin": H, "kind": str, "author": H,
        "parents": [H],
        "payload": _payload_spec(kind, body["payload"] if "payload" in body else None),
    })
    if len(body["parents"]) != 1:
        raise EncodingError("an event has exactly one parent")
    if not verify(body["author"], body, env["sig"]):
        raise EncodingError("bad signature")
    out = {"id": object_id(body), "body": body, "sig": env["sig"]}
    if kind == "reconciliation":
        p, origin = body["payload"], body["origin"]
        if p["proposal"]["origin"] != origin:
            raise EncodingError("proposal origin differs")
        c = p["proposal"]
        if c["resolved_tips"] != sorted(c["resolved_tips"]) or c["rejected"] != sorted(c["rejected"]):
            raise EncodingError("proposal lists not sorted")
        out["copies"] = _decode_embedded(p["copies"], origin, "copies")
        out["approvals"] = _decode_embedded(p["approvals"], origin, "approvals")
        if any(a["body"]["kind"] != "approval" for a in out["approvals"]):
            raise EncodingError("approvals must be approval events")
    return out


def decode_object(data: bytes) -> dict:
    return decode_event(data) if "body" in decode_canonical(data) else decode_genesis(data)


def decode_proposal(data: bytes) -> dict:
    c = decode_canonical(data)
    _check(c, PROPOSAL_SPEC)
    if c["resolved_tips"] != sorted(c["resolved_tips"]) or c["rejected"] != sorted(c["rejected"]):
        raise EncodingError("proposal lists not sorted")
    return c


def decode_package(data: bytes) -> dict:
    pkg = decode_canonical(data)
    _check(pkg, {"proposal": PROPOSAL_SPEC, "copies": [hexs()]})
    c = decode_proposal(canonical(pkg["proposal"]))
    return {"proposal": c, "copies": _decode_embedded(pkg["copies"], c["origin"], "copies")}


def decode_bundle(data: bytes) -> dict:
    b = decode_canonical(data)
    _check(b, {"format": _lit("spine-bundle-v1"), "origin": H, "tip": H, "objects": [hexs()]})
    objects = []
    for h in b["objects"]:
        raw = bytes.fromhex(h)
        objects.append(decode_object(raw))
    _sorted_by_id(objects, "objects")
    return {"origin": b["origin"], "tip": b["tip"], "objects": objects}


# ---- constructors (all return encoded bytes, except where noted) ----

def make_genesis(nonce: str, members: list, devices: list) -> bytes:
    data = canonical({
        "format": FORMAT, "kind": "genesis", "nonce": nonce, "policy": POLICY,
        "members": members, "devices": devices,
    })
    decode_genesis(data)
    return data


def make_event(seed: bytes, origin: str, kind: str, parent: str, payload) -> bytes:
    body = {
        "format": FORMAT, "origin": origin, "kind": kind, "author": public_key(seed),
        "parents": [parent], "payload": payload,
    }
    data = canonical({"body": body, "sig": sign(seed, body)})
    decode_event(data)
    return data


def _id_of(encoded: bytes) -> str:
    return decode_object(encoded)["id"]


def _sorted_hex(objects: list) -> list:
    return [e.hex() for e in sorted(objects, key=_id_of)]


def make_reconciliation_payload(proposal: dict, copies: list, approvals: list) -> dict:
    return {"proposal": proposal, "copies": _sorted_hex(copies), "approvals": _sorted_hex(approvals)}


def make_package(proposal: dict, copies: list) -> bytes:
    data = canonical({"proposal": proposal, "copies": _sorted_hex(copies)})
    decode_package(data)
    return data


def make_bundle(origin: str, tip: str, objects: list) -> bytes:
    data = canonical({
        "format": "spine-bundle-v1", "origin": origin, "tip": tip,
        "objects": _sorted_hex(objects),
    })
    decode_bundle(data)
    return data
