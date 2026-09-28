"""Micro tests for Core's runtime history verifier."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cod_sync.repo import Repo
from cod_sync.verify import UnknownSignerError, UnsignedCommitError
from small_sea_manager import provisioning as p
from small_sea_manager.git_signing import core_signed_repo, signed_repo
from small_sea_manager.git_verification import CoreHistoryVerifier, core_history_verifier


def _key():
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption()),
        private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
    )


def _repo(tmp_path, key):
    repo = Repo.init(tmp_path / "repo" / ".git").with_work_tree(tmp_path / "repo")
    repo.config("user.name", "test")
    repo.config("user.email", "test@example.invalid")
    return signed_repo(repo, key, tmp_path / "keys")


def _commit(repo, content):
    (repo.work_tree / "data").write_text(content)
    repo.stage(["data"])
    return repo.commit(content)


def test_core_accepts_anchor_root_and_recognized_device(tmp_path):
    anchor_private, anchor = _key()
    device_private, device = _key()
    repo = _repo(tmp_path, anchor_private)
    root = _commit(repo, "root")
    signed_repo(repo, device_private, tmp_path / "keys")
    head = _commit(repo, "device")
    assert set(CoreHistoryVerifier(anchor, {anchor, device}).verify_history(repo, head)) == {root, head}


def test_core_rejects_recognized_non_anchor_root(tmp_path):
    _, anchor = _key()
    device_private, device = _key()
    repo = _repo(tmp_path, device_private)
    head = _commit(repo, "wrong root")
    with pytest.raises(UnknownSignerError, match="authority-anchor"):
        CoreHistoryVerifier(anchor, {anchor, device}).verify_history(repo, head)


@pytest.mark.parametrize("unsigned", [True, False])
def test_core_rejects_unsigned_or_unknown_descendant(tmp_path, unsigned):
    anchor_private, anchor = _key()
    repo = _repo(tmp_path, anchor_private)
    _commit(repo, "root")
    if unsigned:
        repo.env = None
        repo.config("commit.gpgsign", "false")
    else:
        private, _ = _key()
        signed_repo(repo, private, tmp_path / "keys")
    head = _commit(repo, "unaccepted")
    error = UnsignedCommitError if unsigned else UnknownSignerError
    with pytest.raises(error) as caught:
        CoreHistoryVerifier(anchor, {anchor}).verify_history(repo, head)
    assert caught.value.commit == head


def test_core_verifier_uses_local_adopted_certificates(tmp_path):
    participant = p.create_new_participant(tmp_path, "Alice")
    p.create_team(tmp_path, participant, "Project")
    directory = p._team_sync_dir(tmp_path, participant, "Project")
    repo = core_signed_repo(tmp_path, participant, "Project", Repo(directory / ".git", directory))
    verifier = core_history_verifier(tmp_path, participant, "Project")
    assert repo.head() in verifier.verify_history(repo, repo.head())
    private, _ = _key()
    signed_repo(repo, private, tmp_path / "unknown")
    head = _commit(repo, "uncertified device")
    with pytest.raises(UnknownSignerError):
        verifier.verify_history(repo, head)


def test_manager_publish_refuses_unsigned_local_history(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cod_sync.store import LocalFolderStore
    from small_sea_manager import manager as m

    participant = p.create_new_participant(tmp_path, "Alice")
    p.create_team(tmp_path, participant, "Project")
    directory = p._team_sync_dir(tmp_path, participant, "Project")
    repo = Repo(directory / ".git", directory)
    repo.config("commit.gpgsign", "false")
    rejected = _commit(repo, "unsigned")
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    store = LocalFolderStore(str(cloud))
    monkeypatch.setattr(m, "SmallSeaStore", lambda *args, **kwargs: store)
    manager = m.TeamManager(tmp_path, participant)
    manager._get_or_open_session = lambda *args: SimpleNamespace(
        token="test", ensure_cloud_ready=lambda: None,
    )
    with pytest.raises(UnsignedCommitError) as caught:
        manager.push_team("Project")
    assert caught.value.commit == rejected
    assert not list(cloud.iterdir())
