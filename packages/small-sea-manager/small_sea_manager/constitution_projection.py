"""Project selected Constitution events into existing Core views."""

import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from wrasse_trust.identity import KeyCertificate, parse_cert_type, verify_cert
from wrasse_trust.keys import key_id_from_public

from small_sea_manager.constitution_store import add_event, append_local_event


class ProjectionError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def encode_certificate(cert: KeyCertificate, issuer_teammate_id: bytes) -> dict:
    return {
        "cert_id": cert.cert_id.hex(),
        "cert_type": cert.cert_type.value,
        "team_id": cert.team_id.hex() if cert.team_id is not None else None,
        "subject_key_id": cert.subject_key_id.hex(),
        "subject_public_key": cert.subject_public_key.hex(),
        "issuer_key_id": cert.issuer_key_id.hex(),
        "issuer_participant_id": cert.issuer_participant_id.hex(),
        "issued_at_iso": cert.issued_at_iso,
        "claims": cert.claims,
        "signature": cert.signature.hex(),
        "issuer_teammate_id": issuer_teammate_id.hex(),
    }


def decode_certificate(payload: dict) -> tuple[KeyCertificate, bytes]:
    try:
        cert = KeyCertificate(
            cert_id=bytes.fromhex(payload["cert_id"]),
            cert_type=parse_cert_type(payload["cert_type"]),
            team_id=bytes.fromhex(payload["team_id"]) if payload["team_id"] is not None else None,
            subject_key_id=bytes.fromhex(payload["subject_key_id"]),
            subject_public_key=bytes.fromhex(payload["subject_public_key"]),
            issuer_key_id=bytes.fromhex(payload["issuer_key_id"]),
            issuer_participant_id=bytes.fromhex(payload["issuer_participant_id"]),
            issued_at_iso=payload["issued_at_iso"],
            claims=payload["claims"],
            signature=bytes.fromhex(payload["signature"]),
        )
        teammate_id = bytes.fromhex(payload["issuer_teammate_id"])
    except (KeyError, TypeError, ValueError):
        raise ProjectionError("bad_certificate_payload") from None
    return cert, teammate_id


def apply_event(conn: Connection, event) -> None:
    if event.event_type != "key_certificate":
        return
    cert, issuer_teammate_id = decode_certificate(event.payload)
    if key_id_from_public(event.signer_public_key) != cert.issuer_key_id:
        raise ProjectionError("wrong_certificate_signer")
    try:
        valid = verify_cert(cert, event.signer_public_key)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ProjectionError("bad_certificate_signature")
    values = {
        "cert_id": cert.cert_id,
        "cert_type": cert.cert_type.value,
        "subject_key_id": cert.subject_key_id,
        "subject_public_key": cert.subject_public_key,
        "issuer_key_id": cert.issuer_key_id,
        "issuer_teammate_id": issuer_teammate_id,
        "issued_at": cert.issued_at_iso,
        "claims": json.dumps(cert.claims, sort_keys=True),
        "signature": cert.signature,
    }
    existing = conn.execute(text("SELECT cert_type, subject_key_id, subject_public_key, issuer_key_id, issuer_teammate_id, issued_at, claims, signature FROM key_certificate WHERE cert_id=:cert_id"), values).first()
    content = tuple(values[k] for k in ("cert_type", "subject_key_id", "subject_public_key", "issuer_key_id", "issuer_teammate_id", "issued_at", "claims", "signature"))
    if existing is not None:
        if tuple(existing) != content:
            raise ProjectionError("certificate_conflict")
        return
    conn.execute(text("INSERT INTO key_certificate (cert_id, cert_type, subject_key_id, subject_public_key, issuer_key_id, issuer_teammate_id, issued_at, claims, signature) VALUES (:cert_id, :cert_type, :subject_key_id, :subject_public_key, :issuer_key_id, :issuer_teammate_id, :issued_at, :claims, :signature)"), values)


def store_and_project(conn: Connection, event) -> tuple[str, list]:
    status, newly_stored = add_event(conn, event)
    for stored_event in newly_stored:
        apply_event(conn, stored_event)
    return status, newly_stored


def record_key_certificate(conn: Connection, cert: KeyCertificate, issuer_teammate_id: bytes, issuer_private_key: bytes) -> None:
    """Record a certificate this device authored: sign a wrapping event, store it, project it."""
    newly_stored = append_local_event(
        conn,
        "key_certificate",
        encode_certificate(cert, issuer_teammate_id),
        issuer_private_key,
    )
    for stored_event in newly_stored:
        apply_event(conn, stored_event)
