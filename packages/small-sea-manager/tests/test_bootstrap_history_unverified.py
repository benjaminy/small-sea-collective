"""Micro tests: bootstrap paths must refuse history that is not signed by accepted keys (#294).

Each test builds a valid enrollment whose fetched history has one bad ancestor
(signed by a key nobody accepted, or unsigned) beneath a correctly signed head.
Each asserts the refusal we want and that nothing is adopted.
All three are strict xfails today: the bootstrap code checks only the head.
"""

import pathlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import small_sea_manager.provisioning as provisioning
from cod_sync.protocol import CodSync
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore
from cod_sync.verify import VerificationError
from small_sea_manager.git_signing import core_signed_repo, nts_signed_repo, signed_repo
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager.provisioning import add_cloud_storage, create_new_participant
from small_sea_note_to_self.db import note_to_self_sync_db_path

from test_invitation import _localfolder_invitation
from test_linked_device_cold_start import (
    TEAM,
    _discovered_team_on_device_b,
    _team_sync_dir,
)

REFUSED = (ValueError, VerificationError)
XFAIL = pytest.mark.xfail(strict=True, reason="#294: bootstrap does not verify earlier history")


def _random_private_key():
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())


def _empty_commit(repo, message):
    repo._run(["commit", "--allow-empty", "-m", message])


def _bury_bad_ancestor(sync_dir, key_dir, *, sign_head, unsigned):
    """Add a bad commit, then a good commit on top, so the head looks fine.

    The bad commit is signed by a fresh unknown key, or unsigned if `unsigned`.
    `sign_head(repo)` makes the repo sign as the legitimate device.
    """
    bad = Repo(sync_dir / ".git", sync_dir)
    if unsigned:
        bad.config("commit.gpgsign", "false")
    else:
        signed_repo(bad, _random_private_key(), key_dir)
    _empty_commit(bad, "bad ancestor")
    good = sign_head(Repo(sync_dir / ".git", sync_dir))
    _empty_commit(good, "good head")
    assert "gpgsig" in good._run(["cat-file", "commit", "HEAD"]).stdout
    assert ("gpgsig" in good._run(["cat-file", "commit", "HEAD~1"]).stdout) != unsigned
    return good.head()


@XFAIL
def test_accept_invitation_refuses_core_ancestor_signed_by_unknown_key(
    playground_dir, monkeypatch
):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    root = pathlib.Path(playground_dir)
    alice, bob, token, store = _localfolder_invitation(root)
    alice_sync = root / "Participants" / alice / "ProjectX" / "Sync"
    _bury_bad_ancestor(
        alice_sync, root / "rogue-keys", unsigned=False,
        sign_head=lambda repo: core_signed_repo(root, alice, "ProjectX", repo),
    )
    CodSync(Repo(alice_sync / ".git", alice_sync), store).publish()

    with pytest.raises(REFUSED):
        provisioning.accept_invitation(root, bob, token, store)

    bob_sync = root / "Participants" / bob / "ProjectX" / "Sync"
    assert not bob_sync.exists()
    assert provisioning.list_teams(root, bob) == []


@XFAIL
def test_linked_device_bootstrap_refuses_unsigned_core_ancestor(
    playground_dir, minio_server_gen, monkeypatch
):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    root_a, root_b, alice_hex, manager_a, manager_b = _discovered_team_on_device_b(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    _bury_bad_ancestor(
        _team_sync_dir(root_a, alice_hex), root_a / "rogue-keys", unsigned=True,
        sign_head=lambda repo: core_signed_repo(root_a, alice_hex, TEAM, repo),
    )
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])

    with pytest.raises(REFUSED):
        manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])

    assert not provisioning.has_local_team_clone(root_b, alice_hex, TEAM)
    assert not _team_sync_dir(root_b, alice_hex).exists()


@XFAIL
def test_identity_bootstrap_refuses_note_to_self_ancestor_signed_by_unknown_key(
    playground_dir, monkeypatch
):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    workspace = pathlib.Path(playground_dir)
    root1, root2 = workspace / "install-a", workspace / "install-b"
    cloud_dir = workspace / "cloud"
    for d in (root1, root2, cloud_dir):
        d.mkdir()
    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))
    join_request = create_identity_join_request(root2)
    welcome = TeamManager(root1, alice_hex).authorize_identity_join(
        join_request["join_request_artifact"]
    )

    sync_dir = root1 / "Participants" / alice_hex / "NoteToSelf" / "Sync"
    _bury_bad_ancestor(
        sync_dir, root1 / "rogue-keys", unsigned=False,
        sign_head=lambda repo: nts_signed_repo(root1, alice_hex, repo),
    )
    CodSync(Repo(sync_dir / ".git", sync_dir), LocalFolderStore(str(cloud_dir))).publish()

    with pytest.raises(REFUSED):
        bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    assert not note_to_self_sync_db_path(root2, alice_hex).exists()
