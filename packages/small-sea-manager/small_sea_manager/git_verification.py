"""Verify Core history using this device's adopted authority anchor."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cod_sync.verify import SshCommitVerifier, UnknownSignerError


def _ssh_key(raw):
    return Ed25519PublicKey.from_public_bytes(raw).public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ).decode("ascii")


class CoreHistoryVerifier:
    """Require recognized device signatures and an anchor-signed root."""

    def __init__(self, anchor, trusted_keys):
        self.signatures = SshCommitVerifier(_ssh_key(key) for key in trusted_keys)
        self.anchor_fingerprint = next(iter(SshCommitVerifier([_ssh_key(anchor)]).fingerprints))

    def verify_history(self, repo, head):
        verified = self.signatures.verify_history(repo, head)
        for commit, fingerprint in verified.items():
            # Read original parent headers, without replacement objects or grafts.
            headers = repo._run_binary(
                ["--no-replace-objects", "cat-file", "commit", commit]
            ).stdout.split(b"\n\n", 1)[0].splitlines()
            if not any(line.startswith(b"parent ") for line in headers):
                if fingerprint != self.anchor_fingerprint:
                    raise UnknownSignerError(
                        "Core history must start with an authority-anchor signature",
                        commit=commit, fingerprint=fingerprint,
                    )
        return verified


def core_history_verifier(root_dir, participant_hex, team_name):
    """Use accepted local certificates, including historical member keys."""
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
    return CoreHistoryVerifier(anchor, {key for keys in view.trusted_keys.values() for key in keys})
