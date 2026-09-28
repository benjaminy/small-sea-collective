"""Signed events for the Team Constitution DAG.

The event format is v0 and is not frozen. The module defines event bytes, IDs, and signatures, but does not store events.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

EVENT_FORMAT = "v0"


class EventInvalidError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ConstitutionEvent:
    event_id: bytes
    event_type: str
    payload: dict
    parents: tuple[bytes, ...]
    signer_public_key: bytes
    signature: bytes


def signed_body_bytes(
    event_type: str,
    payload: dict,
    parents: tuple[bytes, ...],
    signer_public_key: bytes,
) -> bytes:
    body = {
        "format": EVENT_FORMAT,
        "type": event_type,
        "payload": payload,
        "parents": [parent.hex() for parent in sorted(parents)],
        "signer": signer_public_key.hex(),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_event(
    event_type: str,
    payload: dict,
    parents: tuple[bytes, ...],
    signer_private_key: bytes,
) -> ConstitutionEvent:
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event_type must be a non-empty string")
    if len(set(parents)) != len(parents):
        raise ValueError("parents must be unique")
    sorted_parents = tuple(sorted(parents))
    private_key = Ed25519PrivateKey.from_private_bytes(signer_private_key)
    public_key = private_key.public_key().public_bytes_raw()
    body = signed_body_bytes(event_type, payload, sorted_parents, public_key)
    return ConstitutionEvent(
        event_id=hashlib.sha256(body).digest(),
        event_type=event_type,
        payload=payload,
        parents=sorted_parents,
        signer_public_key=public_key,
        signature=private_key.sign(body),
    )


def verify_event(event: ConstitutionEvent) -> None:
    if any(not isinstance(parent, bytes) or len(parent) != 32 for parent in event.parents):
        raise EventInvalidError("bad_parents")
    if tuple(sorted(event.parents)) != event.parents or len(set(event.parents)) != len(event.parents):
        raise EventInvalidError("bad_parents")
    body = signed_body_bytes(
        event.event_type, event.payload, event.parents, event.signer_public_key
    )
    if hashlib.sha256(body).digest() != event.event_id:
        raise EventInvalidError("bad_id")
    try:
        Ed25519PublicKey.from_public_bytes(event.signer_public_key).verify(
            event.signature, body
        )
    except (InvalidSignature, ValueError, TypeError):
        raise EventInvalidError("bad_signature") from None


def encode_event(event: ConstitutionEvent) -> bytes:
    body = json.loads(
        signed_body_bytes(
            event.event_type, event.payload, event.parents, event.signer_public_key
        )
    )
    body["id"] = event.event_id.hex()
    body["signature"] = event.signature.hex()
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decode_event(data: bytes) -> ConstitutionEvent:
    try:
        obj = json.loads(data.decode("utf-8"))
        keys = {"format", "type", "payload", "parents", "signer", "id", "signature"}
        if not isinstance(obj, dict) or set(obj) != keys or obj["format"] != EVENT_FORMAT:
            raise ValueError
        if (
            not isinstance(obj["type"], str)
            or not isinstance(obj["payload"], dict)
            or not isinstance(obj["parents"], list)
            or not all(isinstance(parent, str) for parent in obj["parents"])
            or not isinstance(obj["signer"], str)
            or not isinstance(obj["id"], str)
            or not isinstance(obj["signature"], str)
        ):
            raise ValueError
        event = ConstitutionEvent(
            event_id=bytes.fromhex(obj["id"]),
            event_type=obj["type"],
            payload=obj["payload"],
            parents=tuple(bytes.fromhex(parent) for parent in obj["parents"]),
            signer_public_key=bytes.fromhex(obj["signer"]),
            signature=bytes.fromhex(obj["signature"]),
        )
        if len(event.event_id) != 32 or len(event.signer_public_key) != 32:
            raise ValueError
        return event
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise EventInvalidError("bad_format") from None
