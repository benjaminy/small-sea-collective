"""Project selected Constitution events into existing Core views."""

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from wrasse_trust.identity import KeyCertificate, parse_cert_type, verify_cert
from wrasse_trust.keys import key_id_from_public

from small_sea_manager import berth_authority
from small_sea_manager.constitution_store import add_event, append_local_event
from wrasse_trust.constitution import derive_record_id, verify_constitution_record


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
    check_event(event)
    if event.event_type == "workhorse_delegation":
        _write_workhorse_delegation(conn, event)
    elif event.event_type == "integration_mode_change":
        _write_integration_mode_change(conn, event)
    elif event.event_type == "key_certificate":
        _write_certificate(conn, event)


def check_event(event) -> None:
    if event.event_type == "workhorse_delegation":
        _check_workhorse_delegation(event)
    elif event.event_type == "integration_mode_change":
        _check_integration_mode_change(event)
    elif event.event_type == "key_certificate":
        _check_certificate(event)
    elif event.event_type == "teammate_removed":
        _check_teammate_removal(event)


def _check_teammate_removal(event) -> None:
    try:
        payload = event.payload
        if set(payload) != {"version", "team_id", "author_teammate_id", "teammate_id"}:
            raise ValueError
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ValueError
        for field in ("team_id", "author_teammate_id", "teammate_id"):
            if not bytes.fromhex(payload[field]):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ProjectionError("bad_removal_payload") from None


def _check_certificate(event) -> dict:
    cert, issuer_teammate_id = decode_certificate(event.payload)
    try:
        lengths = {"cert_id": 16, "subject_key_id": 16, "subject_public_key": 32, "issuer_key_id": 16, "signature": 64}
        if any(len(getattr(cert, field)) != size for field, size in lengths.items()) or not issuer_teammate_id or not cert.issuer_participant_id:
            raise ValueError
        if cert.team_id is not None and not cert.team_id:
            raise ValueError
        if not isinstance(cert.issued_at_iso, str) or not isinstance(cert.claims, dict):
            raise ValueError
        signer_key_id = key_id_from_public(event.signer_public_key)
    except (TypeError, ValueError):
        raise ProjectionError("bad_certificate_payload") from None
    if signer_key_id != cert.issuer_key_id:
        raise ProjectionError("wrong_certificate_signer")
    try:
        valid = verify_cert(cert, event.signer_public_key)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ProjectionError("bad_certificate_signature")
    return {
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


def _write_certificate(conn: Connection, event) -> None:
    values = _check_certificate(event)
    existing = conn.execute(text("SELECT cert_type, subject_key_id, subject_public_key, issuer_key_id, issuer_teammate_id, issued_at, claims, signature FROM key_certificate WHERE cert_id=:cert_id"), values).first()
    content = tuple(values[k] for k in ("cert_type", "subject_key_id", "subject_public_key", "issuer_key_id", "issuer_teammate_id", "issued_at", "claims", "signature"))
    if existing is not None:
        if tuple(existing) != content:
            raise ProjectionError("certificate_conflict")
        return
    conn.execute(text("INSERT INTO key_certificate (cert_id, cert_type, subject_key_id, subject_public_key, issuer_key_id, issuer_teammate_id, issued_at, claims, signature) VALUES (:cert_id, :cert_type, :subject_key_id, :subject_public_key, :issuer_key_id, :issuer_teammate_id, :issued_at, :claims, :signature)"), values)
    # Teammate and device rows are derived from certificates, so every path
    # that stores one (local authoring, admission import, Core union) agrees.
    _ensure_teammate(conn, values["issuer_teammate_id"])
    subject_teammate = json.loads(values["claims"]).get("teammate_id")
    if isinstance(subject_teammate, str):
        try:
            subject_teammate_id = bytes.fromhex(subject_teammate)
        except ValueError:
            return
        _ensure_teammate(conn, subject_teammate_id)
        conn.execute(text(
            "INSERT OR IGNORE INTO team_device (device_key_id, teammate_id, public_key, created_at) "
            "VALUES (:device_key_id, :teammate_id, :public_key, :created_at)"
        ), {"device_key_id": values["subject_key_id"], "teammate_id": subject_teammate_id,
            "public_key": values["subject_public_key"], "created_at": values["issued_at"]})


def _ensure_teammate(conn, teammate_id: bytes) -> None:
    conn.execute(text("INSERT OR IGNORE INTO teammate (id) VALUES (:id)"), {"id": teammate_id})


_DELEGATION_COLUMNS = ("record_id", "schema_version", "berth_id", "workhorse_public_key", "delegator_teammate_id", "delegator_public_key", "signature")
_MODE_COLUMNS = ("record_id", "record_type", "author_teammate_id", "author_device_key_id", "created_at", "anchor_commit", "constitution_digest", "constitution_snapshot_json", "schema_version", "teammate_id", "berth_id", "mode", "signature")


def _decode_row(payload, columns, binary_columns):
    try:
        values = {column: payload[column] for column in columns}
        for column in binary_columns:
            values[column] = bytes.fromhex(values[column])
        return values
    except (KeyError, TypeError, ValueError):
        raise ProjectionError("bad_record_payload") from None


def _check_workhorse_delegation(event):
    values = _decode_row(event.payload, _DELEGATION_COLUMNS, {"record_id", "berth_id", "delegator_teammate_id", "delegator_public_key", "signature"})
    try:
        team_id = bytes.fromhex(event.payload["team_id"])
        if not team_id or len(values["record_id"]) != 16 or not values["berth_id"] or not values["delegator_teammate_id"] or len(values["delegator_public_key"]) != 32 or len(values["signature"]) != 64:
            raise ValueError
        if not isinstance(values["schema_version"], int) or isinstance(values["schema_version"], bool) or not isinstance(values["workhorse_public_key"], str):
            raise ValueError
        record = berth_authority.WorkhorseDelegation(
            team_id=team_id, berth_id=values["berth_id"], workhorse_public_key=values["workhorse_public_key"],
            delegator_teammate_id=values["delegator_teammate_id"], delegator_public_key=values["delegator_public_key"], signature=values["signature"],
        )
    except (KeyError, TypeError, ValueError):
        raise ProjectionError("bad_delegation_payload") from None
    if values["delegator_public_key"] != event.signer_public_key:
        raise ProjectionError("wrong_delegation_signer")
    if not record.signature_valid():
        raise ProjectionError("bad_delegation_signature")
    return values


def _write_workhorse_delegation(conn, event):
    values = _check_workhorse_delegation(event)
    existing = conn.execute(text("SELECT schema_version, berth_id, workhorse_public_key, delegator_teammate_id, delegator_public_key, signature FROM workhorse_delegation WHERE record_id=:record_id"), values).first()
    content = tuple(values[k] for k in _DELEGATION_COLUMNS[1:])
    if existing is not None:
        if tuple(existing) != content:
            raise ProjectionError("delegation_conflict")
        return
    conn.execute(text("INSERT INTO workhorse_delegation (record_id, schema_version, berth_id, workhorse_public_key, delegator_teammate_id, delegator_public_key, signature) VALUES (:record_id, :schema_version, :berth_id, :workhorse_public_key, :delegator_teammate_id, :delegator_public_key, :signature)"), values)


def _check_integration_mode_change(event):
    binary = {"record_id", "author_teammate_id", "author_device_key_id", "constitution_digest", "teammate_id", "berth_id", "signature"}
    values = _decode_row(event.payload, _MODE_COLUMNS, binary)
    try:
        lengths = {"record_id": 16, "author_device_key_id": 16, "signature": 64}
        if any(len(values[key]) != size for key, size in lengths.items()):
            raise ValueError
        if any(not values[key] for key in ("author_teammate_id", "constitution_digest", "teammate_id", "berth_id")):
            raise ValueError
        if not isinstance(values["record_type"], str) or not isinstance(values["created_at"], str) or not isinstance(values["schema_version"], int) or isinstance(values["schema_version"], bool) or not isinstance(values["mode"], str):
            raise ValueError
        if values["anchor_commit"] is not None and not isinstance(values["anchor_commit"], str):
            raise ValueError
        if not isinstance(values["constitution_snapshot_json"], str):
            raise ValueError
        record = berth_authority.ModeChangeRecord(
            author_teammate_id=values["author_teammate_id"], author_device_key_id=values["author_device_key_id"],
            created_at=values["created_at"], anchor_commit=values["anchor_commit"], constitution_digest=values["constitution_digest"],
            schema_version=values["schema_version"], teammate_id=values["teammate_id"], berth_id=values["berth_id"], mode=values["mode"], signature=values["signature"],
        )
    except (KeyError, TypeError, ValueError):
        raise ProjectionError("bad_mode_change_payload") from None
    canonical = record.canonical()
    if values["author_device_key_id"] != key_id_from_public(event.signer_public_key):
        raise ProjectionError("wrong_mode_change_signer")
    if derive_record_id(canonical) != values["record_id"] or not verify_constitution_record(event.signer_public_key, canonical, record.signature):
        raise ProjectionError("bad_mode_change_signature")
    # The snapshot is not signed itself; the signed digest binds it.
    try:
        snapshot_digest = hashlib.sha256(json.dumps(
            json.loads(values["constitution_snapshot_json"]), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).digest()
    except (TypeError, ValueError):
        raise ProjectionError("bad_mode_change_payload") from None
    if snapshot_digest != values["constitution_digest"]:
        raise ProjectionError("bad_mode_change_snapshot")
    return values


def _write_integration_mode_change(conn, event):
    values = _check_integration_mode_change(event)
    existing = conn.execute(text("SELECT record_type, author_teammate_id, author_device_key_id, created_at, anchor_commit, constitution_digest, constitution_snapshot_json, schema_version, teammate_id, berth_id, mode, signature FROM integration_mode_change WHERE record_id=:record_id"), values).first()
    content = tuple(values[k] for k in _MODE_COLUMNS[1:])
    if existing is not None:
        if tuple(existing) != content:
            raise ProjectionError("mode_change_conflict")
        return
    conn.execute(text("INSERT INTO integration_mode_change (record_id, record_type, author_teammate_id, author_device_key_id, created_at, anchor_commit, constitution_digest, constitution_snapshot_json, schema_version, teammate_id, berth_id, mode, signature) VALUES (:record_id, :record_type, :author_teammate_id, :author_device_key_id, :created_at, :anchor_commit, :constitution_digest, :constitution_snapshot_json, :schema_version, :teammate_id, :berth_id, :mode, :signature)"), values)
    _ensure_teammate(conn, values["author_teammate_id"])
    _ensure_teammate(conn, values["teammate_id"])


def record_workhorse_delegation(conn, delegation, delegator_private_key):
    """Record a delegation this device signed; a delegation already recorded adds no event."""
    if conn.execute(text("SELECT 1 FROM workhorse_delegation WHERE record_id = :record_id"), {"record_id": delegation.record_id}).first():
        return
    row = {"record_id": delegation.record_id, "schema_version": berth_authority.DELEGATION_VERSION, "berth_id": delegation.berth_id, "workhorse_public_key": delegation.workhorse_public_key, "delegator_teammate_id": delegation.delegator_teammate_id, "delegator_public_key": delegation.delegator_public_key, "signature": delegation.signature}
    payload = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in row.items()}
    payload["team_id"] = delegation.team_id.hex()
    for stored in append_local_event(conn, "workhorse_delegation", payload, delegator_private_key):
        apply_event(conn, stored)


def record_integration_mode_change(conn, row, author_private_key):
    payload = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in row.items()}
    for stored in append_local_event(conn, "integration_mode_change", payload, author_private_key):
        apply_event(conn, stored)


def store_and_project(conn: Connection, event) -> tuple[str, list]:
    check_event(event)
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


def record_teammate_removal(
    conn, team_id, author_teammate_id, target_teammate_id,
    author_team_device_private_key,
):
    """Append removal evidence without changing projection rows."""
    payload = {
        "version": 1, "team_id": team_id.hex(),
        "author_teammate_id": author_teammate_id.hex(),
        "teammate_id": target_teammate_id.hex(),
    }
    for stored in append_local_event(
        conn, "teammate_removed", payload, author_team_device_private_key,
    ):
        apply_event(conn, stored)
