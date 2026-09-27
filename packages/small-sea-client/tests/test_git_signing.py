"""Micro tests for Git signing through a Hub session."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
import pytest

from cod_sync.git import signing_git_env
from cod_sync.work_context import WorkContext, commit_message_with_context
from small_sea_hub.backend import SmallSeaBackend
from small_sea_hub.server import app
import small_sea_manager.provisioning as provisioning


@pytest.fixture
def signing_hub(tmp_path):
    backend = SmallSeaBackend(root_dir=tmp_path / "hub")
    participant = provisioning.create_new_participant(backend.root_dir, "alice")
    provisioning.create_team(backend.root_dir, participant, "ProjectX")
    app.state.backend = backend
    token = backend.open_session("alice", "SmallSeaCollectiveCore", "ProjectX", "Smoke Tests").hex()
    session = backend._lookup_session(token)
    provisioning.ensure_signing_is_set_up(backend.root_dir, participant, "ProjectX", session.berth_id)
    client = TestClient(app)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            response = client.post(self.path, content=body,
                                   headers={"Authorization": self.headers.get("Authorization", "")})
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield backend, session, token, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _git_setup(tmp_path, signing_hub, *, token=None, public_key=None):
    backend, session, real_token, url = signing_hub
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    key = public_key or provisioning.get_workhorse_signing_key(
        backend.root_dir, session.participant_id.hex(), session.berth_id
    )
    if isinstance(key, bytes):
        from cod_sync.sshsig import public_key_from_private
        key = public_key_from_private(key)
    shim = tmp_path / "small-sea-git-sign"
    shim.write_text(f"#!{sys.executable}\nfrom small_sea_client.git_signing import main\nraise SystemExit(main())\n")
    shim.chmod(0o755)
    env = signing_git_env(os.environ, program=str(shim), public_key=key,
                          extra_env={"SMALL_SEA_HUB_URL": url,
                                     "SMALL_SEA_SESSION_TOKEN": token or real_token,
                                     "GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@test",
                                     "GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "alice@test"})
    (repo / "file").write_text("data")
    subprocess.run(["git", "-C", str(repo), "add", "file"], env=env, check=True)
    context = WorkContext("commit", session.team_id.hex(), session.berth_id.hex(),
                          "core", b"view")
    message = commit_message_with_context("message", context)
    return repo, key, env, message


def test_git_sign_shim_signs_real_commit(tmp_path, signing_hub):
    repo, key, env, message = _git_setup(tmp_path, signing_hub)
    commit = subprocess.run(["git", "-C", str(repo), "commit", "-S", "-m", message],
                            env=env, capture_output=True, text=True)
    assert commit.returncode == 0, commit.stderr
    signers = tmp_path / "allowed_signers"
    signers.write_text(f"alice@test {key}\n")
    verify = subprocess.run(["git", "-C", str(repo), "-c",
                             f"gpg.ssh.allowedSignersFile={signers}", "verify-commit", "HEAD"],
                            env=env, capture_output=True, text=True)
    assert verify.returncode == 0, verify.stderr


def test_git_sign_shim_rejects_key_mismatch(tmp_path, signing_hub):
    repo, key, env, message = _git_setup(tmp_path, signing_hub, public_key="ssh-ed25519 AAAAwrong")
    commit = subprocess.run(["git", "-C", str(repo), "commit", "-S", "-m", message],
                            env=env, capture_output=True, text=True)
    assert commit.returncode != 0
    assert "does not match" in commit.stderr
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                          capture_output=True).returncode != 0


def test_git_sign_shim_hub_failure_leaves_no_commit(tmp_path, signing_hub):
    repo, key, env, message = _git_setup(tmp_path, signing_hub, token="bad-token")
    commit = subprocess.run(["git", "-C", str(repo), "commit", "-S", "-m", message],
                            env=env, capture_output=True, text=True)
    assert commit.returncode != 0
    assert "small-sea-git-sign" in commit.stderr
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                          capture_output=True).returncode != 0
