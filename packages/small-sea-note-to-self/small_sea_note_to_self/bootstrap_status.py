"""Keep incomplete identity bootstraps outside normal application access."""
import json
from pathlib import Path


class IdentityBootstrapBlockedError(ValueError):
    """The local installation has not completed identity verification."""


def identity_bootstrap_status_path(root_dir, participant_hex: str) -> Path:
    return (
        Path(root_dir) / 'Participants' / participant_hex / 'NoteToSelf'
        / 'Local' / 'identity_bootstrap_status.json'
    )


def assert_identity_bootstrap_trusted(root_dir, participant_hex: str) -> None:
    path = identity_bootstrap_status_path(root_dir, participant_hex)
    if path.exists():
        try:
            status = json.loads(path.read_text())
        except (OSError, ValueError):
            status = {}
        reason = status.get('reason', 'unknown reason') if isinstance(status, dict) else 'unknown reason'
        raise IdentityBootstrapBlockedError(
            'Installation is blocked because identity bootstrap did not verify cleanly: '
            f"{reason}"
        )
