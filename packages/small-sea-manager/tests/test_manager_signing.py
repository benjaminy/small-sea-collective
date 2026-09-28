"""Micro tests: the Manager signs its own Git histories from the first commit."""

import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import small_sea_manager.provisioning as provisioning
from cod_sync.repo import Repo
from cod_sync.sshsig import public_key_from_private
from cod_sync.verify import SshCommitVerifier
from small_sea_manager import note_to_self_sync
from small_sea_manager.git_signing import signed_repo
from small_sea_note_to_self.db import attached_note_to_self_connection


def _fingerprint(private_bytes):
    return next(iter(SshCommitVerifier([public_key_from_private(private_bytes)]).fingerprints))


def _ancestry_signers(repo, head, *private_keys):
    """Verify every commit reachable from head against the given keys."""
    keys = [public_key_from_private(k) for k in private_keys]
    return SshCommitVerifier(keys).verify_history(repo, head)


def test_manager_bootstrap_and_merge_signed(playground_dir, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    root = pathlib.Path(playground_dir)
    participant = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, participant, "ProjectX")
    provisioning.register_app_for_participant(root, participant, "SmallSeaCollectiveFiles")
    provisioning.activate_app_for_team(root, participant, "ProjectX", "SmallSeaCollectiveFiles")

    # Core: the team device key signs the genesis commit and everything after.
    core_dir = root / "Participants" / participant / "ProjectX" / "Sync"
    core = Repo(core_dir / ".git", core_dir)
    device_key, _public = provisioning.get_current_team_device_key(root, participant, "ProjectX")
    core_commits = core._run(["rev-list", "HEAD"]).stdout.split()
    assert len(core_commits) >= 2
    verified = _ancestry_signers(core, core.head(), device_key)
    assert set(verified) == set(core_commits)
    assert set(verified.values()) == {_fingerprint(device_key)}

    # NoteToSelf: this device's signing key signs the welcome commit, a later
    # commit, and the merge; a second device's key signs the merged-in history.
    nts_dir = root / "Participants" / participant / "NoteToSelf" / "Sync"
    nts = Repo(nts_dir / ".git", nts_dir)

    with attached_note_to_self_connection(root, participant) as conn:
        nts_key = provisioning._read_local_secret(
            pathlib.Path(provisioning._current_device_row(conn)[4]))
    base = nts.head()
    provisioning.create_team(root, participant, "ProjectY")
    local_head = note_to_self_sync.commit_core_db(root, participant, nts, "local change")
    assert local_head

    other_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    other = signed_repo(nts, other_key, root / "other-device")
    tree = nts._run(["rev-parse", f"{base}^{{tree}}"]).stdout.strip()
    source = other.commit_tree(tree, [base], "other device")

    merge = note_to_self_sync._record_merge(root, participant, nts, local_head, source)
    parents = nts._run(["rev-list", "--parents", "-n", "1", merge]).stdout.split()
    assert parents[1:] == [local_head, source]

    nts_commits = nts._run(["rev-list", merge]).stdout.split()
    verified = _ancestry_signers(nts, merge, nts_key, other_key)
    assert set(verified) == set(nts_commits)
    for sha in nts_commits:
        expected = other_key if sha == source else nts_key
        assert verified[sha] == _fingerprint(expected)
