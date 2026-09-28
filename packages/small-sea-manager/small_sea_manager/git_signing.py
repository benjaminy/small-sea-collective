"""Local Git signing for the Manager's own histories.

The Manager signs with keys it already holds, without the Hub:
NoteToSelf commits with this device's NoteToSelf signing key,
and a team's Core commits with this device's current team device key.
"""

import hashlib
import os
import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cod_sync.git import signing_git_env
from cod_sync.repo import Repo
from cod_sync.sshsig import public_key_from_private


def signed_repo(repo: Repo, private_bytes: bytes, key_dir) -> Repo:
    """Make every commit repo creates signed by private_bytes, and return repo.

    Git's ssh-keygen needs the key as an OpenSSH file, so a 0600 copy lives in
    key_dir beside the raw key. Git config carries only its path.
    """
    public_key = public_key_from_private(private_bytes)
    key_dir = pathlib.Path(key_dir)
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / f"git-sign-{hashlib.sha256(public_key.encode()).hexdigest()[:16]}.key"
    if not key_file.exists():
        pem = Ed25519PrivateKey.from_private_bytes(private_bytes).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
    repo.env = signing_git_env(os.environ, program="ssh-keygen", public_key=public_key,
                               signing_key=str(key_file))
    return repo


def nts_signed_repo(root_dir, participant_hex, repo: Repo) -> Repo:
    """Sign as this device's NoteToSelf signing key."""
    from small_sea_manager import provisioning as p
    with p.attached_note_to_self_connection(root_dir, participant_hex) as conn:
        key_path = pathlib.Path(p._current_device_row(conn)[4])
    return signed_repo(repo, p._read_local_secret(key_path),
                       p._fake_enclave_dir(root_dir, participant_hex))


def core_signed_repo(root_dir, participant_hex, team_name, repo: Repo) -> Repo:
    """Sign as this device's current team device key."""
    from small_sea_manager import provisioning as p
    private_key, _public = p.get_current_team_device_key(root_dir, participant_hex, team_name)
    return signed_repo(repo, private_key, p._fake_enclave_dir(root_dir, participant_hex))
