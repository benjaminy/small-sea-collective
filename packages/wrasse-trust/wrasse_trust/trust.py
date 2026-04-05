# Wrasse Trust — Trust chain traversal and policy evaluation
#
# Wrasse Trust does not mandate a single trust metric. All certificates are
# public and the cert graph can be queried by any party. Callers specify their
# own trust policy; this module provides the traversal primitives.

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .identity import KeyCertificate, RevocationCertificate


@dataclass
class CertGraph:
    """A local view of all known certificates and revocations."""

    certs: list[KeyCertificate] = field(default_factory=list)
    revocations: list[RevocationCertificate] = field(default_factory=list)

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
        self._build_index()
        return list(self._by_subject.get(key_id, []))

    def is_revoked(self, key_id: bytes) -> bool:
        self._build_revoked()
        return key_id in self._revoked_set

    def issuer_keys_for_key(self, key_id: bytes) -> list[bytes]:
        return [c.issuer_key_id for c in self.certs_for_key(key_id)]

    def add_cert(self, cert: KeyCertificate):
        self.certs.append(cert)
        self._by_subject = None

    def add_revocation(self, rev: RevocationCertificate):
        self.revocations.append(rev)
        self._revoked_set = None


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
    """Find all cert chains from any anchor key to the subject key."""
    if subject_key_id in anchor_key_ids and not graph.is_revoked(subject_key_id):
        return [TrustPath(subject_key_id=subject_key_id, anchor_key_id=subject_key_id, chain=[])]

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

            new_chain = [cert] + chain

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
