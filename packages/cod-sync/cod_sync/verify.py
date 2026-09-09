"""Commit signature verification for a history Cod Sync is about to accept.

Key authorization belongs to the caller. This module checks original commit
objects against an explicit SSH key set.
"""

import base64
import hashlib
import pathlib
import tempfile
from typing import Dict, Iterable

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_ssh_public_key,
)

from cod_sync.repo import Repo, RepoError

#: The only `%G?` status this package accepts.
ACCEPTED_STATUS = "G"


class VerificationError(Exception):
    """A history was rejected; subclasses distinguish evidence from setup failure."""

    def __init__(self, message, *, commit=None, status=None, fingerprint=None):
        details = [
            f"commit={commit}" if commit else None,
            f"status={status!r}" if status else None,
            f"fingerprint={fingerprint}" if fingerprint else None,
        ]
        suffix = ", ".join(d for d in details if d)
        super().__init__(f"{message} ({suffix})" if suffix else message)
        self.commit = commit
        self.status = status
        self.fingerprint = fingerprint


class UnsignedCommitError(VerificationError):
    """The commit carries no signature."""


class SignatureInvalidError(VerificationError):
    """The commit's signature does not check out against the key that made it."""


class UnknownSignerError(VerificationError):
    """The signature is unrecognized: no supplied key accounts for it.

    An intentionally empty key set produces this too. "Nobody is recognized"
    and "this signer is not recognized" are the same answer.
    """


class VerificationUnavailableError(VerificationError):
    """Verification could not run reliably, so nothing was proved either way."""


class SshCommitVerifier:
    """Accept a history only when every commit is signed by one of these keys.

    allowed_keys holds SSH public key lines ("ssh-ed25519 AAAA... comment").
    Principals are synthesized, because git finds the principal from the
    signing key rather than from the commit's author text: a name in a commit
    is not authenticated identity, and treating one as a key's owner would
    invent evidence.
    """

    def __init__(self, allowed_keys: Iterable[str]):
        self.allowed_keys = []
        self.fingerprints = set()
        for key in allowed_keys:
            try:
                if "\n" in key.strip() or "\r" in key.strip():
                    raise ValueError("expected one SSH public key line")
                public_key = load_ssh_public_key(key.strip().encode())
                normalized = public_key.public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
            except (ValueError, UnsupportedAlgorithm) as exc:
                raise VerificationUnavailableError("invalid SSH public key") from exc
            self.allowed_keys.append(normalized.decode())
            digest = hashlib.sha256(base64.b64decode(normalized.split()[1])).digest()
            self.fingerprints.add("SHA256:" + base64.b64encode(digest).decode().rstrip("="))

    def verify_history(self, repo: Repo, head: str) -> Dict[str, str]:
        """Check head and every commit it reaches.

        Returns {commit: signing key fingerprint} as the evidence of which key
        signed what, and raises on the first commit that is not accepted.
        """
        try:
            with tempfile.TemporaryDirectory(prefix="cod-sync-verify-") as work_dir:
                signers_file = pathlib.Path(work_dir) / "allowed_signers"
                signers_file.write_text(
                    "".join(
                        f"signer-{index}@cod-sync.invalid {key}\n"
                        for index, key in enumerate(self.allowed_keys)
                    )
                )
                rows, diagnostics = repo.signature_report(head, signers_file)
        except (RepoError, OSError) as exc:
            raise VerificationUnavailableError(
                f"could not report signatures: {exc}", commit=head
            ) from exc

        if diagnostics.strip():
            # We wrote the configuration this ran under, so a diagnostic means
            # the verdicts below describe something other than the key set.
            raise VerificationUnavailableError(
                f"git reported a verification configuration problem: "
                f"{diagnostics.strip()}",
                commit=head,
            )
        if not rows:
            raise VerificationUnavailableError(
                "git reported no commits for the head to be accepted", commit=head
            )

        verified = {}
        for row in rows:
            if row.status != ACCEPTED_STATUS:
                raise _classify(row)
            if row.fingerprint not in self.fingerprints:
                raise UnknownSignerError(
                    "the signing fingerprint is not in the supplied SSH key set",
                    commit=row.commit,
                    status=row.status,
                    fingerprint=row.fingerprint,
                )
            verified[row.commit] = row.fingerprint
        return verified


def _classify(row) -> VerificationError:
    """Turn a rejected `%G?` status into the answer it actually supports."""
    evidence = dict(commit=row.commit, status=row.status, fingerprint=row.fingerprint)
    if row.status == "B":
        return SignatureInvalidError("the commit's signature is bad", **evidence)
    if row.status == "N":
        return UnsignedCommitError("the commit is not signed", **evidence)
    if row.status == "U":
        return UnknownSignerError(
            "no supplied key accounts for the commit's signature", **evidence
        )
    # E, X, Y, R, and anything a later git adds. Expiry and revocation are key
    # lifetime claims this package supplies no configuration for, so a status
    # reporting one is a verifier that is not running as intended.
    return VerificationUnavailableError(
        "git returned a signature status this verifier does not accept", **evidence
    )
