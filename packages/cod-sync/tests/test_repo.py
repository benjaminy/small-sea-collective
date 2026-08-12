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
# commit_paths / work_tree_paths_differ_from_head
# ---------------------------------------------------------------------------


def _index_entry(repo_dir, path):
    """Return the raw `git ls-files -s` line for path, or None if unstaged."""
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-files", "-s", "--", path],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() or None


def _commit_paths_fixture(scratch_dir):
    """A repo with a committed core.db and an unrelated tracked file."""
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)
    (work / "core.db").write_text("v1\n")
    (work / "other.txt").write_text("other v1\n")
    repo.stage(["core.db", "other.txt"])
    repo.commit("base")
    return work, repo


def test_commit_paths_commits_named_path_and_leaves_unrelated_staged(scratch_dir):
    work, repo = _commit_paths_fixture(scratch_dir)

    (work / "core.db").write_text("v2\n")
    (work / "other.txt").write_text("other v2\n")
    repo.stage(["other.txt"])
    staged_before = _index_entry(work, "other.txt")

    base = repo.head()
    sha = repo.commit_paths(["core.db"], "Update core")

    assert sha is not None and sha != base
    # The unrelated staged entry is untouched: still staged, byte-identical.
    assert _index_entry(work, "other.txt") == staged_before
    # ... and it is not part of the commit.
    changed = subprocess.run(
        ["git", "-C", str(work), "show", "--name-only", "--format=", sha],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert changed == ["core.db"]


def test_commit_paths_is_no_op_when_named_path_matches_head(scratch_dir):
    work, repo = _commit_paths_fixture(scratch_dir)

    # Only the unrelated path changed, and it is staged.
    (work / "other.txt").write_text("other v2\n")
    repo.stage(["other.txt"])
    staged_before = _index_entry(work, "other.txt")

    base = repo.head()
    assert repo.work_tree_paths_differ_from_head(["core.db"]) is False
    assert repo.commit_paths(["core.db"], "Update core") is None
    assert repo.head() == base
    assert _index_entry(work, "other.txt") == staged_before


def test_commit_paths_commits_unstaged_work_tree_change(scratch_dir):
    work, repo = _commit_paths_fixture(scratch_dir)

    # Modified but never staged — `diff --cached` would call this a no-op.
    (work / "core.db").write_text("v2\n")

    base = repo.head()
    assert repo.work_tree_paths_differ_from_head(["core.db"]) is True
    sha = repo.commit_paths(["core.db"], "Update core")

    assert sha is not None and sha != base
    committed = subprocess.run(
        ["git", "-C", str(work), "show", f"{sha}:core.db"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert committed == "v2\n"


def test_path_scoped_operations_reject_empty_paths(scratch_dir):
    work, repo = _commit_paths_fixture(scratch_dir)

    (work / "core.db").write_text("v2\n")
    (work / "other.txt").write_text("other v2\n")
    repo.stage(["other.txt"])
    staged_before = _index_entry(work, "other.txt")
    base = repo.head()

    with pytest.raises(ValueError, match="at least one path"):
        repo.work_tree_paths_differ_from_head([])
    with pytest.raises(ValueError, match="at least one path"):
        repo.commit_paths([], "Must not commit the index")

    assert repo.head() == base
    assert _index_entry(work, "other.txt") == staged_before
    assert repo.work_tree_paths_differ_from_head(["core.db"]) is True


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


# ---------------------------------------------------------------------------
# resolve_ref
# ---------------------------------------------------------------------------


def test_resolve_ref(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("v1\n")
    repo.stage(["f.txt"])
    sha = repo.commit("c1")

    assert repo.resolve_ref("main") == sha
    assert repo.resolve_ref("HEAD") == sha
    assert repo.resolve_ref("nonexistent") is None


# ---------------------------------------------------------------------------
# is_ancestor
# ---------------------------------------------------------------------------


def test_is_ancestor(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("v1\n")
    repo.stage(["f.txt"])
    sha1 = repo.commit("c1")

    (work / "f.txt").write_text("v2\n")
    repo.stage(["f.txt"])
    sha2 = repo.commit("c2")

    assert repo.is_ancestor(sha1, sha2) is True
    assert repo.is_ancestor(sha2, sha1) is False
    assert repo.is_ancestor(sha1, "HEAD") is True


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def test_log(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    repo.log()  # Should handle empty repo (return [])

    (work / "f.txt").write_text("v1\n")
    repo.stage(["f.txt"])
    repo.commit("first")

    (work / "f.txt").write_text("v2\n")
    repo.stage(["f.txt"])
    repo.commit("second")

    entries = repo.log(limit=5)
    assert len(entries) == 2
    assert entries[0]["message"] == "second"
    assert entries[1]["message"] == "first"
    assert len(entries[0]["sha"]) == 40


# ---------------------------------------------------------------------------
# checkout_branch
# ---------------------------------------------------------------------------


def test_checkout_branch(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("base\n")
    repo.stage(["f.txt"])
    repo.commit("base")

    repo.checkout_branch("feature")
    (work / "f.txt").write_text("feature\n")
    repo.stage(["f.txt"])
    repo.commit("feature commit")

    assert repo.resolve_ref("feature") == repo.head()
    assert (work / "f.txt").read_text() == "feature\n"

    # Switch back to main. To avoid resetting main to feature (HEAD),
    # we must specify main as the start point.
    repo.checkout_branch("main", start_point="main")
    assert (work / "f.txt").read_text() == "base\n"


# ---------------------------------------------------------------------------
# with_work_tree
# ---------------------------------------------------------------------------


def test_with_work_tree(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "repo.git"
    work_tree = pathlib.Path(scratch_dir) / "work"
    work_tree.mkdir()

    repo_cached = Repo.init(git_dir)
    assert repo_cached.work_tree is None

    repo_wt = repo_cached.with_work_tree(work_tree)
    assert repo_wt.git_dir == git_dir
    assert repo_wt.work_tree == work_tree

    # Verify repo_wt can perform work-tree ops
    (work_tree / "f.txt").write_text("hello\n")
    # Need identity for commit
    _git_config(git_dir)
    repo_wt.stage(["f.txt"])
    repo_wt.commit("msg")
    assert repo_wt.head() is not None
