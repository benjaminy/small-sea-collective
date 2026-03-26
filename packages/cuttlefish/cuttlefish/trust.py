# Cuttlefish — Trust chain traversal and policy evaluation
#
# Cuttlefish does not mandate a single trust metric. All certificates are
# public and the cert graph can be queried by any party. Callers specify their
# own trust policy; this module provides the traversal primitives.
#
# The cert graph is a directed graph where each edge is a certificate:
#   issuer_key_id -> subject_key_id
# Trust paths are found by BFS from the subject key back toward anchor keys.

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .identity import KeyCertificate, RevocationCertificate, verify_cert, verify_revocation


@dataclass
class CertGraph:
    """A local view of all known certificates and revocations.

    In practice this is populated from cloud storage; the graph is a
    point-in-time snapshot.
    """

    certs: list[KeyCertificate] = field(default_factory=list)
    revocations: list[RevocationCertificate] = field(default_factory=list)

    # Maps built lazily for efficient lookups
    _by_subject: dict[bytes, list[KeyCertificate]] | None = field(
        default=None, repr=False,
    )
    _revoked_set: set[bytes] | None = field(default=None, repr=False)

    def _build_index(self):
        if self._by_subject is not None:
            return
        self._by_subject = {}
        for cert in self.certs:
            self._by_subject.setdefault(cert.subject_key_id, []).append(cert)

    def _build_revoked(self):
        if self._revoked_set is not None:
            return
        self._revoked_set = {r.revoked_key_id for r in self.revocations}

    def certs_for_key(self, key_id: bytes) -> list[KeyCertificate]:
        """Return all certs where subject_key_id == key_id."""
        self._build_index()
        return list(self._by_subject.get(key_id, []))

    def is_revoked(self, key_id: bytes) -> bool:
        self._build_revoked()
        return key_id in self._revoked_set

    def issuer_keys_for_key(self, key_id: bytes) -> list[bytes]:
        """Return the key_ids of all keys that have certified key_id."""
        return [c.issuer_key_id for c in self.certs_for_key(key_id)]

    def add_cert(self, cert: KeyCertificate):
        """Add a certificate to the graph (invalidates internal indexes)."""
        self.certs.append(cert)
        self._by_subject = None

    def add_revocation(self, rev: RevocationCertificate):
        """Add a revocation to the graph (invalidates internal indexes)."""
        self.revocations.append(rev)
        self._revoked_set = None


@dataclass
class TrustPath:
    """A chain of certificates connecting an anchor key to a subject key."""

    subject_key_id: bytes
    anchor_key_id: bytes
    chain: list[KeyCertificate]  # Ordered: anchor -> ... -> subject


def find_trust_paths(
    subject_key_id: bytes,
    anchor_key_ids: set[bytes],
    graph: CertGraph,
    max_depth: int = 10,
) -> list[TrustPath]:
    """Find all cert chains from any anchor key to the subject key.

    Uses BFS backwards from the subject through issuer edges.
    Returns an empty list if no path exists.
    Revoked keys are not traversed.
    """
    if subject_key_id in anchor_key_ids and not graph.is_revoked(subject_key_id):
        return [TrustPath(subject_key_id=subject_key_id, anchor_key_id=subject_key_id, chain=[])]

    # BFS: each queue entry is (current_key_id, cert_chain_so_far)
    # We walk backwards: from subject, following issuer edges.
    queue: deque[tuple[bytes, list[KeyCertificate]]] = deque()
    queue.append((subject_key_id, []))
    visited: set[bytes] = {subject_key_id}
    results: list[TrustPath] = []

    while queue:
        current_key_id, chain = queue.popleft()

        if len(chain) >= max_depth:
            continue

        for cert in graph.certs_for_key(current_key_id):
            issuer_id = cert.issuer_key_id

            if graph.is_revoked(issuer_id):
                continue

            new_chain = [cert] + chain  # Prepend: we're walking backwards

            if issuer_id in anchor_key_ids:
                results.append(TrustPath(
                    subject_key_id=subject_key_id,
                    anchor_key_id=issuer_id,
                    chain=new_chain,
                ))
                continue

            if issuer_id not in visited:
                visited.add(issuer_id)
                queue.append((issuer_id, new_chain))

    return results
