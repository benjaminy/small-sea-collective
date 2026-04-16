"""Micro tests for cod_sync.repo.Repo."""

import pathlib
import subprocess

import pytest

from cod_sync.repo import ConflictError, NoWorkTreeError, Repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_config(repo_dir):
    """Set a minimal git identity so commits work in test environments."""
    for cmd in [
        ["git", "-C", str(repo_dir), "config", "user.email", "test@test"],
        ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
    ]:
        subprocess.run(cmd, check=True)


def _make_normal_repo(path):
    """Create a normal git repo (git init) at path and configure identity.

    Returns Repo(path/.git, path).
    """
    path = pathlib.Path(path)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True)
    _git_config(path)
    return Repo(path / ".git", path)


def _make_bare_repo(path):
    """Create a bare-style repo via Repo.init() at path.

    Returns Repo(path, work_tree) after setting a work_tree directory.
    """
    path = pathlib.Path(path)
    repo = Repo.init(path)
    # Configure identity directly on the git dir
    subprocess.run(
        ["git", "--git-dir", str(path), "config", "user.email", "test@test"],
        check=True,
    )
    subprocess.run(
        ["git", "--git-dir", str(path), "config", "user.name", "Test"],
        check=True,
    )
    return repo


# ---------------------------------------------------------------------------
# Repo.init
# ---------------------------------------------------------------------------


def test_init_creates_repo(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "repo.git"
    repo = Repo.init(git_dir)
    assert repo.git_dir == git_dir
    assert repo.work_tree is None
    assert git_dir.is_dir()
    # core.bare should be false
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), "config", "core.bare"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "false"


# ---------------------------------------------------------------------------
# has_commits / head — unborn repo
# ---------------------------------------------------------------------------


def test_has_commits_false_on_empty_repo(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "empty.git"
    repo = Repo.init(git_dir)
    assert repo.has_commits() is False
    assert repo.head() is None


# ---------------------------------------------------------------------------
# head — after commits
# ---------------------------------------------------------------------------


def test_head_returns_sha_after_commit(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)
    assert repo.head() is None

    (work / "file.txt").write_text("hello\n")
    repo.stage(["file.txt"])
    sha = repo.commit("initial commit")

    assert sha is not None
    assert len(sha) == 40
    assert repo.head() == sha
    assert repo.has_commits() is True


# ---------------------------------------------------------------------------
# stage / commit
# ---------------------------------------------------------------------------


def test_stage_and_commit(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "a.txt").write_text("alpha\n")
    repo.stage(["a.txt"])
    sha = repo.commit("add a.txt")

    assert sha is not None
    # Second commit with no changes → None
    result = repo.commit("no-op")
    assert result is None


def test_commit_returns_none_when_nothing_staged(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "b.txt").write_text("beta\n")
    repo.stage(["b.txt"])
    repo.commit("first")

    # Nothing new staged
    assert repo.commit("should be none") is None


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "new.txt").write_text("new\n")
    entries = repo.status()
    # Untracked file shows up
    paths = [e["path"] for e in entries]
    assert "new.txt" in paths


# ---------------------------------------------------------------------------
# checkout_head
# ---------------------------------------------------------------------------


def test_checkout_head(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("original\n")
    repo.stage(["f.txt"])
    repo.commit("initial")

    # Dirty the work tree
    (work / "f.txt").write_text("modified\n")
    repo.checkout_head()
    assert (work / "f.txt").read_text() == "original\n"


# ---------------------------------------------------------------------------
# merge / ConflictError
# ---------------------------------------------------------------------------


def test_merge_conflict_raises_with_paths(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    work = scratch / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    # Create initial commit on main
    (work / "shared.txt").write_text("base\n")
    repo.stage(["shared.txt"])
    repo.commit("base")

    # Create a branch that edits shared.txt
    subprocess.run(
        ["git", "--git-dir", str(work / ".git"), "--work-tree", str(work),
         "checkout", "-b", "other"],
        check=True,
    )
    (work / "shared.txt").write_text("branch version\n")
    repo.stage(["shared.txt"])
    repo.commit("branch edit")

    # Switch back to main and make a conflicting edit
    subprocess.run(
        ["git", "--git-dir", str(work / ".git"), "--work-tree", str(work),
         "checkout", "main"],
        check=True,
    )
    (work / "shared.txt").write_text("main version\n")
    repo.stage(["shared.txt"])
    repo.commit("main edit")

    with pytest.raises(ConflictError) as exc_info:
        repo.merge("other")

    assert "shared.txt" in exc_info.value.conflict_paths


# ---------------------------------------------------------------------------
# NoWorkTreeError
# ---------------------------------------------------------------------------


def test_no_work_tree_error_on_cached_repo(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "cached.git"
    repo = Repo.init(git_dir)

    with pytest.raises(NoWorkTreeError):
        repo.stage(["anything"])

    with pytest.raises(NoWorkTreeError):
        repo.commit("msg")

    with pytest.raises(NoWorkTreeError):
        repo.status()
