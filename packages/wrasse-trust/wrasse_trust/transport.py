from __future__ import annotations

import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .identity import KeyCertificate, parse_cert_type, trusted_device_keys_for_teammate


@dataclass(frozen=True)
class TeammateTransportAnnouncement:
    announcement_id: bytes
    teammate_id: bytes
    protocol: str
    url: str
    bucket: str
    announced_at: str
    signer_key_id: bytes
    signature: bytes


@dataclass(frozen=True)
class TeammateBerthStorageAnnouncement:
    announcement_id: bytes
    teammate_id: bytes
    berth_id: bytes
    protocol: str
    url: str
    location: str
    announced_at: str
    signer_key_id: bytes
    signature: bytes


@dataclass(frozen=True)
class TransportEndpoint:
    protocol: str
    url: str
    bucket: str


@dataclass(frozen=True)
class EffectiveTransportSelection:
    status: str
    transport: TransportEndpoint | None
    announcement_id: bytes | None = None
    signer_key_id: bytes | None = None


def key_certificate_from_team_db_record(
    *,
    team_id: bytes,
    cert_id: bytes,
    cert_type: str,
    subject_key_id: bytes,
    subject_public_key: bytes,
    issuer_key_id: bytes,
    issuer_teammate_id: bytes,
    issued_at: str,
    claims_json: str,
    signature: bytes,
) -> KeyCertificate:
    """Rebuild a team DB certificate row into a KeyCertificate.

    Team DB rows intentionally omit `team_id`, so callers must inject the
    enclosing team's ID when bridging DB rows into wrasse-trust types.
    """
    return KeyCertificate(
        cert_id=cert_id,
        cert_type=parse_cert_type(cert_type),
        team_id=team_id,
        subject_key_id=subject_key_id,
        subject_public_key=subject_public_key,
        issuer_key_id=issuer_key_id,
        issuer_participant_id=issuer_teammate_id,
        issued_at_iso=issued_at,
        claims=json.loads(claims_json),
        signature=signature,
    )


def canonical_teammate_transport_announcement_bytes(
    announcement: TeammateTransportAnnouncement,
) -> bytes:
    # `signature` is intentionally excluded: callers sign these canonical bytes
    # and store the resulting signature alongside the row.
    obj = {
        "announcement_id": announcement.announcement_id.hex(),
        "announced_at": announcement.announced_at,
        "bucket": announcement.bucket,
        "teammate_id": announcement.teammate_id.hex(),
        "protocol": announcement.protocol,
        "signer_key_id": announcement.signer_key_id.hex(),
        "url": announcement.url,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_teammate_berth_storage_announcement_bytes(
    announcement: TeammateBerthStorageAnnouncement,
) -> bytes:
    # `signature` is intentionally excluded: callers sign these canonical bytes
    # and store the resulting signature alongside the row.
    obj = {
        "announcement_id": announcement.announcement_id.hex(),
        "announced_at": announcement.announced_at,
        "berth_id": announcement.berth_id.hex(),
        "location": announcement.location,
        "teammate_id": announcement.teammate_id.hex(),
        "protocol": announcement.protocol,
        "signer_key_id": announcement.signer_key_id.hex(),
        "url": announcement.url,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_teammate_transport_announcement_signature(
    announcement: TeammateTransportAnnouncement,
    signer_public_key: bytes,
) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(signer_public_key).verify(
            announcement.signature,
            canonical_teammate_transport_announcement_bytes(announcement),
        )
        return True
    except InvalidSignature:
        return False


def verify_teammate_berth_storage_announcement_signature(
    announcement: TeammateBerthStorageAnnouncement,
    signer_public_key: bytes,
) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(signer_public_key).verify(
            announcement.signature,
            canonical_teammate_berth_storage_announcement_bytes(announcement),
        )
        return True
    except InvalidSignature:
        return False


def select_effective_teammate_transport(
    *,
    teammate_id: bytes,
    announcements: list[TeammateTransportAnnouncement],
    certs: list[KeyCertificate] | None = None,
    team_id: bytes,
    device_public_keys_by_key_id: dict[bytes, bytes],
    trusted_public_keys: set[bytes] | None = None,
) -> EffectiveTransportSelection:
    if trusted_public_keys is None:
        if certs is None:
            raise ValueError("certs are required when trusted_public_keys is not provided")
        trusted_public_keys = trusted_device_keys_for_teammate(certs, team_id, teammate_id)
    relevant = [
        announcement for announcement in announcements if announcement.teammate_id == teammate_id
    ]
    relevant.sort(key=lambda announcement: announcement.announcement_id, reverse=True)

    for announcement in relevant:
        signer_public_key = device_public_keys_by_key_id.get(announcement.signer_key_id)
        if signer_public_key is None:
            continue
        if signer_public_key not in trusted_public_keys:
            continue
        if not verify_teammate_transport_announcement_signature(
            announcement,
            signer_public_key,
        ):
            continue
        return EffectiveTransportSelection(
            status="announced",
            transport=TransportEndpoint(
                protocol=announcement.protocol,
                url=announcement.url,
                bucket=announcement.bucket,
            ),
            announcement_id=announcement.announcement_id,
            signer_key_id=announcement.signer_key_id,
        )

    return EffectiveTransportSelection(status="missing", transport=None)


def select_effective_teammate_berth_storage(
    *,
    teammate_id: bytes,
    berth_id: bytes,
    announcements: list[TeammateBerthStorageAnnouncement],
    certs: list[KeyCertificate] | None = None,
    team_id: bytes,
    device_public_keys_by_key_id: dict[bytes, bytes],
    trusted_public_keys: set[bytes] | None = None,
) -> EffectiveTransportSelection:
    if trusted_public_keys is None:
        if certs is None:
            raise ValueError("certs are required when trusted_public_keys is not provided")
        trusted_public_keys = trusted_device_keys_for_teammate(certs, team_id, teammate_id)
    relevant = [
        announcement
        for announcement in announcements
        if announcement.teammate_id == teammate_id and announcement.berth_id == berth_id
    ]
    relevant.sort(key=lambda announcement: announcement.announcement_id, reverse=True)

    for announcement in relevant:
        signer_public_key = device_public_keys_by_key_id.get(announcement.signer_key_id)
        if signer_public_key is None:
            continue
        if signer_public_key not in trusted_public_keys:
            continue
        if not verify_teammate_berth_storage_announcement_signature(
            announcement,
            signer_public_key,
        ):
            continue
        return EffectiveTransportSelection(
            status="announced",
            transport=TransportEndpoint(
                protocol=announcement.protocol,
                url=announcement.url,
                bucket=announcement.location,
            ),
            announcement_id=announcement.announcement_id,
            signer_key_id=announcement.signer_key_id,
        )

    return EffectiveTransportSelection(status="missing", transport=None)
