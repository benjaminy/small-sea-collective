"""Micro tests for SSH commit signing and for what Git's verification reports.

These convert the throwaway signing spikes recorded in
`.IN_PROGRESS/issue-190-verify-signatures/notes.md` into repeatable evidence.
Two groups live here: what `Repo` produces when a signing key is configured,
and what `git log --format=%G?` does and does not tell a verifier.

Every test in this module isolates git config. Without that, a developer's own
`~/.gitconfig` signing setup silently signs the "unsigned" control, so the
negative controls pass for the wrong reason on a laptop and fail in CI.
"""

import pathlib
import subprocess

import pytest

from cod_sync.repo import RepoError

from cod_sync_test_helpers import (
    commit_file,
    isolated_git_config,  # noqa: F401  (pytest fixture)
    make_repo,
    make_ssh_key as _make_key,
    public_key_text,
)

#: Every test here needs the developer's own signing config out of the way.
pytestmark = pytest.mark.usefixtures("isolated_git_config")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allowed_signers(path, keys):
    """Write an allowed-signers file naming keys as {principal: key path}."""
    path = pathlib.Path(path)
    lines = [
        f"{principal} {public_key_text(key)}" for principal, key in keys.items()
    ]
    path.write_text("".join(line + "\n" for line in lines))
    return path


def _verify(repo, rev, allowed_signers_file=None):
    """Return (%G? status, %GF fingerprint, stderr) for rev.

    The verifier owns this configuration: `git log` exits 0 whatever it finds,
    so the status is the whole answer and stderr is the only place a
    configuration problem shows up.
    """
    args = ["git", "--git-dir", str(repo.git_dir)]
    if allowed_signers_file is not None:
        args += ["-c", f"gpg.ssh.allowedSignersFile={allowed_signers_file}"]
    args += ["log", "-1", "--format=%G?%x09%GF", rev]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    status, _, fingerprint = result.stdout.strip().partition("\t")
    return status, fingerprint, result.stderr


def _signed_repo(directory, key, name="alice"):
    """A work-tree repo configured to sign every commit with key."""
    repo = make_repo(directory, name=name)
    repo.configure_signing(key)
    return repo


def _parents(repo, rev):
    result = repo._run(["rev-list", "--parents", "-n", "1", rev])
    return result.stdout.split()[1:]


# ---------------------------------------------------------------------------
# What Repo produces
# ---------------------------------------------------------------------------


def test_unsigned_control_is_genuinely_unsigned(scratch_dir):
    """The negative control every other test leans on: no config, no signature."""
    root = pathlib.Path(scratch_dir)
    repo = make_repo(root / "repo")
    sha = commit_file(repo, "a.txt", "a")
    signers = _allowed_signers(
        root / "signers", {"alice@test": _make_key(root, "alice")}
    )

    status, fingerprint, _ = _verify(repo, sha, signers)
    assert status == "N"
    assert fingerprint == ""


def test_commit_is_signed_without_an_agent(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    sha = commit_file(repo, "a.txt", "a")
    signers = _allowed_signers(root / "signers", {"alice@test": key})

    status, fingerprint, _ = _verify(repo, sha, signers)
    assert status == "G"
    assert fingerprint.startswith("SHA256:")


def test_commit_paths_is_signed(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    commit_file(repo, "a.txt", "a")
    (repo.work_tree / "a.txt").write_text("a changed")
    sha = repo.commit_paths(["a.txt"], "change a")
    signers = _allowed_signers(root / "signers", {"alice@test": key})

    assert _verify(repo, sha, signers)[0] == "G"


def test_commit_tree_is_signed(scratch_dir):
    """`git commit-tree` is plumbing: it ignores commit.gpgsign entirely.

    Without an explicit -S this path stays unsigned, and the framework uses it
    (small_sea_manager/note_to_self_sync.py builds its integration commit here).
    """
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    parent = commit_file(repo, "a.txt", "a")
    tree = repo._run(["rev-parse", f"{parent}^{{tree}}"]).stdout.strip()
    sha = repo.commit_tree(tree, [parent], "rollup")
    signers = _allowed_signers(root / "signers", {"alice@test": key})

    assert _verify(repo, sha, signers)[0] == "G"


def test_merge_commit_is_signed(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    base = commit_file(repo, "base.txt", "base")
    side = commit_file(repo, "side.txt", "side")
    repo.checkout_branch("other", base)
    main_tip = commit_file(repo, "main.txt", "main")
    signers = _allowed_signers(root / "signers", {"alice@test": key})

    assert side != main_tip, "fixture tips must diverge"
    repo.merge(side)
    merge = repo.head()

    assert sorted(_parents(repo, merge)) == sorted([main_tip, side])
    assert _verify(repo, merge, signers)[0] == "G"


def test_signing_failure_creates_nothing_and_the_merge_waits(scratch_dir):
    """A configured signing failure never falls back to an unsigned commit.

    The merge stays staged so a retry can finish it once the key is back; git
    itself does not abort, and Cod Sync adds no automatic abort of its own.
    """
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    base = commit_file(repo, "base.txt", "base")
    side = commit_file(repo, "side.txt", "side")
    repo.checkout_branch("other", base)
    main_tip = commit_file(repo, "main.txt", "main")
    signers = _allowed_signers(root / "signers", {"alice@test": key})

    stashed = key.read_bytes()
    key.unlink()

    with pytest.raises(RepoError):
        repo.merge(side)
    assert repo.head() == main_tip
    assert (repo.git_dir / "MERGE_HEAD").exists()
    assert repo.conflict_paths() == []

    with pytest.raises(RepoError):
        repo.commit("retry the merge")
    assert repo.head() == main_tip
    assert (repo.git_dir / "MERGE_HEAD").exists()

    key.write_bytes(stashed)
    key.chmod(0o600)
    merge = repo.commit("retry the merge")

    assert merge is not None and merge != main_tip
    assert sorted(_parents(repo, merge)) == sorted([main_tip, side])
    assert _verify(repo, merge, signers)[0] == "G"


def test_bundle_round_trip_preserves_the_original_signature(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    commit_file(repo, "a.txt", "a")
    sha = commit_file(repo, "b.txt", "b")
    bundle = root / "main.bundle"
    repo.create_bundle_from_head(bundle, sha)

    destination = make_repo(root / "destination", name="bob")
    destination.import_bundle(bundle)
    signers = _allowed_signers(root / "signers", {"alice@test": key})

    assert _verify(destination, sha, signers)[0] == "G"


# ---------------------------------------------------------------------------
# What Git's verdicts do and do not classify
# ---------------------------------------------------------------------------


def test_missing_allowed_signers_config_looks_exactly_like_unsigned(scratch_dir):
    """The dangerous row: a correctly signed commit reports N, same as unsigned.

    `git log` still exits 0 and puts the diagnostic on stderr, so a verifier
    cannot recover "could not check" from the status. It must own the
    allowed-signers file and the effective verification configuration.
    """
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    sha = commit_file(repo, "a.txt", "a")

    status, fingerprint, stderr = _verify(repo, sha)

    assert status == "N"
    assert fingerprint == ""
    assert "allowedSignersFile" in stderr


@pytest.mark.parametrize("case", ["absent", "empty", "other_key"])
def test_unusable_or_nonmatching_signers_are_all_unverified(scratch_dir, case):
    """Setup failure and a deliberately empty key set share one status: U.

    An absent file, an empty file, and a file naming somebody else are three
    different situations, and U does not distinguish them.
    """
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    sha = commit_file(repo, "a.txt", "a")

    signers = root / "signers"
    if case == "empty":
        signers.write_text("")
    elif case == "other_key":
        _allowed_signers(signers, {"mallory@test": _make_key(root, "mallory")})

    status, _, stderr = _verify(repo, sha, signers)

    assert status == "U"
    assert stderr == ""


def test_tampered_commit_object_reports_a_bad_signature(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    sha = commit_file(repo, "a.txt", "a", message="original")
    signers = _allowed_signers(root / "signers", {"alice@test": key})

    original = repo._run_binary(["cat-file", "commit", sha]).stdout
    tampered = original.replace(b"original", b"tampered")
    assert tampered != original
    write = subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=tampered,
        capture_output=True,
        check=True,
    )
    tampered_sha = write.stdout.decode().strip()

    assert _verify(repo, sha, signers)[0] == "G"
    assert _verify(repo, tampered_sha, signers)[0] == "B"


def test_verified_parents_say_nothing_about_the_merge_tree(scratch_dir):
    """Direct support for the all-required-commits invariant.

    A publisher merges two signed histories, edits one author's file inside the
    merge, and signs the result. Both parents still verify against their own
    authors' keys; only the merge commit's own signature covers the tree that
    an accepted head resolves to.
    """
    root = pathlib.Path(scratch_dir)
    alice_key = _make_key(root, "alice")
    bob_key = _make_key(root, "bob")
    publisher_key = _make_key(root, "publisher")

    repo = _signed_repo(root / "repo", alice_key)
    base = commit_file(repo, "base.txt", "base")
    alice_tip = commit_file(repo, "alice.txt", "alice work")

    repo.checkout_branch("bob", base)
    repo.configure_signing(bob_key)
    bob_tip = commit_file(repo, "bob.txt", "bob work")

    repo.checkout_branch("main", alice_tip)
    repo.configure_signing(publisher_key)
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(repo.git_dir),
            "--work-tree",
            str(repo.work_tree),
            "merge",
            "--no-commit",
            "--no-ff",
            bob_tip,
        ],
        check=True,
        capture_output=True,
    )
    (repo.work_tree / "alice.txt").write_text("alice work\nSNEAKED IN BY PUBLISHER")
    repo.stage(["alice.txt"])
    merge = repo.commit("merge bob")

    assert sorted(_parents(repo, merge)) == sorted([alice_tip, bob_tip])
    authors = _allowed_signers(
        root / "authors", {"alice@test": alice_key, "bob@test": bob_key}
    )
    assert _verify(repo, alice_tip, authors)[0] == "G"
    assert _verify(repo, bob_tip, authors)[0] == "G"
    assert _verify(repo, merge, authors)[0] == "U"

    with_publisher = _allowed_signers(
        root / "with_publisher",
        {"alice@test": alice_key, "bob@test": bob_key, "publisher@test": publisher_key},
    )
    assert _verify(repo, merge, with_publisher)[0] == "G"
    assert (
        repo._run(["show", f"{merge}:alice.txt"]).stdout
        == "alice work\nSNEAKED IN BY PUBLISHER"
    )


def test_signed_squash_keeps_only_the_squasher_attestation(scratch_dir):
    """A squash loses the original authors' signatures, not all evidence.

    The parentless commit carries the squasher's attestation of the published
    tree, and nothing about who wrote the content it contains.
    """
    root = pathlib.Path(scratch_dir)
    alice_key = _make_key(root, "alice")
    publisher_key = _make_key(root, "publisher")

    repo = _signed_repo(root / "repo", alice_key)
    commit_file(repo, "a.txt", "a")
    alice_tip = commit_file(repo, "b.txt", "b")
    tree = repo._run(["rev-parse", f"{alice_tip}^{{tree}}"]).stdout.strip()

    repo.configure_signing(publisher_key)
    squash = repo.commit_tree(tree, [], "squash")

    destination = make_repo(root / "destination", name="carol")
    bundle = root / "squash.bundle"
    repo.create_bundle_from_head(bundle, squash)
    destination.import_bundle(bundle)

    signers = _allowed_signers(
        root / "signers", {"alice@test": alice_key, "publisher@test": publisher_key}
    )
    assert _parents(destination, squash) == []
    assert _verify(destination, squash, signers)[0] == "G"
    assert not destination.has_commit(alice_tip)


def test_commit_tree_rejects_invalid_signing_config(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = _make_key(root, "alice")
    repo = _signed_repo(root / "repo", key)
    parent = commit_file(repo, "a.txt", "a")
    tree = repo._run(["rev-parse", f"{parent}^{{tree}}"]).stdout.strip()
    repo.config("commit.gpgsign", "tru")
    objects_before = set((repo.git_dir / "objects").rglob("*"))
    with pytest.raises(RepoError, match="commit.gpgsign"):
        repo.commit_tree(tree, [parent], "must not be unsigned")
    assert set((repo.git_dir / "objects").rglob("*")) == objects_before
    assert repo.head() == parent
    repo.config("commit.gpgsign", "true")
    signed = repo.commit_tree(tree, [parent], "signed control")
    signers = _allowed_signers(root / "signers", {"alice@test": key})
    assert _verify(repo, signed, signers)[0] == "G"
