# Cuttlefish — Trust chain traversal and policy evaluation
#
# Cuttlefish does not mandate a single trust metric. All certificates are
# public and the cert graph can be queried by any party. Callers specify their
# own trust policy; this module provides the traversal primitives.
#
# Initial implementation: make the full cert graph available, defer policy
# to callers. Future work: define common policy primitives (thresholds,
# weighted scores, time-decay, etc.).

from __future__ import annotations

from dataclasses import dataclass, field

from .identity import KeyCertificate, RevocationCertificate
from .keys import ParticipantKey


@dataclass
class CertGraph:
    """A local view of all known certificates and revocations.

    In practice this is populated from cloud storage; the graph is a
    point-in-time snapshot.
    """

    certs: list[KeyCertificate] = field(default_factory=list)
    revocations: list[RevocationCertificate] = field(default_factory=list)

    def certs_for_key(self, key_id: bytes) -> list[KeyCertificate]:
        """Return all certs where subject_key_id == key_id."""
        raise NotImplementedError

    def is_revoked(self, key_id: bytes) -> bool:
        raise NotImplementedError

    def issuer_keys_for_key(self, key_id: bytes) -> list[bytes]:
        """Return the key_ids of all keys that have certified key_id."""
        raise NotImplementedError


@dataclass
class TrustPath:
    """A chain of certificates connecting an anchor key to a subject key."""

    subject_key_id: bytes
    anchor_key_id: bytes
    chain: list[KeyCertificate]


def find_trust_paths(
    subject_key_id: bytes,
    anchor_key_ids: set[bytes],
    graph: CertGraph,
    max_depth: int = 10,
) -> list[TrustPath]:
    """Find all cert chains from any anchor key to the subject key.

    Returns an empty list if no path exists.
    Revoked keys are not traversed.
    """
    raise NotImplementedError


def load_cert_graph_from_storage(storage_download_fn) -> CertGraph:
    """Fetch and deserialize all known certs from cloud storage.

    storage_download_fn: callable(path: str) -> bytes
    """
    raise NotImplementedError
