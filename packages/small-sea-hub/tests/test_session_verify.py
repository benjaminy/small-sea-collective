"""Session history verification micro tests with real git signatures."""

import pathlib
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from cod_sync.work_context import WorkContext, commit_message_with_context
from small_sea_hub.backend import SmallSeaBackend
from small_sea_hub.server import app
import small_sea_manager.provisioning as provisioning
from small_sea_manager.berth_authority import MissingAuthorityAnchor


@pytest.fixture()
def verify_env(playground_dir, tmp_path):
    backend = SmallSeaBackend(root_dir=playground_dir)
    participant = provisioning.create_new_participant(playground_dir, "alice")
    provisioning.create_team(playground_dir, participant, "ProjectX")
    provisioning.register_app_for_participant(playground_dir, participant, "SmallSeaCollectiveFiles")
    provisioning.activate_app_for_team(playground_dir, participant, "ProjectX", "SmallSeaCollectiveFiles")
    token = backend.open_session("alice", "SmallSeaCollectiveFiles", "ProjectX", "Smoke Tests").hex()
    session = backend._lookup_session(token)
    provisioning.delegate_workhorse_key(playground_dir, participant, "ProjectX", session.berth_id)
    view = provisioning.load_transitional_authority_view(playground_dir, participant, "ProjectX")
    key = provisioning.get_workhorse_signing_key(playground_dir, participant, session.berth_id)
    app.state.backend = backend
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Alice")
    _git(repo, "config", "user.email", "alice@test")
    _git(repo, "config", "gpg.format", "ssh")
    _set_key(repo, tmp_path / "known.key", key)
    return backend, TestClient(app), token, session, view, repo


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _set_key(repo, path, key):
    path.write_bytes(Ed25519PrivateKey.from_private_bytes(key).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()))
    path.chmod(0o600)
    _git(repo, "config", "user.signingkey", str(path))


def _commit(repo, session, view, name, *, berth_id=None, purpose="SmallSeaCollectiveFiles/content", sign=True):
    (repo / name).write_text(name)
    _git(repo, "add", name)
    context = WorkContext("commit", session.team_id.hex(), berth_id or session.berth_id.hex(),
                          purpose, view.identifier)
    _git(repo, "-c", f"commit.gpgsign={'true' if sign else 'false'}", "commit", "-q",
         "-m", commit_message_with_context(name, context))
    return _git(repo, "rev-parse", "HEAD")


def _verify(client, token, repo, head):
    return client.post("/session/verify", headers={"Authorization": f"Bearer {token}"},
                       json={"git_dir": str(repo / ".git"), "head": head})


def test_verify_authorized_history(verify_env):
    _backend, client, token, session, view, repo = verify_env
    first = _commit(repo, session, view, "first")
    _git(repo, "checkout", "-q", "-b", "side")
    side = _commit(repo, session, view, "side")
    _git(repo, "checkout", "-q", "master")
    main = _commit(repo, session, view, "main")
    context = WorkContext("commit", session.team_id.hex(), session.berth_id.hex(),
                          "SmallSeaCollectiveFiles/content", view.identifier)
    _git(repo, "-c", "commit.gpgsign=true", "merge", "-q", "--no-ff", "side",
         "-m", commit_message_with_context("merge", context))
    head = _git(repo, "rev-parse", "HEAD")
    result = _verify(client, token, repo, head)
    assert result.status_code == 200, result.text
    assert {row["commit"] for row in result.json()["commits"]} == {first, side, main, head}
    assert {row["result"] for row in result.json()["commits"]} == {"authorized"}
    authority = client.get("/session/authority_view", headers={"Authorization": f"Bearer {token}"})
    assert result.json()["view_identifier"] == authority.json()["authority_view"]
    assert all(row["delegation_ids"] for row in result.json()["commits"])


def test_verify_reports_unsigned_and_bad(verify_env, tmp_path):
    _backend, client, token, session, view, repo = verify_env
    unsigned = _commit(repo, session, view, "unsigned", sign=False)
    _set_key(repo, tmp_path / "other.key", Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
    head = _commit(repo, session, view, "other")
    response = _verify(client, token, repo, head)
    assert response.status_code == 200, response.text
    results = {row["commit"]: row["result"] for row in response.json()["commits"]}
    assert results == {unsigned: "bad_signature", head: "missing_authority"}


def test_verify_wrong_scope(verify_env):
    _backend, client, token, session, view, repo = verify_env
    other_berth = _commit(repo, session, view, "other-berth", berth_id="ab" * 16)
    head = _commit(repo, session, view, "other-app", purpose="AnotherApp/content")
    response = _verify(client, token, repo, head)
    assert response.status_code == 200, response.text
    assert {row["commit"]: row["result"] for row in response.json()["commits"]} == {
        other_berth: "wrong_scope", head: "wrong_scope"}


def test_verify_refuses_small_sea_paths(verify_env, tmp_path):
    backend, client, token, _session, _view, repo = verify_env
    inside = pathlib.Path(backend.root_dir) / "inside"
    inside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(inside, target_is_directory=True)
    for path in (inside, link):
        response = client.post("/session/verify", headers={"Authorization": f"Bearer {token}"},
                               json={"git_dir": str(path), "head": "deadbeef"})
        assert response.status_code == 400
        assert response.json()["detail"] == {"code": "path_not_allowed"}


def test_verify_without_anchor(verify_env, monkeypatch):
    _backend, client, token, session, view, repo = verify_env
    head = _commit(repo, session, view, "first")

    def absent(*args):
        raise MissingAuthorityAnchor("no adopted anchor")

    monkeypatch.setattr(provisioning, "load_transitional_authority_view", absent)
    response = _verify(client, token, repo, head)
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "authority_anchor_absent"}


def test_verify_invalid_repository(verify_env):
    _backend, client, token, _session, _view, repo = verify_env
    response = _verify(client, token, repo, "deadbeef")
    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "invalid_repository"}
