"""The Cod Sync link format and its wire codec.

A link is one entry in a store's chain of deltas. It names the `main` head it
publishes, the bundle carrying that head's objects, and the link it extends.
This module owns the only YAML in Cod Sync: stores move opaque bytes and
protocol.py coordinates Git, so neither of them parses a link.

Version 2 is a named mapping with exactly these top-level keys:

    version: 2.0.0
    link_id: 0123456789abcdef
    head: <main commit object id>
    bundle_id: fedcba9876543210
    previous:
      link_id: 1111111111111111
      head: <previous main commit object id>
    extensions: {}

The first link in a chain has `previous: null` and a full bundle with no Git
prerequisite. Every later link's bundle includes `previous.head` among its
actual prerequisites; any extras must already be ancestors of that head.

The core is strict: an unknown or missing top-level key is an error. The
extension point is open: unknown keys inside `extensions` are accepted,
preserved through a decode/encode round trip, and covered by canonical
signing. Minor and patch evolution may add ignorable data there; a change to
traversal, validation, or adoption semantics requires a new major.
"""

import base64
import secrets
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

import yaml

COD_SYNC_VERSION = "2.0.0"

#: The one link major this reader understands.
SUPPORTED_MAJOR = 2

#: Key inside `extensions` holding per-teammate signatures. Excluded from the
#: canonical signed bytes; everything else in `extensions` is covered.
SIGNATURES_KEY = "signatures"

_TOP_LEVEL_KEYS = frozenset(
    {"version", "link_id", "head", "bundle_id", "previous", "extensions"}
)
_PREVIOUS_KEYS = frozenset({"link_id", "head"})


class LinkFormatError(Exception):
    """Raised when link bytes do not decode as a well-formed link."""


class UnsupportedLinkVersionError(Exception):
    """Raised when a link declares a major version this reader does not implement.

    Distinct from LinkFormatError: the bytes may be perfectly well-formed for a
    newer Cod Sync, so the answer is an upgrade rather than a repair.
    """

    def __init__(self, version: str, supported_major: int = SUPPORTED_MAJOR):
        super().__init__(
            f"link format version {version} is not supported by this reader "
            f"(major {supported_major})"
        )
        self.version = version
        self.supported_major = supported_major


def new_uid(num_bytes: int = 8) -> str:
    """Return a fresh random locator for a link or bundle."""
    return secrets.token_bytes(num_bytes).hex()


@dataclass(frozen=True)
class Predecessor:
    """The link a link extends, and the `main` head that link published."""

    link_id: str
    head: str


@dataclass(frozen=True)
class Link:
    """One published `main` head and the bundle that carries it."""

    link_id: str
    head: str
    bundle_id: str
    previous: Optional[Predecessor] = None
    version: str = COD_SYNC_VERSION
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))

    def with_extensions(self, extensions: Mapping[str, Any]) -> "Link":
        """Return a copy carrying different extensions."""
        return Link(
            link_id=self.link_id,
            head=self.head,
            bundle_id=self.bundle_id,
            previous=self.previous,
            version=self.version,
            extensions=extensions,
        )

    @property
    def version_tuple(self) -> Tuple[int, ...]:
        return parse_version(self.version)


@dataclass(frozen=True)
class BundleDescriptor:
    """What a bundle file itself advertises, as read from its header.

    Separate from Link because the whole point of the comparison is that a
    link's claims and a bundle's contents are independent sources.
    """

    head: Optional[str]
    prerequisites: frozenset


def parse_version(version: str) -> Tuple[int, ...]:
    """Return version as a comparable tuple of integers."""
    if not isinstance(version, str):
        raise LinkFormatError(f"version must be a string, got {type(version).__name__}")
    parts = version.split(".")
    if len(parts) != 3:
        raise LinkFormatError(f"malformed version {version!r}")
    try:
        return tuple(int(part) for part in parts)
    except ValueError as exc:
        raise LinkFormatError(f"malformed version {version!r}") from exc


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise LinkFormatError(f"{what} must be a non-empty string, got {value!r}")
    return value


def _require_uid(value: Any, what: str) -> str:
    value = _require_str(value, what)
    if len(value) != 16 or any(c not in "0123456789abcdef" for c in value):
        raise LinkFormatError(f"{what} must be a 16-character lowercase hex string")
    return value


def _require_object_id(value: Any, what: str) -> str:
    value = _require_str(value, what)
    if len(value) not in (40, 64) or any(
        c not in "0123456789abcdef" for c in value
    ):
        raise LinkFormatError(f"{what} must be a lowercase SHA-1 or SHA-256 object id")
    return value


def _decode_previous(value: Any) -> Optional[Predecessor]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LinkFormatError(f"previous must be a mapping or null, got {value!r}")
    keys = set(value)
    if keys != _PREVIOUS_KEYS:
        raise LinkFormatError(
            f"previous must have exactly {sorted(_PREVIOUS_KEYS)}, got {sorted(keys)}"
        )
    return Predecessor(
        link_id=_require_uid(value["link_id"], "previous.link_id"),
        head=_require_object_id(value["head"], "previous.head"),
    )


def decode_link(data: bytes) -> Link:
    """Decode link bytes, rejecting anything that is not a supported link.

    Raises UnsupportedLinkVersionError for a readable link from a newer major
    and LinkFormatError for everything else.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise LinkFormatError(f"link bytes must be bytes, got {type(data).__name__}")
    try:
        parsed = yaml.safe_load(bytes(data).decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise LinkFormatError(f"link is not valid YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise LinkFormatError(
            f"link must be a mapping, got {type(parsed).__name__}"
        )

    version = _require_str(parsed.get("version"), "version")
    if parse_version(version)[0] != SUPPORTED_MAJOR:
        raise UnsupportedLinkVersionError(version)

    keys = set(parsed)
    if keys != _TOP_LEVEL_KEYS:
        missing = sorted(_TOP_LEVEL_KEYS - keys)
        unknown = sorted(keys - _TOP_LEVEL_KEYS)
        raise LinkFormatError(
            f"link has missing keys {missing} and unknown keys {unknown}"
        )

    extensions = parsed["extensions"]
    if not isinstance(extensions, dict):
        raise LinkFormatError(
            f"extensions must be a mapping, got {type(extensions).__name__}"
        )

    return Link(
        link_id=_require_uid(parsed["link_id"], "link_id"),
        head=_require_object_id(parsed["head"], "head"),
        bundle_id=_require_uid(parsed["bundle_id"], "bundle_id"),
        previous=_decode_previous(parsed["previous"]),
        version=version,
        extensions=extensions,
    )


def _link_mapping(link: Link) -> dict:
    previous = None
    if link.previous is not None:
        previous = {"link_id": link.previous.link_id, "head": link.previous.head}
    return {
        "version": link.version,
        "link_id": link.link_id,
        "head": link.head,
        "bundle_id": link.bundle_id,
        "previous": previous,
        "extensions": dict(link.extensions),
    }


def encode_link(link: Link) -> bytes:
    """Serialize a link to its wire bytes."""
    return yaml.dump(
        _link_mapping(link), default_flow_style=False, sort_keys=True
    ).encode("utf-8")


def canonical_link_bytes(link: Link) -> bytes:
    """Return the bytes a signature over this link covers.

    Everything except `extensions.signatures`, so unknown extensions added by
    another implementation remain attested.
    """
    mapping = _link_mapping(link)
    mapping["extensions"] = {
        key: value
        for key, value in mapping["extensions"].items()
        if key != SIGNATURES_KEY
    }
    return yaml.dump(mapping, default_flow_style=False, sort_keys=True).encode("utf-8")


def sign_link(private_key_bytes: bytes, canonical_bytes: bytes) -> str:
    """Sign canonical link bytes with a raw 32-byte Ed25519 private key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    return base64.b64encode(key.sign(canonical_bytes)).decode()


def verify_link_signature(
    public_key_bytes: bytes, signature_b64: str, canonical_bytes: bytes
) -> bool:
    """Verify an Ed25519 signature over canonical link bytes."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    try:
        key.verify(base64.b64decode(signature_b64), canonical_bytes)
        return True
    except InvalidSignature:
        return False


def signed_link(
    link: Link, signing_key: bytes, teammate_id: str, device_public_key
) -> Link:
    """Return a copy of link carrying this device's signature in extensions."""
    if device_public_key is None:
        raise ValueError("device_public_key is required when signing a link")
    if isinstance(device_public_key, bytes):
        device_public_key = device_public_key.hex()
    signature = sign_link(signing_key, canonical_link_bytes(link))
    extensions = dict(link.extensions)
    signatures = dict(extensions.get(SIGNATURES_KEY, {}))
    signatures[teammate_id] = {
        "device_public_key": device_public_key,
        "signature": signature,
    }
    extensions[SIGNATURES_KEY] = signatures
    return link.with_extensions(extensions)
