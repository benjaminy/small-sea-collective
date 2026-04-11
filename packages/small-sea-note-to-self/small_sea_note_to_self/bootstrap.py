from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class JoinRequestArtifact:
    version: int
    device_id_hex: str
    device_encryption_public_key_hex: str
    device_signing_public_key_hex: str


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
    authorizing_device_label: str


@dataclass(frozen=True)
class SignedWelcomeBundle:
    version: int
    bundle: WelcomeBundle
    authorizing_device_id_hex: str
    signature_hex: str


def _canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_join_request_artifact_bytes(artifact: JoinRequestArtifact) -> bytes:
    return _canonical_json(asdict(artifact))


def canonical_welcome_bundle_bytes(bundle: WelcomeBundle) -> bytes:
    return _canonical_json(asdict(bundle))


def serialize_join_request_artifact(artifact: JoinRequestArtifact) -> str:
    return base64.b64encode(canonical_join_request_artifact_bytes(artifact)).decode("ascii")


def deserialize_join_request_artifact(encoded: str) -> JoinRequestArtifact:
    payload = json.loads(base64.b64decode(encoded.encode("ascii")).decode("utf-8"))
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


def deserialize_welcome_bundle_plaintext(data: bytes) -> WelcomeBundle:
    payload = json.loads(data.decode("utf-8"))
    return WelcomeBundle(**payload)


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
    return SignedWelcomeBundle(
        version=payload["version"],
        bundle=WelcomeBundle(**payload["bundle"]),
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
