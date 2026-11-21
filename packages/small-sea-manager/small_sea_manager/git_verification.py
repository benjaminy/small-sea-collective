"""Verify Core history using this device's adopted authority anchor."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cod_sync.aio.verify import SshCommitVerifier as AsyncSshCommitVerifier
from cod_sync.verify import SshCommitVerifier, UnknownSignerError


def _ssh_key(raw):
    return Ed25519PublicKey.from_public_bytes(raw).public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ).decode("ascii")


class CoreHistoryVerifier:
    """Require recognized device signatures and an anchor-signed root."""

    def __init__(self, anchor, trusted_keys):
        self.signatures = SshCommitVerifier(_ssh_key(key) for key in trusted_keys)
        self.anchor_fingerprint = _fingerprint(anchor)

    def verify_history(self, repo, head):
        verified = self.signatures.verify_history(repo, head)
        for commit, fingerprint in verified.items():
            commit_object = repo._run_binary(_ORIGINAL_COMMIT + [commit]).stdout
            _check_root(self.anchor_fingerprint, commit, fingerprint, commit_object)
        return verified


class AsyncCoreHistoryVerifier:
    """CoreHistoryVerifier for the async Cod Sync, which passes an async Repo."""

    def __init__(self, anchor, trusted_keys):
        self.signatures = AsyncSshCommitVerifier(_ssh_key(key) for key in trusted_keys)
        self.anchor_fingerprint = _fingerprint(anchor)

    async def verify_history(self, repo, head):
        verified = await self.signatures.verify_history(repo, head)
        for commit, fingerprint in verified.items():
            commit_object = (await repo._run_binary(_ORIGINAL_COMMIT + [commit])).stdout
            _check_root(self.anchor_fingerprint, commit, fingerprint, commit_object)
        return verified


def _fingerprint(raw_key):
    return next(iter(SshCommitVerifier([_ssh_key(raw_key)]).fingerprints))


def _check_root(anchor_fingerprint, commit, fingerprint, commit_object):
    """Refuse a root commit that the authority anchor did not sign."""
    headers = commit_object.split(b"\n\n", 1)[0].splitlines()
    if not any(line.startswith(b"parent ") for line in headers):
        if fingerprint != anchor_fingerprint:
            raise UnknownSignerError(
                "Core history must start with an authority-anchor signature",
                commit=commit, fingerprint=fingerprint,
            )


# Reads a commit's original parent headers, without replacement objects or grafts.
_ORIGINAL_COMMIT = ["--no-replace-objects", "cat-file", "commit"]


def core_history_verifier(root_dir, participant_hex, team_name):
    """Use accepted local certificates, including historical member keys."""
    return CoreHistoryVerifier(*_core_history_keys(root_dir, participant_hex, team_name))


def async_core_history_verifier(root_dir, participant_hex, team_name):
    """core_history_verifier for the async Cod Sync."""
    return AsyncCoreHistoryVerifier(*_core_history_keys(root_dir, participant_hex, team_name))


def _core_history_keys(root_dir, participant_hex, team_name):
    """Return (anchor, trusted device keys) from this device's accepted certificates."""
    from small_sea_manager import berth_authority, provisioning as p

    team_id, _ = p._team_row(root_dir, participant_hex, team_name)
    anchor = p._adopted_anchor_or_pause(root_dir, participant_hex, team_id)
    engine = p._sqlite_engine(p._team_sync_dir(root_dir, participant_hex, team_name) / "core.db")
    try:
        with engine.begin() as conn:
            # This view supplies signature evidence only. Removals affect current
            # authority, but do not erase earlier device signatures.
            view = berth_authority.build_view(
                team_id=team_id, anchor_public_key=anchor,
                certs=p._load_team_certificates(conn, team_id),
                mode_changes=[], delegations=[], berth_ids=[],
            )
    finally:
        engine.dispose()
    return anchor, {key for keys in view.trusted_keys.values() for key in keys}
