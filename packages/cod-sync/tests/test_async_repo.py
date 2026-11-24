"""Micro tests for cod_sync.async_repo.Repo, ported from test_repo.py."""

import io
import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.anyio

from cod_sync.async_repo import (
    REF_ADVANCE_ATTEMPTS,
    BundleFormatError,
    ConflictError,
    NoWorkTreeError,
    RefAdvanceContendedError,
    RefDivergedError,
    RefImmutableConflictError,
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


async def _make_bare_repo(path):
    """Create a bare-style repo via Repo.init() at path.

    Returns Repo(path, work_tree) after setting a work_tree directory.
    """
    path = pathlib.Path(path)
    repo = await Repo.init(path)
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


async def test_init_creates_repo(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "repo.git"
    repo = await Repo.init(git_dir)
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


async def test_has_commits_false_on_empty_repo(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "empty.git"
    repo = await Repo.init(git_dir)
    assert await repo.has_commits() is False
    assert await repo.head() is None


# ---------------------------------------------------------------------------
# head — after commits
# ---------------------------------------------------------------------------


async def test_head_returns_sha_after_commit(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)
    assert await repo.head() is None

    (work / "file.txt").write_text("hello\n")
    await repo.stage(["file.txt"])
    sha = await repo.commit("initial commit")

    assert sha is not None
    assert len(sha) == 40
    assert await repo.head() == sha
    assert await repo.has_commits() is True


# ---------------------------------------------------------------------------
# stage / commit
# ---------------------------------------------------------------------------


async def test_stage_and_commit(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "a.txt").write_text("alpha\n")
    await repo.stage(["a.txt"])
    sha = await repo.commit("add a.txt")

    assert sha is not None
    # Second commit with no changes → None
    result = await repo.commit("no-op")
    assert result is None


async def test_commit_returns_none_when_nothing_staged(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "b.txt").write_text("beta\n")
    await repo.stage(["b.txt"])
    await repo.commit("first")

    # Nothing new staged
    assert await repo.commit("should be none") is None


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


async def _commit_paths_fixture(scratch_dir):
    """A repo with a committed core.db and an unrelated tracked file."""
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)
    (work / "core.db").write_text("v1\n")
    (work / "other.txt").write_text("other v1\n")
    await repo.stage(["core.db", "other.txt"])
    await repo.commit("base")
    return work, repo


async def test_commit_paths_commits_named_path_and_leaves_unrelated_staged(scratch_dir):
    work, repo = await _commit_paths_fixture(scratch_dir)

    (work / "core.db").write_text("v2\n")
    (work / "other.txt").write_text("other v2\n")
    await repo.stage(["other.txt"])
    staged_before = _index_entry(work, "other.txt")

    base = await repo.head()
    sha = await repo.commit_paths(["core.db"], "Update core")

    assert sha is not None and sha != base
    # The unrelated staged entry is untouched: still staged, byte-identical.
    assert _index_entry(work, "other.txt") == staged_before
    # ... and it is not part of the commit.
    changed = subprocess.run(
        ["git", "-C", str(work), "show", "--name-only", "--format=", sha],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert changed == ["core.db"]


async def test_commit_paths_is_no_op_when_named_path_matches_head(scratch_dir):
    work, repo = await _commit_paths_fixture(scratch_dir)

    # Only the unrelated path changed, and it is staged.
    (work / "other.txt").write_text("other v2\n")
    await repo.stage(["other.txt"])
    staged_before = _index_entry(work, "other.txt")

    base = await repo.head()
    assert await repo.work_tree_paths_differ_from_head(["core.db"]) is False
    assert await repo.commit_paths(["core.db"], "Update core") is None
    assert await repo.head() == base
    assert _index_entry(work, "other.txt") == staged_before


async def test_commit_paths_commits_unstaged_work_tree_change(scratch_dir):
    work, repo = await _commit_paths_fixture(scratch_dir)

    # Modified but never staged — `diff --cached` would call this a no-op.
    (work / "core.db").write_text("v2\n")

    base = await repo.head()
    assert await repo.work_tree_paths_differ_from_head(["core.db"]) is True
    sha = await repo.commit_paths(["core.db"], "Update core")

    assert sha is not None and sha != base
    committed = subprocess.run(
        ["git", "-C", str(work), "show", f"{sha}:core.db"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert committed == "v2\n"


async def test_path_scoped_operations_reject_empty_paths(scratch_dir):
    work, repo = await _commit_paths_fixture(scratch_dir)

    (work / "core.db").write_text("v2\n")
    (work / "other.txt").write_text("other v2\n")
    await repo.stage(["other.txt"])
    staged_before = _index_entry(work, "other.txt")
    base = await repo.head()

    with pytest.raises(ValueError, match="at least one path"):
        await repo.work_tree_paths_differ_from_head([])
    with pytest.raises(ValueError, match="at least one path"):
        await repo.commit_paths([], "Must not commit the index")

    assert await repo.head() == base
    assert _index_entry(work, "other.txt") == staged_before
    assert await repo.work_tree_paths_differ_from_head(["core.db"]) is True


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


async def test_status(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "new.txt").write_text("new\n")
    entries = await repo.status()
    # Untracked file shows up
    paths = [e["path"] for e in entries]
    assert "new.txt" in paths


# ---------------------------------------------------------------------------
# checkout_head
# ---------------------------------------------------------------------------


async def test_checkout_head(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("original\n")
    await repo.stage(["f.txt"])
    await repo.commit("initial")

    # Dirty the work tree
    (work / "f.txt").write_text("modified\n")
    await repo.checkout_head()
    assert (work / "f.txt").read_text() == "original\n"


# ---------------------------------------------------------------------------
# merge / ConflictError
# ---------------------------------------------------------------------------


async def test_merge_conflict_raises_with_paths(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    work = scratch / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    # Create initial commit on main
    (work / "shared.txt").write_text("base\n")
    await repo.stage(["shared.txt"])
    await repo.commit("base")

    # Create a branch that edits shared.txt
    subprocess.run(
        ["git", "--git-dir", str(work / ".git"), "--work-tree", str(work),
         "checkout", "-b", "other"],
        check=True,
    )
    (work / "shared.txt").write_text("branch version\n")
    await repo.stage(["shared.txt"])
    await repo.commit("branch edit")

    # Switch back to main and make a conflicting edit
    subprocess.run(
        ["git", "--git-dir", str(work / ".git"), "--work-tree", str(work),
         "checkout", "main"],
        check=True,
    )
    (work / "shared.txt").write_text("main version\n")
    await repo.stage(["shared.txt"])
    await repo.commit("main edit")

    with pytest.raises(ConflictError) as exc_info:
        await repo.merge("other")

    assert "shared.txt" in exc_info.value.conflict_paths


# ---------------------------------------------------------------------------
# NoWorkTreeError
# ---------------------------------------------------------------------------


async def test_no_work_tree_error_on_cached_repo(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "cached.git"
    repo = await Repo.init(git_dir)

    with pytest.raises(NoWorkTreeError):
        await repo.stage(["anything"])

    with pytest.raises(NoWorkTreeError):
        await repo.commit("msg")

    with pytest.raises(NoWorkTreeError):
        await repo.status()


# ---------------------------------------------------------------------------
# resolve_ref
# ---------------------------------------------------------------------------


async def test_resolve_ref(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("v1\n")
    await repo.stage(["f.txt"])
    sha = await repo.commit("c1")

    assert await repo.resolve_ref("main") == sha
    assert await repo.resolve_ref("HEAD") == sha
    assert await repo.resolve_ref("nonexistent") is None


# ---------------------------------------------------------------------------
# is_ancestor
# ---------------------------------------------------------------------------


async def test_is_ancestor(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("v1\n")
    await repo.stage(["f.txt"])
    sha1 = await repo.commit("c1")

    (work / "f.txt").write_text("v2\n")
    await repo.stage(["f.txt"])
    sha2 = await repo.commit("c2")

    assert await repo.is_ancestor(sha1, sha2) is True
    assert await repo.is_ancestor(sha2, sha1) is False
    assert await repo.is_ancestor(sha1, "HEAD") is True


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


async def test_log(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    await repo.log()  # Should handle empty repo (return [])

    (work / "f.txt").write_text("v1\n")
    await repo.stage(["f.txt"])
    await repo.commit("first")

    (work / "f.txt").write_text("v2\n")
    await repo.stage(["f.txt"])
    await repo.commit("second")

    entries = await repo.log(limit=5)
    assert len(entries) == 2
    assert entries[0]["message"] == "second"
    assert entries[1]["message"] == "first"
    assert len(entries[0]["sha"]) == 40


# ---------------------------------------------------------------------------
# checkout_branch
# ---------------------------------------------------------------------------


async def test_checkout_branch(scratch_dir):
    work = pathlib.Path(scratch_dir) / "work"
    work.mkdir()
    repo = _make_normal_repo(work)

    (work / "f.txt").write_text("base\n")
    await repo.stage(["f.txt"])
    await repo.commit("base")

    await repo.checkout_branch("feature")
    (work / "f.txt").write_text("feature\n")
    await repo.stage(["f.txt"])
    await repo.commit("feature commit")

    assert await repo.resolve_ref("feature") == await repo.head()
    assert (work / "f.txt").read_text() == "feature\n"

    # Switch back to main. To avoid resetting main to feature (HEAD),
    # we must specify main as the start point.
    await repo.checkout_branch("main", start_point="main")
    assert (work / "f.txt").read_text() == "base\n"


# ---------------------------------------------------------------------------
# with_work_tree
# ---------------------------------------------------------------------------


async def test_with_work_tree(scratch_dir):
    git_dir = pathlib.Path(scratch_dir) / "repo.git"
    work_tree = pathlib.Path(scratch_dir) / "work"
    work_tree.mkdir()

    repo_cached = await Repo.init(git_dir)
    assert repo_cached.work_tree is None

    repo_wt = repo_cached.with_work_tree(work_tree)
    assert repo_wt.git_dir == git_dir
    assert repo_wt.work_tree == work_tree

    # Verify repo_wt can perform work-tree ops
    (work_tree / "f.txt").write_text("hello\n")
    # Need identity for commit
    _git_config(git_dir)
    await repo_wt.stage(["f.txt"])
    await repo_wt.commit("msg")
    assert await repo_wt.head() is not None


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------


async def _commit(repo, work, name, text):
    """Write and commit one file; return the new head SHA."""
    (pathlib.Path(work) / name).write_text(text)
    await repo.stage([name])
    return await repo.commit(f"add {name}")


@pytest.fixture
async def chain(scratch_dir):
    """A repo with three commits on main, plus their SHAs.

    Returns (repo, work, [sha1, sha2, sha3]).
    """
    work = pathlib.Path(scratch_dir) / "chain"
    work.mkdir()
    repo = _make_normal_repo(work)
    shas = [
        await _commit(repo, work, "a.txt", "alpha\n"),
        await _commit(repo, work, "b.txt", "beta\n"),
        await _commit(repo, work, "c.txt", "gamma\n"),
    ]
    return repo, work, shas


async def test_create_bundle_full_has_no_prerequisites(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "full.bundle"
    await repo.create_bundle(path, ["main"])

    assert await repo.bundle_prerequisites(path) == set()
    assert await repo.bundle_heads(path) == {"refs/heads/main": shas[-1]}


async def test_create_bundle_incremental_exposes_its_prerequisite(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    await repo.create_bundle(path, [f"^{shas[0]}", "main"])

    assert await repo.bundle_prerequisites(path) == {shas[0]}
    assert await repo.bundle_heads(path) == {"refs/heads/main": shas[-1]}


async def test_create_bundle_from_head_ignores_the_current_main(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "fixed.bundle"

    await repo.create_bundle_from_head(path, shas[1], predecessor_head=shas[0])

    assert await repo.bundle_prerequisites(path) == {shas[0]}
    assert await repo.bundle_heads(path) == {"refs/heads/main": shas[1]}
    assert await repo.resolve_ref("refs/heads/main") == shas[2]


async def test_create_bundle_rejects_empty_range(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "empty.bundle"
    with pytest.raises(RepoError):
        await repo.create_bundle(path, [f"^{shas[-1]}", "main"])


async def test_bundle_prerequisites_reads_v3_capability_line(scratch_dir, chain):
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

    assert await repo.bundle_prerequisites(path) == {shas[0]}


@pytest.mark.parametrize(
    "capability",
    [b"@bad_key=value", b"@bad key=value", b"@valid=bad\x00value"],
)
async def test_bundle_prerequisites_rejects_malformed_v3_capability(
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
        await repo.bundle_prerequisites(path)


async def test_bundle_prerequisites_stops_reading_at_header(
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

    assert await repo.bundle_prerequisites("tracked.bundle") == {shas[0]}
    assert stream.position_at_close == len(header)


async def test_bundle_prerequisites_tolerates_spaces_in_commit_subject(scratch_dir):
    work = pathlib.Path(scratch_dir) / "spacey"
    work.mkdir()
    repo = _make_normal_repo(work)
    (work / "a.txt").write_text("alpha\n")
    await repo.stage(["a.txt"])
    await repo.commit("a subject with several spaces in it")
    base = await repo.head()
    await _commit(repo, work, "b.txt", "beta\n")

    path = pathlib.Path(scratch_dir) / "spacey.bundle"
    await repo.create_bundle(path, [f"^{base}", "main"])
    assert await repo.bundle_prerequisites(path) == {base}


async def test_bundle_prerequisites_readable_when_prerequisite_is_absent(scratch_dir, chain):
    """The backward-walk case: verify fails, but the header still parses."""
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    await repo.create_bundle(path, [f"^{shas[1]}", "main"])

    other = await Repo.init(pathlib.Path(scratch_dir) / "other.git")
    assert await other.has_commit(shas[1]) is False
    assert await other.bundle_prerequisites(path) == {shas[1]}
    with pytest.raises(RepoError):
        await other.verify_bundle(path)


async def test_bundle_prerequisites_rejects_unrecognized_signature(scratch_dir, chain):
    repo, _work, _shas = chain
    path = pathlib.Path(scratch_dir) / "bogus.bundle"
    path.write_bytes(b"# v9 git bundle\ndeadbeef refs/heads/main\n\nPACK")
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(path)


async def test_bundle_prerequisites_rejects_an_empty_header(scratch_dir, chain):
    repo, _work, _shas = chain
    path = pathlib.Path(scratch_dir) / "blank.bundle"
    path.write_bytes(b"\n# v2 git bundle\n\nPACK")
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(path)


async def test_bundle_prerequisites_rejects_truncated_header(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "trunc.bundle"
    path.write_bytes(f"# v2 git bundle\n{shas[-1]} refs/heads/main\n".encode())
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(path)


async def test_bundle_prerequisites_rejects_out_of_order_lines(scratch_dir, chain):
    repo, _work, shas = chain
    good = pathlib.Path(scratch_dir) / "good.bundle"
    await repo.create_bundle(good, [f"^{shas[0]}", "main"])

    # Prerequisite after an advertised ref.
    reordered = pathlib.Path(scratch_dir) / "reordered.bundle"
    reordered.write_bytes(
        f"# v2 git bundle\n{shas[-1]} refs/heads/main\n-{shas[0]} a\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(reordered)

    # Capability line in a v2 bundle.
    capability_in_v2 = pathlib.Path(scratch_dir) / "cap2.bundle"
    capability_in_v2.write_bytes(
        f"# v2 git bundle\n@object-format=sha1\n{shas[-1]} refs/heads/main\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(capability_in_v2)

    # Capability line after the bundle's contents.
    late_capability = pathlib.Path(scratch_dir) / "cap3.bundle"
    late_capability.write_bytes(
        f"# v3 git bundle\n{shas[-1]} refs/heads/main\n@object-format=sha1\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(late_capability)


async def test_bundle_prerequisites_rejects_malformed_lines(scratch_dir, chain):
    repo, _work, shas = chain

    no_refs = pathlib.Path(scratch_dir) / "norefs.bundle"
    no_refs.write_bytes(f"# v2 git bundle\n-{shas[0]} a\n\nPACK".encode())
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(no_refs)

    bad_prereq = pathlib.Path(scratch_dir) / "badprereq.bundle"
    bad_prereq.write_bytes(
        f"# v2 git bundle\n-notasha subject\n{shas[-1]} refs/heads/main\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(bad_prereq)

    missing_prereq_comment = (
        pathlib.Path(scratch_dir) / "missing-prereq-comment.bundle"
    )
    missing_prereq_comment.write_bytes(
        f"# v2 git bundle\n-{shas[0]}\n{shas[-1]} refs/heads/main\n\nPACK".encode()
    )
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(missing_prereq_comment)

    bad_ref = pathlib.Path(scratch_dir) / "badref.bundle"
    bad_ref.write_bytes(b"# v2 git bundle\nrefs/heads/main\n\nPACK")
    with pytest.raises(BundleFormatError):
        await repo.bundle_prerequisites(bad_ref)


async def test_verify_bundle_fails_when_prerequisite_missing(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    await repo.create_bundle(path, [f"^{shas[0]}", "main"])

    await repo.verify_bundle(path)  # prerequisite present here

    other = await Repo.init(pathlib.Path(scratch_dir) / "bare.git")
    with pytest.raises(RepoError):
        await other.verify_bundle(path)


async def test_import_bundle_creates_no_ref(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "full.bundle"
    await repo.create_bundle(path, ["main"])

    other = await Repo.init(pathlib.Path(scratch_dir) / "other.git")
    refs_before = _for_each_ref(other)

    advertised = await other.import_bundle(path)

    assert advertised == {"refs/heads/main": shas[-1]}
    assert await other.has_commit(shas[-1]) is True
    assert _for_each_ref(other) == refs_before
    assert not (other.git_dir / "FETCH_HEAD").exists()


async def test_import_bundle_fails_on_missing_prerequisite(scratch_dir, chain):
    repo, _work, shas = chain
    path = pathlib.Path(scratch_dir) / "incr.bundle"
    await repo.create_bundle(path, [f"^{shas[0]}", "main"])

    other = await Repo.init(pathlib.Path(scratch_dir) / "other.git")
    with pytest.raises(RepoError):
        await other.import_bundle(path)


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


async def test_has_commit(scratch_dir, chain):
    repo, _work, shas = chain
    assert await repo.has_commit(shas[0]) is True
    assert await repo.has_commit("0" * 40) is False


async def test_merge_base(scratch_dir):
    work = pathlib.Path(scratch_dir) / "diverge"
    work.mkdir()
    repo = _make_normal_repo(work)
    base = await _commit(repo, work, "a.txt", "alpha\n")
    left = await _commit(repo, work, "b.txt", "beta\n")

    await repo.checkout_branch("side", start_point=base)
    right = await _commit(repo, work, "c.txt", "gamma\n")

    assert await repo.merge_base(left, right) == base
    assert await repo.merge_base(base, left) == base


async def test_merge_base_returns_none_for_unrelated_histories(scratch_dir, chain):
    repo, work, shas = chain
    subprocess.run(
        ["git", "-C", str(work), "checkout", "--orphan", "unrelated"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(work), "rm", "-rf", "."], check=True, capture_output=True)
    orphan = await _commit(repo, work, "z.txt", "zeta\n")

    assert await repo.merge_base(shas[-1], orphan) is None


# ---------------------------------------------------------------------------
# advance_ref
# ---------------------------------------------------------------------------

PIN = "refs/pins/cloud"


async def test_advance_ref_creates_absent_ref(scratch_dir, chain):
    repo, _work, shas = chain
    result = await repo.advance_ref(PIN, shas[0])

    assert result.disposition == "created"
    assert result.previous_sha is None
    assert result.current_sha == shas[0]
    assert await repo.resolve_ref(PIN) == shas[0]


async def test_advance_ref_advances_forward(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.advance_ref(PIN, shas[0])

    result = await repo.advance_ref(PIN, shas[2])

    assert result.disposition == "advanced"
    assert result.previous_sha == shas[0]
    assert result.current_sha == shas[2]
    assert await repo.resolve_ref(PIN) == shas[2]


async def test_advance_ref_equal_is_unchanged(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.advance_ref(PIN, shas[1])

    result = await repo.advance_ref(PIN, shas[1])

    assert result.disposition == "unchanged"
    assert result.previous_sha == shas[1]
    assert await repo.resolve_ref(PIN) == shas[1]


async def test_advance_ref_retains_newer_pin_on_stale_update(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.advance_ref(PIN, shas[2])

    result = await repo.advance_ref(PIN, shas[0])

    assert result.disposition == "stale"
    assert result.previous_sha == shas[2]
    assert result.current_sha == shas[2]
    assert await repo.resolve_ref(PIN) == shas[2]


async def test_advance_ref_rejects_divergence(scratch_dir):
    work = pathlib.Path(scratch_dir) / "diverge"
    work.mkdir()
    repo = _make_normal_repo(work)
    base = await _commit(repo, work, "a.txt", "alpha\n")
    left = await _commit(repo, work, "b.txt", "beta\n")
    await repo.checkout_branch("side", start_point=base)
    right = await _commit(repo, work, "c.txt", "gamma\n")

    await repo.advance_ref(PIN, left)
    with pytest.raises(RefDivergedError) as excinfo:
        await repo.advance_ref(PIN, right)

    assert excinfo.value.current_sha == left
    assert excinfo.value.new_sha == right
    assert await repo.resolve_ref(PIN) == left


async def test_advance_ref_rejects_the_null_object_id(scratch_dir, chain):
    """Git reads the null id as a delete, which would report a write that never happened."""
    repo, _work, shas = chain
    null_sha = "0" * 40

    with pytest.raises(RepoError):
        await repo.advance_ref("refs/pins/absent", null_sha)
    assert await repo.resolve_ref("refs/pins/absent") is None

    await repo.advance_ref(PIN, shas[0])
    with pytest.raises(RepoError) as excinfo:
        await repo.advance_ref(PIN, null_sha)
    assert not isinstance(excinfo.value, RefDivergedError)
    assert await repo.resolve_ref(PIN) == shas[0]


async def test_advance_ref_loses_out_of_order_race_then_rereads(scratch_dir, chain, monkeypatch):
    """A concurrent writer wins between the read and the compare-and-swap."""
    repo, _work, shas = chain
    await repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    raced = []

    async def racing_run(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref" and not raced:
            raced.append(True)
            # Another writer moves the pin past the value we are about to set.
            subprocess.run(
                ["git", "--git-dir", str(repo.git_dir), "update-ref", PIN, shas[2]],
                check=True, capture_output=True,
            )
        return await real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    result = await repo.advance_ref(PIN, shas[1])

    assert raced == [True]
    # The reread sees the newer value and keeps it.
    assert result.disposition == "stale"
    assert await repo.resolve_ref(PIN) == shas[2]


async def test_advance_ref_retries_failed_update_while_ref_is_unchanged(
    scratch_dir, chain, monkeypatch
):
    """A live lock can make update-ref fail before its writer moves the ref."""
    repo, _work, shas = chain
    await repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    attempts = []

    async def locked_once(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref":
            attempts.append(extra_args)
            if len(attempts) == 1:
                return subprocess.CompletedProcess(
                    extra_args, 128, "", "cannot lock ref: lock is held"
                )
        return await real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", locked_once)
    result = await repo.advance_ref(PIN, shas[1])

    assert len(attempts) == 2
    assert result.disposition == "advanced"
    assert await repo.resolve_ref(PIN) == shas[1]


async def test_advance_ref_stops_after_bounded_contention(scratch_dir, monkeypatch):
    """A competing writer wins every attempt, so contention is the answer."""
    work = pathlib.Path(scratch_dir) / "busy"
    work.mkdir()
    repo = _make_normal_repo(work)
    shas = [
        await _commit(repo, work, f"f{i}.txt", f"line {i}\n")
        for i in range(REF_ADVANCE_ATTEMPTS + 2)
    ]
    await repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    contender = iter(shas[1:])
    attempts = []

    async def racing_run(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref":
            attempts.append(extra_args)
            subprocess.run(
                ["git", "--git-dir", str(repo.git_dir), "update-ref", PIN, next(contender)],
                check=True, capture_output=True,
            )
        return await real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    with pytest.raises(RefAdvanceContendedError) as excinfo:
        await repo.advance_ref(PIN, shas[-1])

    assert excinfo.value.attempts == REF_ADVANCE_ATTEMPTS
    assert len(attempts) == REF_ADVANCE_ATTEMPTS
    # Every attempt lost to the competing writer; none of them wrote.
    assert await repo.resolve_ref(PIN) == shas[REF_ADVANCE_ATTEMPTS]


async def test_advance_ref_classifies_the_ref_after_final_race(scratch_dir, monkeypatch):
    """The final losing CAS still rereads a value with a definite disposition."""
    work = pathlib.Path(scratch_dir) / "final-race"
    work.mkdir()
    repo = _make_normal_repo(work)
    shas = [
        await _commit(repo, work, f"f{i}.txt", f"line {i}\n")
        for i in range(REF_ADVANCE_ATTEMPTS + 1)
    ]
    await repo.advance_ref(PIN, shas[0])

    real_run = repo._run
    contender = iter(shas[1:])

    async def racing_run(extra_args, raise_on_error=True):
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
        return await real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    result = await repo.advance_ref(PIN, shas[-1])

    assert result.disposition == "unchanged"
    assert await repo.resolve_ref(PIN) == shas[-1]


async def test_advance_ref_reports_a_hard_failure_as_repo_error(scratch_dir, chain):
    repo, _work, shas = chain
    with pytest.raises(RepoError) as excinfo:
        await repo.advance_ref("not a valid ref name", shas[0])

    assert not isinstance(excinfo.value, RefAdvanceContendedError)


# ---------------------------------------------------------------------------
# Error wrapping for the new mutating operations
# ---------------------------------------------------------------------------


async def test_new_git_failures_are_wrapped_as_repo_error(scratch_dir, chain):
    repo, _work, _shas = chain
    missing = pathlib.Path(scratch_dir) / "nope.bundle"

    with pytest.raises(RepoError):
        await repo.create_bundle(missing, ["no-such-ref"])
    with pytest.raises(RepoError):
        await repo.verify_bundle(missing)
    with pytest.raises(RepoError):
        await repo.bundle_heads(missing)
    with pytest.raises(RepoError):
        await repo.import_bundle(missing)


# ---------------------------------------------------------------------------
# list_refs
# ---------------------------------------------------------------------------

OBSERVED = "refs/small-sea/core-peer/aa/observations/L1"


async def test_list_refs_returns_exact_pairs_under_one_prefix(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.advance_ref("refs/small-sea/core-peer/aa/latest", shas[0])
    await repo.advance_ref(OBSERVED, shas[1])
    await repo.advance_ref("refs/small-sea/other/aa", shas[2])

    assert await repo.list_refs("refs/small-sea/core-peer") == {
        "refs/small-sea/core-peer/aa/latest": shas[0],
        OBSERVED: shas[1],
    }


async def test_list_refs_is_empty_when_the_prefix_holds_nothing(scratch_dir, chain):
    repo, _work, _shas = chain
    assert await repo.list_refs("refs/small-sea/core-peer") == {}


async def test_list_refs_needs_no_work_tree(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.advance_ref(OBSERVED, shas[0])
    cached = Repo(repo.git_dir)

    assert await cached.list_refs("refs/small-sea/core-peer") == {OBSERVED: shas[0]}


# ---------------------------------------------------------------------------
# create_ref_immutable
# ---------------------------------------------------------------------------


async def test_create_ref_immutable_creates_an_absent_ref(scratch_dir, chain):
    repo, _work, shas = chain
    result = await repo.create_ref_immutable(OBSERVED, shas[1])

    assert result.disposition == "created"
    assert result.current_sha == shas[1]
    assert await repo.resolve_ref(OBSERVED) == shas[1]


async def test_create_ref_immutable_verifies_the_same_sha(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.create_ref_immutable(OBSERVED, shas[1])

    result = await repo.create_ref_immutable(OBSERVED, shas[1])

    assert result.disposition == "verified"
    assert await repo.resolve_ref(OBSERVED) == shas[1]


async def test_create_ref_immutable_refuses_to_retarget_even_forward(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.create_ref_immutable(OBSERVED, shas[0])

    with pytest.raises(RefImmutableConflictError) as excinfo:
        await repo.create_ref_immutable(OBSERVED, shas[2])

    assert excinfo.value.current_sha == shas[0]
    assert await repo.resolve_ref(OBSERVED) == shas[0]


async def test_create_ref_immutable_accepts_a_race_lost_to_the_same_sha(
    scratch_dir, chain, monkeypatch
):
    """Another creator wins between the read and the write, writing our SHA."""
    repo, _work, shas = chain
    real_run = repo._run
    raced = []

    async def racing_run(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref" and not raced:
            raced.append(True)
            subprocess.run(
                ["git", "--git-dir", str(repo.git_dir), "update-ref", OBSERVED, shas[1]],
                check=True, capture_output=True,
            )
        return await real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    result = await repo.create_ref_immutable(OBSERVED, shas[1])

    assert raced == [True]
    assert result.disposition == "verified"
    assert await repo.resolve_ref(OBSERVED) == shas[1]


async def test_create_ref_immutable_reports_a_race_lost_to_another_sha(
    scratch_dir, chain, monkeypatch
):
    repo, _work, shas = chain
    real_run = repo._run
    raced = []

    async def racing_run(extra_args, raise_on_error=True):
        if extra_args[0] == "update-ref" and not raced:
            raced.append(True)
            subprocess.run(
                ["git", "--git-dir", str(repo.git_dir), "update-ref", OBSERVED, shas[2]],
                check=True, capture_output=True,
            )
        return await real_run(extra_args, raise_on_error=raise_on_error)

    monkeypatch.setattr(repo, "_run", racing_run)
    with pytest.raises(RefImmutableConflictError) as excinfo:
        await repo.create_ref_immutable(OBSERVED, shas[1])

    assert excinfo.value.current_sha == shas[2]
    assert await repo.resolve_ref(OBSERVED) == shas[2]


async def test_create_ref_immutable_rejects_the_null_object_id(scratch_dir, chain):
    repo, _work, shas = chain
    await repo.create_ref_immutable(OBSERVED, shas[0])

    with pytest.raises(RepoError) as excinfo:
        await repo.create_ref_immutable(OBSERVED, "0" * len(shas[0]))

    assert not isinstance(excinfo.value, RefImmutableConflictError)
    assert await repo.resolve_ref(OBSERVED) == shas[0]


async def test_create_ref_immutable_reports_a_hard_failure_as_repo_error(scratch_dir, chain):
    repo, _work, shas = chain
    with pytest.raises(RepoError) as excinfo:
        await repo.create_ref_immutable("not a valid ref name", shas[0])

    assert not isinstance(excinfo.value, RefImmutableConflictError)


async def test_create_ref_immutable_needs_no_work_tree(scratch_dir, chain):
    repo, _work, shas = chain
    cached = Repo(repo.git_dir)

    assert (await cached.create_ref_immutable(OBSERVED, shas[0])).disposition == "created"


# ---------------------------------------------------------------------------
# Object and index plumbing
# ---------------------------------------------------------------------------


def _sqlite_bytes(path):
    """Write a small real SQLite database and return its exact bytes."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v BLOB)")
    conn.execute("INSERT INTO t (id, v) VALUES (1, ?)", (bytes(range(256)),))
    conn.commit()
    conn.close()
    return pathlib.Path(path).read_bytes()


async def test_blob_at_preserves_exact_binary_content(scratch_dir):
    """A SQLite blob survives extraction byte for byte, work tree untouched."""
    repo_dir = pathlib.Path(scratch_dir) / "repo"
    repo = _make_normal_repo(repo_dir)
    original = _sqlite_bytes(repo_dir / "core.db")
    await repo.stage(["core.db"])
    committed = await repo.commit("add db")

    # Local work moves on; the extraction must not see or disturb it.
    (repo_dir / "core.db").write_bytes(original + b"\x00local\xff")

    dest = pathlib.Path(scratch_dir) / "extracted.db"
    await repo.blob_at(committed, "core.db", dest)

    assert dest.read_bytes() == original
    assert (repo_dir / "core.db").read_bytes() == original + b"\x00local\xff"
    assert [entry["path"] for entry in await repo.status()] == ["core.db"]


async def test_tree_entries_reports_mode_type_and_raw_path_bytes(scratch_dir):
    repo_dir = pathlib.Path(scratch_dir) / "repo"
    repo = _make_normal_repo(repo_dir)
    (repo_dir / "core.db").write_bytes(b"\x00\x01\x02")
    (repo_dir / "script.sh").write_text("#!/bin/sh\n")
    (repo_dir / "script.sh").chmod(0o755)
    (repo_dir / "nested").mkdir()
    (repo_dir / "nested" / "inner").write_text("x")
    await repo.stage(None)
    head = await repo.commit("mixed tree")

    by_path = {entry.path: entry for entry in await repo.tree_entries(head)}

    assert set(by_path) == {b"core.db", b"script.sh", b"nested"}
    assert by_path[b"core.db"].mode == "100644"
    assert by_path[b"core.db"].object_type == "blob"
    assert by_path[b"script.sh"].mode == "100755"
    assert by_path[b"nested"].object_type == "tree"


async def test_tree_entries_survives_newline_and_non_utf8_paths(scratch_dir):
    """Path bytes come back intact, so no line-oriented parser can split them.

    The tree is built with plumbing rather than files: macOS rejects a
    filename that is not valid UTF-8, while a Git tree from another platform
    can still name one.
    """
    repo_dir = pathlib.Path(scratch_dir) / "repo"
    repo = _make_normal_repo(repo_dir)
    awkward = b"od\nd\xff name"

    blob = subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "hash-object", "-w", "--stdin"],
        input=b"x", capture_output=True, check=True,
    ).stdout.decode().strip()
    tree = subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "mktree", "-z"],
        input=b"100644 blob " + blob.encode() + b"\t" + awkward + b"\x00",
        capture_output=True, check=True,
    ).stdout.decode().strip()
    head = await repo.commit_tree(tree, [], "awkward path")

    entries = await repo.tree_entries(head)

    assert [entry.path for entry in entries] == [awkward]
    assert entries[0].object_type == "blob"


async def test_read_tree_changes_only_the_index(scratch_dir):
    repo_dir = pathlib.Path(scratch_dir) / "repo"
    repo = _make_normal_repo(repo_dir)
    (repo_dir / "core.db").write_bytes(b"first")
    await repo.stage(["core.db"])
    first = await repo.commit("first")
    (repo_dir / "core.db").write_bytes(b"second")
    await repo.stage(["core.db"])
    second = await repo.commit("second")

    (repo_dir / "core.db").write_bytes(b"live")
    await repo.read_tree(first)

    assert await repo.head() == second
    assert (repo_dir / "core.db").read_bytes() == b"live"
    # The index now holds `first`, so the staged diff is against it.
    staged = (await repo._run(["diff", "--cached", "--name-only", second])).stdout.split()
    assert staged == ["core.db"]


async def test_commit_tree_declares_its_parents_and_moves_no_ref(scratch_dir):
    repo_dir = pathlib.Path(scratch_dir) / "repo"
    repo = _make_normal_repo(repo_dir)
    (repo_dir / "core.db").write_bytes(b"base")
    await repo.stage(["core.db"])
    base = await repo.commit("base")
    await repo.checkout_branch("side", start_point=base)
    (repo_dir / "core.db").write_bytes(b"side")
    await repo.stage(["core.db"])
    side = await repo.commit("side")
    await repo.checkout_branch("main", start_point=base)

    (repo_dir / "core.db").write_bytes(b"merged")
    await repo.stage(["core.db"])
    tree = await repo.write_tree()
    merged = await repo.commit_tree(tree, [base, side], "merge side")

    parents = (await repo._run(["rev-list", "--parents", "-n", "1", merged])).stdout.split()
    assert parents[1:] == [base, side]
    assert await repo.resolve_ref("refs/heads/main") == base
    assert await repo.resolve_ref("refs/heads/side") == side
