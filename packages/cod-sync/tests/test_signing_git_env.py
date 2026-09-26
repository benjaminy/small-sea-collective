"""Micro tests for Git signing subprocess configuration."""

import os
import subprocess

from cod_sync.git import gitCmd, signing_git_env
from cod_sync.repo import Repo


def test_signing_git_env_preserves_existing_config():
    base = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.name",
                GIT_CONFIG_VALUE_0="Existing")
    env = signing_git_env(base, program="/tmp/shim", public_key="ssh-ed25519 AAAA comment",
                          extra_env={"SMALL_SEA_SESSION_TOKEN": "token"})
    assert env["GIT_CONFIG_COUNT"] == "5"
    assert [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(5)] == [
        ("user.name", "Existing"), ("gpg.format", "ssh"),
        ("gpg.ssh.program", "/tmp/shim"),
        ("user.signingkey", "key::ssh-ed25519 AAAA comment"),
        ("commit.gpgsign", "true"),
    ]
    assert env["SMALL_SEA_SESSION_TOKEN"] == "token"
    assert "GIT_CONFIG_COUNT" not in base or base["GIT_CONFIG_COUNT"] == "1"


def test_repo_env_reaches_git(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    parent_env = dict(os.environ)
    env = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.name",
               GIT_CONFIG_VALUE_0="From Child")
    repo = Repo(tmp_path / ".git", tmp_path, env=env)
    assert repo._run(["config", "user.name"]).stdout.strip() == "From Child"
    assert repo.with_work_tree(tmp_path)._run(["config", "user.name"]).stdout.strip() == "From Child"
    assert repo._run_wt(["config", "user.name"]).stdout.strip() == "From Child"
    assert gitCmd(["config", "user.name"], env=env).stdout.strip() == "From Child"
    assert dict(os.environ) == parent_env


def test_repo_merge_message(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="Alice", GIT_AUTHOR_EMAIL="alice@test",
               GIT_COMMITTER_NAME="Alice", GIT_COMMITTER_EMAIL="alice@test")
    repo = Repo(tmp_path / ".git", tmp_path, env=env)
    (tmp_path / "base").write_text("base")
    repo._run_wt(["add", "base"])
    repo._run_wt(["commit", "-m", "base"])
    repo._run_wt(["checkout", "-b", "side"])
    (tmp_path / "side").write_text("side")
    repo._run_wt(["add", "side"])
    repo._run_wt(["commit", "-m", "side"])
    repo._run_wt(["checkout", "main"])
    (tmp_path / "main").write_text("main")
    repo._run_wt(["add", "main"])
    repo._run_wt(["commit", "-m", "main"])
    repo.merge("side", message="Chosen merge message")
    assert repo._run(["log", "-1", "--format=%s"]).stdout.strip() == "Chosen merge message"
