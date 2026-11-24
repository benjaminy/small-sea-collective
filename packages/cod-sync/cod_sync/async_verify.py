"""Async commit signature verification: the async twin of cod_sync.verify.

It has only what an async caller uses so far. Errors and the checks on
git's report are cod_sync.verify's.
"""

import tempfile
from typing import Dict, Iterable

from cod_sync.repo import RepoError
from cod_sync.verify import (
    VerificationUnavailableError,
    _accept,
    _checked_rows,
    _load_keys,
    _write_signers_file,
)


class SshCommitVerifier:
    """Accept a history only when every commit is signed by one of these keys.

    The async twin of cod_sync.verify.SshCommitVerifier; see it for the rules.
    """

    def __init__(self, allowed_keys: Iterable[str]):
        self.allowed_keys, self.fingerprints = _load_keys(allowed_keys)

    async def verify_history(self, repo, head: str) -> Dict[str, str]:
        """Check head and every commit it reaches, in an async Repo.

        Returns {commit: signing key fingerprint}, and raises on the first
        commit that is not accepted.
        """
        try:
            with tempfile.TemporaryDirectory(prefix="cod-sync-verify-") as work_dir:
                signers_file = _write_signers_file(work_dir, self.allowed_keys)
                rows, diagnostics = await repo.signature_report(head, signers_file)
        except (RepoError, OSError) as exc:
            raise VerificationUnavailableError(
                f"could not report signatures: {exc}", commit=head
            ) from exc
        return _accept(_checked_rows(rows, diagnostics, head), self.fingerprints)
