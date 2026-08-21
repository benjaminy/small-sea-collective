"""Micro tests for cod_sync.repo.Repo."""

import io
import pathlib
import subprocess

import pytest

from cod_sync.repo import (
    REF_ADVANCE_ATTEMPTS,
    BundleFormatError,
    ConflictError,
    NoWorkTreeError,
    RefAdvanceContendedError,
    RefDivergedError,
    Repo,
    RepoError,
)


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


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------


def _commit(repo, work, name, text):
    """Write and commit one file; return the new head SHA."""
    (pathlib.Path(work) / name).write_text(text)
    repo.stage([name])
    return repo.commit(f"add {name}")


@pytest.fixture
def chain(scratch_dir):
    """A repo with three commits on main, plus their SHAs.

    Returns (repo, work, [sha1, sha2, sha3]).
    """
    work = pathlib.Path(scratch_dir) / "chain"
    work.mkdir()
    repo = _make_normal_repo(work)
    shas = [
        _commit(repo, work, "a.txt", "alpha\n"),
        _commit(repo, work, "b.txt", "beta\n"),
        _commit(repo, work, "c.txt", "gamma\n"),
    ]
    return repo, work, shas


def test_create_bundle_full_has_no_prerequisites(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "full.bundle"
    repo.create_bundle(path, ["main"])

    assert repo.bundle_prerequisites(path) == set()
    assert repo.bundle_heads(path) == {"refs/heads/main": shas[-1]}


def test_create_bundle_incremental_exposes_its_prerequisite(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    repo.create_bundle(path, [f"^{shas[0]}", "main"])

    assert repo.bundle_prerequisites(path) == {shas[0]}
    assert repo.bundle_heads(path) == {"refs/heads/main": shas[-1]}


def test_create_bundle_from_head_ignores_the_current_main(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "fixed.bundle"

    repo.create_bundle_from_head(path, shas[1], predecessor_head=shas[0])

    assert repo.bundle_prerequisites(path) == {shas[0]}
    assert repo.bundle_heads(path) == {"refs/heads/main": shas[1]}
    assert repo.resolve_ref("refs/heads/main") == shas[2]


def test_create_bundle_rejects_empty_range(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "empty.bundle"
    with pytest.raises(RepoError):
        repo.create_bundle(path, [f"^{shas[-1]}", "main"])


def test_bundle_prerequisites_reads_v3_capability_line(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "v3.bundle"
    subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "bundle", "create",
         "--version=3", str(path), f"^{shas[0]}", "main"],
        check=True, capture_output=True,
    )
    with open(path, "rb") as handle:
        header = handle.read(4096)
    assert header.startswith(b"# v3 git bundle\n")
    assert b"\n@" in header

    assert repo.bundle_prerequisites(path) == {shas[0]}


@pytest.mark.parametrize(
    "capability",
    [b"@bad_key=value", b"@bad key=value", b"@valid=bad\x00value"],
)
def test_bundle_prerequisites_rejects_malformed_v3_capability(
    scratch_dir, chain, capability
):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "malformed-v3.bundle"
    path.write_bytes(
        b"# v3 git bundle\n"
        + capability
        + b"\n"
        + f"{shas[-1]} refs/heads/main\n\nPACK".encode()
    )

    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(path)


def test_bundle_prerequisites_stops_reading_at_header(
    scratch_dir, chain, monkeypatch
):
    repo, _work, shas = chain
    header = (
        f"# v2 git bundle\n-{shas[0]} base\n"
        f"{shas[-1]} refs/heads/main\n\n"
    ).encode()

    class TrackingBytesIO(io.BytesIO):
        def __exit__(self, *args):
            self.position_at_close = self.tell()
            return super().__exit__(*args)

    stream = TrackingBytesIO(header + b"PACK" + b"x" * 65536)

    def fake_open(_path, mode, **_kwargs):
        assert mode == "rb"
        return stream

    monkeypatch.setattr("builtins.open", fake_open)

    assert repo.bundle_prerequisites("tracked.bundle") == {shas[0]}
    assert stream.position_at_close == len(header)


def test_bundle_prerequisites_tolerates_spaces_in_commit_subject(scratch_dir):
    work = pathlib.Path(scratch_dir) / "spacey"
    work.mkdir()
    repo = _make_normal_repo(work)
    (work / "a.txt").write_text("alpha\n")
    repo.stage(["a.txt"])
    repo.commit("a subject with several spaces in it")
    base = repo.head()
    _commit(repo, work, "b.txt", "beta\n")

    path = pathlib.Path(scratch_dir) / "spacey.bundle"
    repo.create_bundle(path, [f"^{base}", "main"])
    assert repo.bundle_prerequisites(path) == {base}


def test_bundle_prerequisites_readable_when_prerequisite_is_absent(scratch_dir, chain):
    """The backward-walk case: verify fails, but the header still parses."""
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    repo.create_bundle(path, [f"^{shas[1]}", "main"])

    other = Repo.init(pathlib.Path(scratch_dir) / "other.git")
    assert other.has_commit(shas[1]) is False
    assert other.bundle_prerequisites(path) == {shas[1]}
    with pytest.raises(RepoError):
        other.verify_bundle(path)


def test_bundle_prerequisites_rejects_unrecognized_signature(scratch_dir, chain):
    repo, _work, _shas = chain
    path = pathlib.Path(scratch_dir) / "bogus.bundle"
    path.write_bytes(b"# v9 git bundle\ndeadbeef refs/heads/main\n\nPACK")
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(path)


def test_bundle_prerequisites_rejects_an_empty_header(scratch_dir, chain):
    repo, _work, _shas = chain
    path = pathlib.Path(scratch_dir) / "blank.bundle"
    path.write_bytes(b"\n# v2 git bundle\n\nPACK")
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(path)


def test_bundle_prerequisites_rejects_truncated_header(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "trunc.bundle"
    path.write_bytes(f"# v2 git bundle\n{shas[-1]} refs/heads/main\n".encode())
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(path)


def test_bundle_prerequisites_rejects_out_of_order_lines(scratch_dir, chain):
    repo, _work, shas = chain
    good = pathlib.Path(scratch_dir) / "good.bundle"
    repo.create_bundle(good, [f"^{shas[0]}", "main"])

    # Prerequisite after an advertised ref.
    reordered = pathlib.Path(scratch_dir) / "reordered.bundle"
    reordered.write_bytes(
        f"# v2 git bundle\n{shas[-1]} refs/heads/main\n-{shas[0]} a\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(reordered)

    # Capability line in a v2 bundle.
    capability_in_v2 = pathlib.Path(scratch_dir) / "cap2.bundle"
    capability_in_v2.write_bytes(
        f"# v2 git bundle\n@object-format=sha1\n{shas[-1]} refs/heads/main\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(capability_in_v2)

    # Capability line after the bundle's contents.
    late_capability = pathlib.Path(scratch_dir) / "cap3.bundle"
    late_capability.write_bytes(
        f"# v3 git bundle\n{shas[-1]} refs/heads/main\n@object-format=sha1\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(late_capability)


def test_bundle_prerequisites_rejects_malformed_lines(scratch_dir, chain):
    repo, _work, shas = chain

    no_refs = pathlib.Path(scratch_dir) / "norefs.bundle"
    no_refs.write_bytes(f"# v2 git bundle\n-{shas[0]} a\n\nPACK".encode())
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(no_refs)

    bad_prereq = pathlib.Path(scratch_dir) / "badprereq.bundle"
    bad_prereq.write_bytes(
        f"# v2 git bundle\n-notasha subject\n{shas[-1]} refs/heads/main\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(bad_prereq)

    missing_prereq_comment = (
        pathlib.Path(scratch_dir) / "missing-prereq-comment.bundle"
    )
    missing_prereq_comment.write_bytes(
        f"# v2 git bundle\n-{shas[0]}\n{shas[-1]} refs/heads/main\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(missing_prereq_comment)

    bad_ref = pathlib.Path(scratch_dir) / "badref.bundle"
    bad_ref.write_bytes(b"# v2 git bundle\nrefs/heads/main\n\nPACK")
    with pytest.raises(BundleFormatError):
        repo.bundle_prerequisites(bad_ref)


def test_verify_bundle_fails_when_prerequisite_missing(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    repo.create_bundle(path, [f"^{shas[0]}", "main"])

    repo.verify_bundle(path)  # prerequisite present here

    other = Repo.init(pathlib.Path(scratch_dir) / "bare.git")
    with pytest.raises(RepoError):
        other.verify_bundle(path)


def test_import_bundle_creates_no_ref(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "full.bundle"
    repo.create_bundle(path, ["main"])

    other = Repo.init(pathlib.Path(scratch_dir) / "other.git")
    refs_before = _for_each_ref(other)

    advertised = other.import_bundle(path)

    assert advertised == {"refs/heads/main": shas[-1]}
    assert other.has_commit(shas[-1]) is True
    assert _for_each_ref(other) == refs_before
    assert not (other.git_dir / "FETCH_HEAD").exists()


def test_import_bundle_fails_on_missing_prerequisite(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    repo.create_bundle(path, [f"^{shas[0]}", "main"])

    other = Repo.init(pathlib.Path(scratch_dir) / "other.git")
    with pytest.raises(RepoError):
        other.import_bundle(path)


def _for_each_ref(repo):
    result = subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "for-each-ref",
         "--format=%(refname) %(objectname)"],
        capture_output=True, text=True, check=True,
    )
    return sorted(result.stdout.splitlines())


# ---------------------------------------------------------------------------
# has_commit / merge_base
# ---------------------------------------------------------------------------


def test_has_commit(scratch_dir, chain):
    repo, _work, shas = chain
    assert repo.has_commit(shas[0]) is True
    assert repo.has_commit("0" * 40) is False


def test_merge_base(scratch_dir):
    work = pathlib.Path(scratch_dir) / "diverge"
    work.mkdir()
    repo = _make_normal_repo(work)
    base = _commit(repo, work, "a.txt", "alpha\n")
    left = _commit(repo, work, "b.txt", "beta\n")

    repo.checkout_branch("side", start_point=base)
    right = _commit(repo, work, "c.txt", "gamma\n")

    assert repo.merge_base(left, right) == base
    assert repo.merge_base(base, left) == base


def test_merge_base_returns_none_for_unrelated_histories(scratch_dir, chain):
    repo, work, shas = chain
    subprocess.run(
        ["git", "-C", str(work), "checkout", "--orphan", "unrelated"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(work), "rm", "-rf", "."], check=True, capture_output=True)
    orphan = _commit(repo, work, "z.txt", "zeta\n")

    assert repo.merge_base(shas[-1], orphan) is None


# ---------------------------------------------------------------------------
# advance_ref
# ---------------------------------------------------------------------------

PIN = "refs/pins/cloud"


def test_advance_ref_creates_absent_ref(scratch_dir, chain):
    repo, _work, shas = chain
    result = repo.advance_ref(PIN, shas[0])

    assert result.disposition == "created"
    assert result.previous_sha is None
    assert result.current_sha == shas[0]
    assert repo.resolve_ref(PIN) == shas[0]


def test_advance_ref_advances_forward(scratch_dir, chain):
    repo, _work, shas = chain
    repo.advance_ref(PIN, shas[0])

    result = repo.advance_ref(PIN, shas[2])

    assert result.disposition == "advanced"
    assert result.previous_sha == shas[0]
    assert result.current_sha == shas[2]
    assert repo.resolve_ref(PIN) == shas[2]


def test_advance_ref_equal_is_unchanged(scratch_dir, chain):
    repo, _work, shas = chain
    repo.advance_ref(PIN, shas[1])

    result = repo.advance_ref(PIN, shas[1])

    assert result.disposition == "unchanged"
    assert result.previous_sha == shas[1]
    assert repo.resolve_ref(PIN) == shas[1]


def test_advance_ref_retains_newer_pin_on_stale_update(scratch_dir, chain):
    repo, _work, shas = chain
    repo.advance_ref(PIN, shas[2])

    result = repo.advance_ref(PIN, shas[0])

    assert result.disposition == "stale"
    assert result.previous_sha == shas[2]
    assert result.current_sha == shas[2]
    assert repo.resolve_ref(PIN) == shas[2]


def test_advance_ref_rejects_divergence(scratch_dir):
    work = pathlib.Path(scratch_dir) / "diverge"
    work.mkdir()
    repo = _make_normal_repo(work)
    base = _commit(repo, work, "a.txt", "alpha\n")
    left = _commit(repo, work, "b.txt", "beta\n")
    repo.checkout_branch("side", start_point=base)
    right = _commit(repo, work, "c.txt", "gamma\n")

    repo.advance_ref(PIN, left)
    with pytest.raises(RefDivergedError) as excinfo:
        repo.advance_ref(PIN, right)

    assert excinfo.value.current_sha == left
    assert excinfo.value.new_sha == right
    assert repo.resolve_ref(PIN) == left


def test_advance_ref_rejects_the_null_object_id(scratch_dir, chain):
    """Git reads the null id as a delete, which would report a write that never happened."""
    repo, _work, shas = chain
    null_sha = "0" * 40

    with pytest.raises(RepoError):
        repo.advance_ref("refs/pins/absent", null_sha)
    assert repo.resolve_ref("refs/pins/absent") is None

    repo.advance_ref(PIN, shas[0])
    with pytest.raises(RepoError) as excinfo:
        repo.advance_ref(PIN, null_sha)
    assert not isinstance(excinfo.value, RefDivergedError)
    assert repo.resolve_ref(PIN) == shas[0]


def test_advance_ref_loses_out_of_order_race_then_rereads(scratch_dir, chain, monkeypatch):
    """A concurrent writer wins between the read and the compare-and-swap."""
    repo, _work, shas = chain
    repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    raced = []

    def racing_run(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref" and not raced:
            raced.append(True)
            # Another writer moves the pin past the value we are about to set.
            subprocess.run(
                ["git", "--git-dir", str(repo.git_dir), "update-ref", PIN, shas[2]],
                check=True, capture_output=True,
            )
        return real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    result = repo.advance_ref(PIN, shas[1])

    assert raced == [True]
    # The reread sees the newer value and keeps it.
    assert result.disposition == "stale"
    assert repo.resolve_ref(PIN) == shas[2]


def test_advance_ref_retries_failed_update_while_ref_is_unchanged(
    scratch_dir, chain, monkeypatch
):
    """A live lock can make update-ref fail before its writer moves the ref."""
    repo, _work, shas = chain
    repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    attempts = []

    def locked_once(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref":
            attempts.append(extra_args)
            if len(attempts) == 1:
                return subprocess.CompletedProcess(
                    extra_args, 128, "", "cannot lock ref: lock is held"
                )
        return real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", locked_once)
    result = repo.advance_ref(PIN, shas[1])

    assert len(attempts) == 2
    assert result.disposition == "advanced"
    assert repo.resolve_ref(PIN) == shas[1]


def test_advance_ref_stops_after_bounded_contention(scratch_dir, monkeypatch):
    """A competing writer wins every attempt, so contention is the answer."""
    work = pathlib.Path(scratch_dir) / "busy"
    work.mkdir()
    repo = _make_normal_repo(work)
    shas = [
        _commit(repo, work, f"f{i}.txt", f"line {i}\n")
        for i in range(REF_ADVANCE_ATTEMPTS + 2)
    ]
    repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    contender = iter(shas[1:])
    attempts = []

    def racing_run(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref":
            attempts.append(extra_args)
            subprocess.run(
                ["git", "--git-dir", str(repo.git_dir), "update-ref", PIN, next(contender)],
                check=True, capture_output=True,
            )
        return real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    with pytest.raises(RefAdvanceContendedError) as excinfo:
        repo.advance_ref(PIN, shas[-1])

    assert excinfo.value.attempts == REF_ADVANCE_ATTEMPTS
    assert len(attempts) == REF_ADVANCE_ATTEMPTS
    # Every attempt lost to the competing writer; none of them wrote.
    assert repo.resolve_ref(PIN) == shas[REF_ADVANCE_ATTEMPTS]


def test_advance_ref_classifies_the_ref_after_final_race(scratch_dir, monkeypatch):
    """The final losing CAS still rereads a value with a definite disposition."""
    work = pathlib.Path(scratch_dir) / "final-race"
    work.mkdir()
    repo = _make_normal_repo(work)
    shas = [
        _commit(repo, work, f"f{i}.txt", f"line {i}\n")
        for i in range(REF_ADVANCE_ATTEMPTS + 1)
    ]
    repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    contender = iter(shas[1:])

    def racing_run(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref":
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(repo.git_dir),
                    "update-ref",
                    PIN,
                    next(contender),
                ],
                check=True,
                capture_output=True,
            )
        return real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    result = repo.advance_ref(PIN, shas[-1])

    assert result.disposition == "unchanged"
    assert repo.resolve_ref(PIN) == shas[-1]


def test_advance_ref_reports_a_hard_failure_as_repo_error(scratch_dir, chain):
    repo, _work, shas = chain
    with pytest.raises(RepoError) as excinfo:
        repo.advance_ref("not a valid ref name", shas[0])

    assert not isinstance(excinfo.value, RefAdvanceContendedError)


# ---------------------------------------------------------------------------
# Error wrapping for the new mutating operations
# ---------------------------------------------------------------------------


def test_new_git_failures_are_wrapped_as_repo_error(scratch_dir, chain):
    repo, _work, _shas = chain
    missing = pathlib.Path(scratch_dir) / "nope.bundle"

    with pytest.raises(RepoError):
        repo.create_bundle(missing, ["no-such-ref"])
    with pytest.raises(RepoError):
        repo.verify_bundle(missing)
    with pytest.raises(RepoError):
        repo.bundle_heads(missing)
    with pytest.raises(RepoError):
        repo.import_bundle(missing)
