from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


# The artifact version each of these contracts is produced at and accepted at.
# Each artifact versions its own serialized contract: WELCOME_BUNDLE_VERSION covers
# the signed bundle fields, SIGNED_WELCOME_BUNDLE_VERSION covers only the outer
# wrapper fields. Neither has anything to do with the encryption-envelope version
# in cuttlefish, which selects the sealed-envelope format instead.
JOIN_REQUEST_ARTIFACT_VERSION = 2
WELCOME_BUNDLE_VERSION = 2
SIGNED_WELCOME_BUNDLE_VERSION = 1


def _require_optional_string(label: str, value: Any) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")


@dataclass(frozen=True)
class JoinRequestArtifact:
    version: int
    device_id_hex: str
    device_encryption_public_key_hex: str
    device_signing_public_key_hex: str
    # The joining device's own label for itself, chosen before it has an identity.
    device_label: str | None

    def __post_init__(self) -> None:
        _require_optional_string("device_label", self.device_label)


@dataclass(frozen=True)
class WelcomeBundle:
    version: int
    participant_hex: str
    joining_device_id_hex: str
    joining_device_public_key_hex: str
    identity_label: str
    remote_descriptor: dict[str, Any]
    issued_at: str
    expires_at: str
    # The authorizing device's own label for itself, absent when it has none.
    # Never falls back to the participant's name: the joiner is checking which
    # device authorized the link, and a person's name cannot answer that.
    authorizing_device_label: str | None

    def __post_init__(self) -> None:
        _require_optional_string("authorizing_device_label", self.authorizing_device_label)


@dataclass(frozen=True)
class SignedWelcomeBundle:
    version: int
    bundle: WelcomeBundle
    authorizing_device_id_hex: str
    signature_hex: str


def _canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require_version(label: str, payload: Any, supported: int) -> None:
    """Reject an artifact whose declared version this build does not implement.

    Called before the payload's other fields are read, because a future version
    is free to change which fields exist.
    """
    version = payload.get("version") if isinstance(payload, dict) else None
    if type(version) is not int or version != supported:
        raise ValueError(
            f"Unsupported {label} version: {version!r} (this installation supports {supported})"
        )


def canonical_join_request_artifact_bytes(artifact: JoinRequestArtifact) -> bytes:
    return _canonical_json(asdict(artifact))


def canonical_welcome_bundle_bytes(bundle: WelcomeBundle) -> bytes:
    return _canonical_json(asdict(bundle))


def serialize_join_request_artifact(artifact: JoinRequestArtifact) -> str:
    return base64.b64encode(canonical_join_request_artifact_bytes(artifact)).decode("ascii")


def deserialize_join_request_artifact(encoded: str) -> JoinRequestArtifact:
    payload = json.loads(base64.b64decode(encoded.encode("ascii")).decode("utf-8"))
    _require_version("join request artifact", payload, JOIN_REQUEST_ARTIFACT_VERSION)
    return JoinRequestArtifact(**payload)


def join_request_auth_string(artifact: JoinRequestArtifact) -> str:
    digest = hashlib.sha256(canonical_join_request_artifact_bytes(artifact)).hexdigest().upper()
    short = digest[:16]
    return "-".join(short[i:i + 4] for i in range(0, len(short), 4))


def welcome_bundle_aad(
    *,
    joining_device_id_hex: str,
    version: int,
) -> bytes:
    return (
        f"SmallSeaWelcomeBundle|v={version}|device={joining_device_id_hex}"
    ).encode("utf-8")


def serialize_welcome_bundle_plaintext(bundle: WelcomeBundle) -> bytes:
    return canonical_welcome_bundle_bytes(bundle)


def _welcome_bundle_from_payload(payload: Any) -> WelcomeBundle:
    _require_version("welcome bundle", payload, WELCOME_BUNDLE_VERSION)
    return WelcomeBundle(**payload)


def deserialize_welcome_bundle_plaintext(data: bytes) -> WelcomeBundle:
    return _welcome_bundle_from_payload(json.loads(data.decode("utf-8")))


def serialize_signed_welcome_bundle_plaintext(bundle: SignedWelcomeBundle) -> bytes:
    payload = {
        "version": bundle.version,
        "bundle": asdict(bundle.bundle),
        "authorizing_device_id_hex": bundle.authorizing_device_id_hex,
        "signature_hex": bundle.signature_hex,
    }
    return _canonical_json(payload)


def deserialize_signed_welcome_bundle_plaintext(data: bytes) -> SignedWelcomeBundle:
    payload = json.loads(data.decode("utf-8"))
    _require_version("signed welcome bundle", payload, SIGNED_WELCOME_BUNDLE_VERSION)
    return SignedWelcomeBundle(
        version=payload["version"],
        bundle=_welcome_bundle_from_payload(payload["bundle"]),
        authorizing_device_id_hex=payload["authorizing_device_id_hex"],
        signature_hex=payload["signature_hex"],
    )


def welcome_bundle_confirmation_string(
    artifact: JoinRequestArtifact,
    bundle: WelcomeBundle,
    signature: bytes,
) -> str:
    digest = hashlib.sha256(
        canonical_join_request_artifact_bytes(artifact)
        + canonical_welcome_bundle_bytes(bundle)
        + signature
    ).hexdigest().upper()
    short = digest[:16]
    return "-".join(short[i:i + 4] for i in range(0, len(short), 4))
