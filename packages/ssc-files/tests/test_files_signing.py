"""Micro tests for signed Files commits."""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading

from fastapi.testclient import TestClient
import pytest

from cod_sync.git import GitCmdFailed
from cod_sync.work_context import context_from_commit
from cod_sync.repo import Repo
from ssc_files import files
from small_sea_client.client import SmallSeaClient, SmallSeaSession
from small_sea_hub.backend import SmallSeaBackend
from small_sea_hub.server import app
import small_sea_manager.provisioning as provisioning
from test_support import _files_signing_key


PARTICIPANT = "aa" * 16
BERTH = "11" * 16
TEAM = "22" * 16
PEER = "bb" * 16


def _setup(tmp_path, test_signer):
    root = tmp_path / "files"
    checkout = tmp_path / "checkout"
    context = files.FilesMaterializationContext(PARTICIPANT, BERTH, TEAM, "Test")
    signer = test_signer(context)
    files.init_files(root, PARTICIPANT)
    files.create_niche(root, PARTICIPANT, context, "docs", signer=signer)
    files.add_checkout(root, PARTICIPANT, context, "docs", str(checkout))
    git_dir = files._niche_git_dir(root, context, "docs")
    return root, checkout, git_dir, context, signer


def _git(git_dir, checkout, *args, env=None):
    return subprocess.run(
        ["git", "--git-dir", str(git_dir), "--work-tree", str(checkout), *args],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _verify(tmp_path, git_dir, commit, signer, purpose):
    _, public_key = _files_signing_key()
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"files@test {public_key}\n")
    _git(git_dir, tmp_path, "-c", f"gpg.ssh.allowedSignersFile={allowed}",
         "verify-commit", commit)
    context = context_from_commit(Repo(git_dir), commit)
    assert (context.purpose, context.team_id, context.berth_id,
            context.authority_view) == (
        purpose, signer.team_id, signer.berth_id, signer.authority_view)


def _side_commit(git_dir, checkout, signer, base, branch, filename,
                 purpose="files-content"):
    _git(git_dir, checkout, "checkout", "-B", branch, base)
    (checkout / filename).write_text(branch)
    _git(git_dir, checkout, "add", filename)
    _git(git_dir, checkout, "commit", "-m", signer.message(branch, purpose),
         env=signer.env)
    side = _git(git_dir, checkout, "rev-parse", "HEAD")
    _git(git_dir, checkout, "checkout", "-B", "main", base)
    return side


def test_files_registry_content_merge_signed(tmp_path, test_signer):
    root, checkout, git_dir, context, signer = _setup(tmp_path, test_signer)
    registry = files._registry_git_dir(root, context)
    _verify(tmp_path, registry, "HEAD", signer, "files-registry")
    registry_checkout = files._registry_checkout_dir(root, context)
    registry_base = _git(registry, registry_checkout, "rev-parse", "HEAD")
    peer_registry = _side_commit(registry, registry_checkout, signer,
                                 registry_base, "peer-registry", "peer-note",
                                 purpose="files-registry")
    files.create_niche(root, PARTICIPANT, context, "other", signer=signer)
    _git(registry, registry_checkout, "update-ref",
         f"refs/peers/{PEER}/main", peer_registry)
    files.merge_registry(root, PARTICIPANT, context, PEER, signer=signer)
    _verify(tmp_path, registry, "HEAD", signer, "files-merge")

    (checkout / "base").write_text("base")
    base = files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    _verify(tmp_path, git_dir, base, signer, "files-content")

    peer = _side_commit(git_dir, checkout, signer, base, "peer", "peer-file")
    (checkout / "local-file").write_text("local")
    files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    _git(git_dir, checkout, "update-ref", f"refs/peers/{PEER}/main", peer)
    files.merge_niche(root, PARTICIPANT, context, "docs", PEER, signer=signer)
    _verify(tmp_path, git_dir, "HEAD", signer, "files-merge")

    base = _git(git_dir, checkout, "rev-parse", "HEAD")
    self_head = _side_commit(git_dir, checkout, signer, base, "self", "self-file")
    (checkout / "other-file").write_text("other")
    files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    _git(git_dir, checkout, "update-ref", "refs/cod-sync/parked/self-test", self_head)
    files.merge_self_niche(root, PARTICIPANT, context, "docs", signer=signer)
    _verify(tmp_path, git_dir, "HEAD", signer, "files-merge")


def test_files_publish_completes_merge_as_files_merge(tmp_path, test_signer):
    root, checkout, git_dir, context, signer = _setup(tmp_path, test_signer)
    (checkout / "same").write_text("base\n")
    base = files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    peer = _side_commit(git_dir, checkout, signer, base, "peer", "same")
    (checkout / "same").write_text("local\n")
    files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    _git(git_dir, checkout, "update-ref", f"refs/peers/{PEER}/main", peer)
    with pytest.raises(files.MergeConflictError):
        files.merge_niche(root, PARTICIPANT, context, "docs", PEER, signer=signer)
    (checkout / "same").write_text("resolved\n")
    files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    _verify(tmp_path, git_dir, "HEAD", signer, "files-merge")


def test_files_signing_failure_preserves_head(tmp_path, test_signer):
    root, checkout, git_dir, context, signer = _setup(tmp_path, test_signer)
    program_index = next(i for i in range(int(signer.env["GIT_CONFIG_COUNT"]))
                         if signer.env[f"GIT_CONFIG_KEY_{i}"] == "gpg.ssh.program")
    bad = replace(signer, env={**signer.env,
                               f"GIT_CONFIG_VALUE_{program_index}": "/missing/git-signer"})
    registry = files._registry_git_dir(root, context)
    before = _git(registry, checkout, "rev-parse", "HEAD")
    with pytest.raises(GitCmdFailed) as exc:
        files.create_niche(root, PARTICIPANT, context, "bad", signer=bad)
    assert "/missing/git-signer" in exc.value.err
    assert _git(registry, checkout, "rev-parse", "HEAD") == before

    (checkout / "base").write_text("base")
    base = files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    (checkout / "new").write_text("new")
    with pytest.raises(GitCmdFailed) as exc:
        files.publish(root, PARTICIPANT, context, "docs", checkout, signer=bad)
    assert "/missing/git-signer" in exc.value.err
    assert _git(git_dir, checkout, "rev-parse", "HEAD") == base
    _git(git_dir, checkout, "reset", "--hard", "HEAD")

    peer = _side_commit(git_dir, checkout, signer, base, "peer", "peer-file")
    (checkout / "local-file").write_text("local")
    local = files.publish(root, PARTICIPANT, context, "docs", checkout, signer=signer)
    _git(git_dir, checkout, "update-ref", f"refs/peers/{PEER}/main", peer)
    with pytest.raises(GitCmdFailed) as exc:
        files.merge_niche(root, PARTICIPANT, context, "docs", PEER, signer=bad)
    assert "/missing/git-signer" in exc.value.err
    assert _git(git_dir, checkout, "rev-parse", "HEAD") == local


def test_files_signer_scope_mismatch_rejected(tmp_path, test_signer):
    root, checkout, git_dir, context, signer = _setup(tmp_path, test_signer)
    for wrong in (replace(signer, team_id="ff" * 16),
                  replace(signer, berth_id="ff" * 16)):
        with pytest.raises(ValueError, match="signer team or berth"):
            files.publish(root, PARTICIPANT, context, "docs", checkout, signer=wrong)


def test_files_commit_signed_through_real_hub(tmp_path):
    backend = SmallSeaBackend(root_dir=tmp_path / "hub", auto_approve_sessions=True)
    participant = provisioning.create_new_participant(backend.root_dir, "Alice")
    provisioning.register_app_for_participant(backend.root_dir, participant,
                                              "SmallSeaCollectiveFiles")
    provisioning.create_team(backend.root_dir, participant, "ProjectX")
    provisioning.activate_app_for_team(backend.root_dir, participant, "ProjectX",
                                       "SmallSeaCollectiveFiles")
    token = backend.open_session("Alice", "SmallSeaCollectiveFiles", "ProjectX", "Smoke Tests").hex()
    berth = backend._lookup_session(token).berth_id
    provisioning.get_workhorse_signing_key(backend.root_dir, participant, berth)
    app.state.backend = backend
    client = TestClient(app)

    class Handler(BaseHTTPRequestHandler):
        def _handle(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {"Authorization": self.headers.get("Authorization", "")}
            response = (client.post(self.path, content=body, headers=headers)
                        if self.command == "POST" else client.get(self.path, headers=headers))
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        do_POST = _handle
        do_GET = _handle

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        session = SmallSeaSession(SmallSeaClient(port=server.server_port), token)
        signer = files.CommitSigner.from_session(
            session, f"http://127.0.0.1:{server.server_port}")
        signer = replace(signer, env={**signer.env,
                                      "GIT_AUTHOR_NAME": "Hub Test",
                                      "GIT_AUTHOR_EMAIL": "hub@test",
                                      "GIT_COMMITTER_NAME": "Hub Test",
                                      "GIT_COMMITTER_EMAIL": "hub@test"})
        context = files.FilesMaterializationContext.from_session_info(session.session_info())
        root = tmp_path / "files"
        files.init_files(root, participant)
        files.create_niche(root, participant, context, "docs", signer=signer)
        registry = files._registry_git_dir(root, context)
        signers = tmp_path / "hub-allowed-signers"
        signers.write_text(f"hub@test {session.signing_public_key()}\n")
        _git(registry, tmp_path, "-c", f"gpg.ssh.allowedSignersFile={signers}",
             "verify-commit", "HEAD")
        assert context_from_commit(Repo(registry), "HEAD").purpose == "files-registry"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
