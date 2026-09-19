"""Local Git/SSH micro tests for the original-ancestry verification contract."""
import pathlib
import subprocess

import pytest
from cod_sync.repo import Repo
from cod_sync.verify import SshCommitVerifier, UnsignedCommitError, VerificationUnavailableError


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    key = tmp_path / "key"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, capture_output=True)
    repo = Repo.init(tmp_path / "repo.git")
    repo.config("user.name", "Research")
    repo.config("user.email", "research@example.invalid")
    tree = subprocess.run(["git", "--git-dir", str(repo.git_dir), "mktree"], input="", text=True, capture_output=True, check=True).stdout.strip()
    unsigned = repo.commit_tree(tree, [], "unsigned root")
    repo.configure_signing(key)
    signed_root = repo.commit_tree(tree, [], "signed root")
    head = repo.commit_tree(tree, [unsigned], "signed child")
    verifier = SshCommitVerifier([key.with_suffix(".pub").read_text()])
    assert set(verifier.verify_history(repo, signed_root)) == {signed_root}
    with pytest.raises(UnsignedCommitError):
        verifier.verify_history(repo, head)
    return repo, verifier, unsigned, signed_root, head, tree


@pytest.mark.parametrize("override", ["replace", "graft", pytest.param("graft_quiet", marks=pytest.mark.xfail(strict=True, reason="quiet legacy graft hides original parent")), "shallow", "missing_parent"])
def test_ancestry_override(history, override):
    repo, verifier, unsigned, signed_root, head, tree = history
    if override == "replace":
        repo._run(["replace", unsigned, signed_root])
    elif override.startswith("graft"):
        (repo.git_dir / "info" / "grafts").write_text(head + "\n")
        if override == "graft_quiet":
            repo.config("advice.graftFileDeprecated", "false")
    elif override == "shallow":
        (repo.git_dir / "shallow").write_text(head + "\n")
    else:
        (repo.git_dir / "objects" / unsigned[:2] / unsigned[2:]).unlink()
    with pytest.raises((UnsignedCommitError, VerificationUnavailableError)):
        verifier.verify_history(repo, head)


def test_merge_side_and_signed_control(history):
    repo, verifier, unsigned, signed_root, head, tree = history
    merge = repo.commit_tree(tree, [signed_root, head], "merge unsigned side")
    with pytest.raises(UnsignedCommitError) as error:
        verifier.verify_history(repo, merge)
    assert error.value.commit == unsigned
    side = repo.commit_tree(tree, [signed_root], "signed side")
    main = repo.commit_tree(tree, [signed_root], "signed main")
    good_merge = repo.commit_tree(tree, [main, side], "signed merge")
    assert set(verifier.verify_history(repo, good_merge)) == {signed_root, main, side, good_merge}


@pytest.mark.parametrize("name,value", [("log.showSignature", "true"), ("log.decorate", "full"), ("log.showRoot", "false"), ("log.follow", "true"), ("format.pretty", "oneline"), ("gpg.minTrustLevel", "ultimate")])
def test_local_presentation_config(history, name, value):
    repo, verifier, unsigned, signed_root, head, tree = history
    repo.config(name, value)
    assert set(verifier.verify_history(repo, signed_root)) == {signed_root}
    with pytest.raises(UnsignedCommitError):
        verifier.verify_history(repo, head)


def test_command_controls_disable_grafts_and_signature_prose(history, tmp_path):
    """Probe a narrow backend fix without changing production code."""
    import os
    repo, verifier, unsigned, signed_root, head, tree = history
    (repo.git_dir / "info" / "grafts").write_text(head + "\n")
    repo.config("advice.graftFileDeprecated", "false")
    repo.config("log.showSignature", "true")
    signers = tmp_path / "signers"
    signers.write_text(f"research@example.invalid {verifier.allowed_keys[0]}\n")
    env = dict(os.environ, GIT_GRAFT_FILE="/dev/null")
    result = subprocess.run([
        "git", "--git-dir", str(repo.git_dir), "--no-replace-objects",
        "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={signers}",
        "log", "--no-show-signature", "--format=%H%x09%G?%x09%GF", head,
    ], env=env, text=True, capture_output=True, check=True)
    assert not result.stderr
    rows = [line.split("\t") for line in result.stdout.splitlines()]
    assert {row[0]: row[1] for row in rows} == {head: "G", unsigned: "N"}
