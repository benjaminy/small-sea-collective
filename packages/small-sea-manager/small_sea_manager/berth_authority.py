"""Decide whether a signed commit had berth authority under a local trust view.

This is a transitional view (issue #266, decision D3).
It is built from the signed records that exist today: membership and
device-link certificates, signed `integration_mode_change` records, and
workhorse delegations.
It is NOT the Team Constitution basis; that DAG is unimplemented.
The version marker in the view identifier lets a later decision say which
kind of view it used.

Rules of view version 1:

- Trust starts at one explicitly adopted anchor key, never at whatever
  self-issued genesis membership the records happen to contain.
- The anchor's teammate holds authority on every berth.
  Another teammate holds a berth after a signed `automatic` mode record from a
  device of a teammate who holds that berth.
- Without a Constitution DAG, records cannot be ordered.
  A teammate with both `automatic` and `proposal-only` records for a berth has
  ambiguous authority, and so does anyone whose only grants come from
  ambiguous holders. Self-grants and grant cycles never create standing:
  unambiguous standing must chain from the anchor.
- The anchor-teammate rule is an explicit transitional ASSUMPTION, not a
  reconstruction. Today the founder's roles and app-activation roles are
  unsigned `berth_role` rows, and there is no signed evidence of who
  originated each berth (see the B5 status in Documentation/bootstrap-trust.md:
  "Berths have no signed origin at all"). When signed founder roles exist, derive
  standing from them instead.
- Current authorization is not proof of historical authority. A view judges
  work under the verifier's own current selection; it says nothing about
  whether the signer had authority when the work was made. The signer's
  `WorkContext.authority_view` is evidence only.
- Enrollment grants no purposes.
  Only a workhorse delegation (decision D4) lets a workhorse key sign work, and
  only when a device of a teammate holding the berth signed it.
"""

import base64
import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_ssh_public_key,
)

from cod_sync.verify import SignatureEvidence, SignatureEvidenceKind
from cod_sync.work_context import PURPOSES, WorkContext
from wrasse_trust.constitution import canonical_constitution_bytes
from wrasse_trust.identity import (
    CertType,
    KeyCertificate,
    verify_device_link_cert,
    verify_membership_cert,
)
from wrasse_trust.keys import key_id_from_public


VIEW_VERSION = b"ssc-transitional-authority-view/1"
DELEGATION_VERSION = 1


class MissingAuthorityAnchor(Exception):
    """This device has adopted no usable anchor for the team.

    Callers treat this as a MISSING_AUTHORITY pause, never as a default anchor.
    """


class DelegationError(ValueError):
    """A delegation record is malformed."""


def ssh_fingerprint(openssh_public_key: str) -> str:
    """Return Git's SHA256 fingerprint for one OpenSSH public key line."""
    blob = base64.b64decode(openssh_public_key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _normalized_ed25519_ssh_key(key: str) -> str:
    try:
        public_key = load_ssh_public_key(key.strip().encode("ascii"))
    except (ValueError, UnsupportedAlgorithm, UnicodeError) as exc:
        raise DelegationError("invalid workhorse public key") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise DelegationError("workhorse key must be Ed25519")
    return public_key.public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode("ascii")


@dataclass(frozen=True)
class WorkhorseDelegation:
    """A team-device key grants purposes on one berth to a workhorse key.

    The record carries no timestamp: a date cannot establish authority.
    """

    team_id: bytes
    berth_id: bytes
    purposes: tuple
    workhorse_public_key: str
    delegator_teammate_id: bytes
    delegator_public_key: bytes
    signature: bytes = b""

    def canonical(self) -> bytes:
        purposes = list(self.purposes)
        if not purposes or purposes != sorted(set(purposes)) or not set(purposes) <= PURPOSES:
            raise DelegationError("purposes must be a nonempty sorted set of known purposes")
        if self.workhorse_public_key != _normalized_ed25519_ssh_key(self.workhorse_public_key):
            raise DelegationError("workhorse key is not in normalized OpenSSH form")
        return json.dumps(
            {
                "record_type": "workhorse_delegation",
                "version": DELEGATION_VERSION,
                "team_id": self.team_id.hex(),
                "berth_id": self.berth_id.hex(),
                "purposes": purposes,
                "workhorse_public_key": self.workhorse_public_key,
                "delegator_teammate_id": self.delegator_teammate_id.hex(),
                "delegator_public_key": self.delegator_public_key.hex(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")

    @property
    def record_id(self) -> bytes:
        return hashlib.sha256(self.canonical()).digest()[:16]

    def signature_valid(self) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(self.delegator_public_key).verify(
                self.signature, self.canonical()
            )
            return True
        except (InvalidSignature, DelegationError, ValueError):
            return False


def sign_workhorse_delegation(
    *, team_id, berth_id, purposes, workhorse_public_key,
    delegator_teammate_id, delegator_private_key,
) -> WorkhorseDelegation:
    private_key = Ed25519PrivateKey.from_private_bytes(delegator_private_key)
    unsigned = WorkhorseDelegation(
        team_id=team_id,
        berth_id=berth_id,
        purposes=tuple(sorted(set(purposes))),
        workhorse_public_key=_normalized_ed25519_ssh_key(workhorse_public_key),
        delegator_teammate_id=delegator_teammate_id,
        delegator_public_key=private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )
    return WorkhorseDelegation(**{**unsigned.__dict__, "signature": private_key.sign(unsigned.canonical())})


@dataclass(frozen=True)
class ModeChangeRecord:
    """The signed fields of one `integration_mode_change` row."""

    author_teammate_id: bytes
    author_device_key_id: bytes
    created_at: str
    anchor_commit: str | None
    constitution_digest: bytes
    schema_version: int
    teammate_id: bytes
    berth_id: bytes
    mode: str
    signature: bytes

    def canonical(self) -> bytes:
        return canonical_constitution_bytes({
            "record_type": "integration_mode_change",
            "author_teammate_id": self.author_teammate_id.hex(),
            "author_device_key_id": self.author_device_key_id.hex(),
            "created_at": self.created_at,
            "anchor_commit": self.anchor_commit,
            "constitution_digest": self.constitution_digest.hex(),
            "schema_version": self.schema_version,
            "teammate_id": self.teammate_id.hex(),
            "berth_id": self.berth_id.hex(),
            "mode": self.mode,
        })


class Standing(Enum):
    HELD = "held"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class TransitionalView:
    team_id: bytes
    identifier: bytes
    trusted_keys: dict          # teammate_id -> frozenset of team-device public keys
    standing: dict              # (teammate_id, berth_id) -> Standing
    delegations: tuple          # accepted WorkhorseDelegation records

    def holds(self, teammate_id: bytes, berth_id: bytes):
        return self.standing.get((teammate_id, berth_id))

    def workhorse_public_keys(self) -> list[str]:
        """Keys to give SshCommitVerifier so its evidence can report KNOWN_KEY."""
        return sorted({d.workhorse_public_key for d in self.delegations})


def _anchored_trust(certs, team_id, anchor_public_key):
    """Trusted team-device keys per teammate, starting only at the anchor key."""
    trusted: dict[bytes, set[bytes]] = {}
    used: dict[bytes, bytes] = {}
    anchor_teammate = None
    changed = True
    while changed:
        changed = False
        for cert in certs:
            if cert.team_id != team_id or cert.cert_id in used:
                continue
            try:
                subject_teammate = bytes.fromhex(cert.claims.get("teammate_id"))
            except (TypeError, ValueError):
                continue
            ok = False
            if cert.cert_type == CertType.MEMBERSHIP:
                if cert.issuer_participant_id == subject_teammate:
                    # Self-issued genesis: only the adopted anchor, only once.
                    ok = (
                        anchor_teammate is None
                        and cert.subject_public_key == anchor_public_key
                        and verify_membership_cert(
                            cert, anchor_public_key, team_id,
                            subject_teammate, subject_teammate, anchor_public_key,
                        )
                    )
                    if ok:
                        anchor_teammate = subject_teammate
                else:
                    ok = any(
                        verify_membership_cert(
                            cert, key, team_id, cert.issuer_participant_id,
                            subject_teammate, cert.subject_public_key,
                        )
                        for key in sorted(trusted.get(cert.issuer_participant_id, ()))
                    )
            elif cert.cert_type == CertType.DEVICE_LINK:
                ok = any(
                    verify_device_link_cert(
                        cert, key, team_id, subject_teammate, cert.subject_public_key,
                    )
                    for key in sorted(trusted.get(subject_teammate, ()))
                )
            if ok:
                trusted.setdefault(subject_teammate, set()).add(cert.subject_public_key)
                used[cert.cert_id] = hashlib.sha256(
                    b"cert" + cert.cert_id + cert.signature
                ).digest()
                changed = True
    return anchor_teammate, trusted, list(used.values())


def _author_key(trusted, teammate_id, device_key_id):
    for key in trusted.get(teammate_id, ()):
        if key_id_from_public(key) == device_key_id:
            return key
    return None


def build_view(*, team_id, anchor_public_key, certs, mode_changes, delegations,
               berth_ids=()) -> TransitionalView:
    """Compute the view from records; only records that pass checks affect it.

    `berth_ids` lists the team's berths, so the anchor teammate holds them
    even before any record mentions them.
    """
    anchor_teammate, trusted, digests = _anchored_trust(certs, team_id, anchor_public_key)

    verified_modes = []
    for record in mode_changes:
        key = _author_key(trusted, record.author_teammate_id, record.author_device_key_id)
        if key is None or record.mode not in ("automatic", "proposal-only"):
            continue
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(record.signature, record.canonical())
        except InvalidSignature:
            continue
        verified_modes.append(record)

    berths = set(berth_ids) | {r.berth_id for r in verified_modes} | {d.berth_id for d in delegations}

    def has_automatic_grant(holders, teammate, berth):
        return teammate == anchor_teammate or any(
            r.teammate_id == teammate and r.berth_id == berth and r.mode == "automatic"
            and r.author_teammate_id in holders.get(berth, set())
            for r in verified_modes
        )

    # Least fixed point: who has any automatic grant chained from the anchor.
    holders: dict[bytes, set[bytes]] = {}
    changed = anchor_teammate is not None
    while changed:
        changed = False
        for berth in berths:
            for teammate in trusted:
                if teammate not in holders.setdefault(berth, set()) and has_automatic_grant(holders, teammate, berth):
                    holders[berth].add(teammate)
                    changed = True

    used_modes = [
        r for r in verified_modes if r.author_teammate_id in holders.get(r.berth_id, set())
    ]
    # Unambiguous standing is a separate least fixed point rooted at the
    # anchor. A directly conflicted teammate never enters it, a self-grant
    # never counts, and a cycle cannot bootstrap itself because it needs an
    # already-held author outside it.
    conflicted = {
        (r.teammate_id, r.berth_id) for r in used_modes if r.mode == "proposal-only"
    }
    held = {
        (anchor_teammate, berth) for berth in berths
        if anchor_teammate is not None and (anchor_teammate, berth) not in conflicted
    }
    changed = True
    while changed:
        changed = False
        for r in used_modes:
            key = (r.teammate_id, r.berth_id)
            if (
                r.mode == "automatic" and key not in held and key not in conflicted
                and r.author_teammate_id != r.teammate_id
                and (r.author_teammate_id, r.berth_id) in held
            ):
                held.add(key)
                changed = True
    standing = {
        (teammate, berth): Standing.HELD if (teammate, berth) in held else Standing.AMBIGUOUS
        for berth, members in holders.items() for teammate in members
    }
    digests += [hashlib.sha256(b"mode" + r.canonical() + r.signature).digest() for r in used_modes]

    accepted = []
    for d in delegations:
        if (
            d.team_id == team_id
            and d.delegator_public_key in trusted.get(d.delegator_teammate_id, ())
            and (d.delegator_teammate_id, d.berth_id) in standing
            and d.signature_valid()
        ):
            accepted.append(d)
            digests.append(hashlib.sha256(b"delegation" + d.canonical() + d.signature).digest())

    h = hashlib.sha256(VIEW_VERSION + b"\0" + team_id + anchor_public_key)
    # The effective berth set changes the anchor's standing, so it is an input.
    for berth in sorted(berths):
        h.update(b"berth" + hashlib.sha256(berth).digest())
    for digest in sorted(set(digests)):
        h.update(digest)
    return TransitionalView(
        team_id=team_id,
        identifier=VIEW_VERSION + b":" + h.digest(),
        trusted_keys={t: frozenset(k) for t, k in trusted.items()},
        standing=standing,
        delegations=tuple(sorted(accepted, key=lambda d: d.record_id)),
    )


class AuthorityResult(Enum):
    AUTHORIZED = "authorized"
    BAD_SIGNATURE = "bad_signature"
    WRONG_SCOPE = "wrong_scope"
    MISSING_AUTHORITY = "missing_authority"
    AMBIGUOUS_AUTHORITY = "ambiguous_authority"


@dataclass(frozen=True)
class AuthorityDecision:
    result: AuthorityResult
    view_identifier: bytes
    reason: str
    delegation_ids: tuple = ()


def evaluate(view: TransitionalView, context: WorkContext | None, evidence: SignatureEvidence,
             *, team_id: bytes, berth_id: bytes, purpose: str) -> AuthorityDecision:
    """Judge one commit's signature evidence and signed context under `view`.

    The caller supplies the expected scope. Build the SshCommitVerifier that
    produced `evidence` from `view.workhorse_public_keys()`.
    `context.authority_view` names the signer's view; this function reports the
    verifier's own view and does not compare the two.
    """
    def decide(result, reason, ids=()):
        return AuthorityDecision(result, view.identifier, reason, tuple(ids))

    if evidence.kind in (SignatureEvidenceKind.UNSIGNED, SignatureEvidenceKind.INVALID):
        return decide(AuthorityResult.BAD_SIGNATURE, evidence.kind.value)
    if evidence.kind is not SignatureEvidenceKind.KNOWN_KEY:
        return decide(AuthorityResult.MISSING_AUTHORITY, "signing key has no delegation in this view")
    if context is None:
        return decide(AuthorityResult.WRONG_SCOPE, "commit has no work context")
    if (context.origin, context.team_id, context.berth_id, context.purpose) != (
        "commit", team_id.hex(), berth_id.hex(), purpose
    ):
        return decide(AuthorityResult.WRONG_SCOPE, "signed context names a different scope")

    keyed = [d for d in view.delegations if ssh_fingerprint(d.workhorse_public_key) == evidence.fingerprint]
    here = [d for d in keyed if d.team_id == team_id and d.berth_id == berth_id]
    if not here:
        if keyed:
            return decide(AuthorityResult.WRONG_SCOPE, "key is delegated only for another berth",
                          [d.record_id for d in keyed])
        return decide(AuthorityResult.MISSING_AUTHORITY, "signing key has no delegation in this view")
    ids = [d.record_id for d in here]
    if len({d.delegator_teammate_id for d in here}) > 1:
        return decide(AuthorityResult.AMBIGUOUS_AUTHORITY, "several teammates delegated this key", ids)
    covering = [d for d in here if purpose in d.purposes]
    if not covering:
        return decide(AuthorityResult.WRONG_SCOPE, "no delegation covers this purpose", ids)
    if all(view.holds(d.delegator_teammate_id, berth_id) is not Standing.HELD for d in covering):
        return decide(AuthorityResult.AMBIGUOUS_AUTHORITY, "delegator's berth authority is ambiguous", ids)
    return decide(AuthorityResult.AUTHORIZED, "delegated by a berth holder",
                  [d.record_id for d in covering])
