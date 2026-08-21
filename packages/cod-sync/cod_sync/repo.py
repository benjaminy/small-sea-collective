"""Local git repository wrapper.

Repo wraps a (git_dir, work_tree) pair and exposes generic DVCS methods.
gitCmd remains a private implementation detail; callers use Repo instead.

work_tree=None means CACHED mode (bare-style, no checkout files).
Work-tree-requiring methods raise NoWorkTreeError in that mode.
"""

import pathlib
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Union

from cod_sync.git import GitCmdFailed, gitCmd as _gitCmd


class RepoError(Exception):
    """Base class for all Repo failures.

    Wraps the underlying GitCmdFailed so raw git info is available for
    debugging but is not part of the advertised API.
    """

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class NoWorkTreeError(RepoError):
    """Raised when a work-tree method is called on a CACHED Repo."""

    def __init__(self, method_name: str, git_dir: pathlib.Path):
        super().__init__(
            f"{method_name}() requires a work_tree but Repo({git_dir}) is in CACHED mode"
        )


class ConflictError(RepoError):
    """Raised by merge() when the merge leaves unresolved conflicts."""

    def __init__(self, conflict_paths: List[str]):
        super().__init__(f"Merge conflict in: {', '.join(conflict_paths)}")
        self.conflict_paths = conflict_paths


class BundleFormatError(RepoError):
    """Raised when a file does not parse as a git bundle header."""


class RefDivergedError(RepoError):
    """Raised by advance_ref() when the ref and the new value have diverged."""

    def __init__(self, ref_name: str, current_sha: str, new_sha: str):
        super().__init__(
            f"{ref_name} is at {current_sha}, which neither contains nor is contained by {new_sha}"
        )
        self.ref_name = ref_name
        self.current_sha = current_sha
        self.new_sha = new_sha


class RefAdvanceContendedError(RepoError):
    """Raised by advance_ref() when repeated compare-and-swap attempts all lose."""

    def __init__(self, ref_name: str, attempts: int):
        super().__init__(
            f"{ref_name} was updated by another writer during all {attempts} attempts"
        )
        self.ref_name = ref_name
        self.attempts = attempts


@dataclass(frozen=True)
class RefAdvanceResult:
    """Outcome of a forward-only ref update.

    disposition is one of:
      "created"   the ref did not exist and now points at new_sha
      "advanced"  the ref moved forward from previous_sha to new_sha
      "unchanged" the ref already pointed at new_sha
      "stale"     the ref already descends from new_sha and was left alone
    """

    ref_name: str
    disposition: str
    previous_sha: Optional[str]
    current_sha: str


#: Ref-update attempts before advance_ref() gives up.
REF_ADVANCE_ATTEMPTS = 5

#: Refuse to scan further than this for a bundle header's terminating blank line.
_BUNDLE_HEADER_LIMIT = 1 << 20

_BUNDLE_SIGNATURES = {b"# v2 git bundle": 2, b"# v3 git bundle": 3}
_BUNDLE_CAPABILITY_KEY_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"
)


def _is_object_id(text: str) -> bool:
    """Return True if text looks like a SHA-1 or SHA-256 object id."""
    if len(text) not in (40, 64):
        return False
    return all(c in "0123456789abcdef" for c in text)


def _read_bundle_header(path: Union[str, pathlib.Path]) -> List[bytes]:
    """Return the bundle header lines at path, excluding the terminating blank line.

    Reads only header bytes; the pack that follows is never touched.
    """
    path = pathlib.Path(path)
    lines = []
    bytes_read = 0
    with open(path, "rb", buffering=0) as handle:
        while True:
            line = handle.readline(_BUNDLE_HEADER_LIMIT - bytes_read + 1)
            if not line:
                raise BundleFormatError(
                    f"{path}: bundle header has no terminating blank line"
                )
            bytes_read += len(line)
            if bytes_read > _BUNDLE_HEADER_LIMIT:
                raise BundleFormatError(
                    f"{path}: bundle header exceeds {_BUNDLE_HEADER_LIMIT} bytes"
                )
            if line == b"\n":
                return lines
            if not line.endswith(b"\n"):
                raise BundleFormatError(
                    f"{path}: bundle header has no terminating blank line"
                )
            lines.append(line[:-1])


def parse_bundle_prerequisites(path: Union[str, pathlib.Path]) -> Set[str]:
    """Return the object ids a bundle declares as prerequisites.

    `git bundle list-heads` omits prerequisites and `git bundle verify` fails
    when they are absent, which is exactly the state a backward chain walk is
    in when it needs to read them. So this parses the header directly.

    The whole header is validated: an unrecognized signature, a capability
    line outside version 3 or after the prerequisites, a prerequisite after an
    advertised ref, a malformed line, or a bundle advertising no ref is a
    BundleFormatError rather than something to skip past.
    """
    path = pathlib.Path(path)
    lines = _read_bundle_header(path)
    if not lines:
        raise BundleFormatError(f"{path}: bundle header is empty")
    version = _BUNDLE_SIGNATURES.get(lines[0])
    if version is None:
        raise BundleFormatError(f"{path}: unrecognized bundle signature {lines[0]!r}")

    prerequisites: Set[str] = set()
    ref_count = 0
    for raw in lines[1:]:
        if raw.startswith(b"@"):
            if version < 3:
                raise BundleFormatError(
                    f"{path}: capability line in a v2 bundle: {raw!r}"
                )
            if prerequisites or ref_count:
                raise BundleFormatError(
                    f"{path}: capability line after bundle contents: {raw!r}"
                )
            key, _, value = raw[1:].partition(b"=")
            if (
                not key
                or any(byte not in _BUNDLE_CAPABILITY_KEY_BYTES for byte in key)
                or b"\x00" in value
            ):
                raise BundleFormatError(
                    f"{path}: malformed capability line: {raw!r}"
                )
            continue
        line = raw.decode("utf-8", errors="replace")
        if line.startswith("-"):
            if ref_count:
                raise BundleFormatError(f"{path}: prerequisite after advertised ref: {line!r}")
            # The commit subject after the object id is an ignorable comment.
            object_id, space, _comment = line[1:].partition(" ")
            if not space or not _is_object_id(object_id):
                raise BundleFormatError(f"{path}: malformed prerequisite line: {line!r}")
            prerequisites.add(object_id)
            continue
        object_id, space, ref_name = line.partition(" ")
        if not space or not _is_object_id(object_id) or not ref_name:
            raise BundleFormatError(f"{path}: malformed header line: {line!r}")
        ref_count += 1

    if ref_count == 0:
        raise BundleFormatError(f"{path}: bundle advertises no refs")
    return prerequisites


class Repo:
    """A local git repository identified by its git_dir and optional work_tree.

    work_tree=None means CACHED mode (bare-style, no checkout files).
    """

    def __init__(
        self, git_dir: Union[str, pathlib.Path], work_tree: Optional[Union[str, pathlib.Path]] = None
    ):
        self.git_dir = pathlib.Path(git_dir)
        self.work_tree = pathlib.Path(work_tree) if work_tree else None

    def with_work_tree(self, work_tree: Union[str, pathlib.Path]) -> "Repo":
        """Return a new Repo instance with the same git_dir and a new work_tree."""
        return Repo(self.git_dir, work_tree)

    def config(self, key: str, value: str):
        """Set a config value in the repository."""
        self._run(["config", key, value])

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _base_args(self) -> List[str]:
        """git args that identify this repo for any command."""
        return ["--git-dir", str(self.git_dir)]

    def _wt_args(self) -> List[str]:
        """Additional args when a work-tree is needed."""
        return ["--work-tree", str(self.work_tree)]

    def _run(self, extra_args: List[str], raise_on_error: bool = True):
        """Run a git command with the repo's identity args prepended."""
        try:
            return _gitCmd(self._base_args() + extra_args, raise_on_error=raise_on_error)
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc

    def _run_wt(self, extra_args: List[str], raise_on_error: bool = True, method_name: str = "<unknown>"):
        """Run a git command that requires the work-tree."""
        if self.work_tree is None:
            raise NoWorkTreeError(method_name, self.git_dir)
        try:
            return _gitCmd(
                self._base_args() + self._wt_args() + extra_args,
                raise_on_error=raise_on_error,
            )
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc

    # ------------------------------------------------------------------ #
    # Repo setup
    # ------------------------------------------------------------------ #

    @staticmethod
    def init(git_dir: Union[str, pathlib.Path], initial_branch: str = "main") -> "Repo":
        """Create a new repo at git_dir with core.bare=false.

        Uses bare-init so that git_dir IS the git directory (no .git/
        subdirectory). Returns a CACHED Repo (work_tree=None).
        """
        git_dir = pathlib.Path(git_dir)
        try:
            _gitCmd(["init", "--bare", "-b", initial_branch, str(git_dir)])
            _gitCmd(["--git-dir", str(git_dir), "config", "core.bare", "false"])
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc
        return Repo(git_dir)

    # ------------------------------------------------------------------ #
    # Read-only introspection (safe in CACHED and CHECKED_OUT modes)
    # ------------------------------------------------------------------ #

    def head(self) -> Optional[str]:
        """Return the SHA of HEAD, or None if the repo has no commits."""
        result = self._run(["rev-parse", "HEAD"], raise_on_error=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def has_commits(self) -> bool:
        """Return True if HEAD resolves to a commit."""
        return self.head() is not None

    def resolve_ref(self, ref_name: str) -> Optional[str]:
        """Return the SHA for ref_name, or None if it doesn't exist."""
        result = self._run(["rev-parse", "--verify", ref_name], raise_on_error=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def is_ancestor(self, maybe_ancestor: str, descendant: str = "HEAD") -> bool:
        """Return True if maybe_ancestor is an ancestor of descendant."""
        result = self._run(
            ["merge-base", "--is-ancestor", maybe_ancestor, descendant],
            raise_on_error=False,
        )
        return result.returncode == 0

    def log(self, limit: int = 10) -> List[Dict[str, str]]:
        """Return up to limit log entries as list of dicts with 'sha' and 'message'."""
        result = self._run(
            ["log", f"--max-count={limit}", "--oneline", "--format=%H %s"],
            raise_on_error=False,
        )
        if result.returncode != 0:
            return []
        entries = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            sha, _, message = line.partition(" ")
            entries.append({"sha": sha, "message": message})
        return entries

    def has_commit(self, sha: str) -> bool:
        """Return True if sha names a commit object present in this repo."""
        result = self._run(
            ["cat-file", "-e", f"{sha}^{{commit}}"], raise_on_error=False
        )
        return result.returncode == 0

    def merge_base(self, left: str, right: str) -> Optional[str]:
        """Return the best common ancestor of left and right, or None if unrelated."""
        result = self._run(["merge-base", left, right], raise_on_error=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    # ------------------------------------------------------------------ #
    # Bundles
    # ------------------------------------------------------------------ #

    def create_bundle(self, path: Union[str, pathlib.Path], rev_args: List[str]):
        """Write a bundle at path covering rev_args (e.g. ["^<sha>", "main"])."""
        self._run(["bundle", "create", str(path)] + list(rev_args))

    def create_bundle_from_head(
        self,
        path: Union[str, pathlib.Path],
        head: str,
        predecessor_head: Optional[str] = None,
    ):
        """Write a main bundle pinned to head rather than the mutable main ref.

        Git bundles advertise refs, not arbitrary object IDs. Use an
        invocation-local shared clone so refs/heads/main can name the captured
        commit without moving any ref in the application repository.
        """
        with tempfile.TemporaryDirectory(prefix="cod-sync-bundle-repo-") as temp_dir:
            snapshot_path = pathlib.Path(temp_dir) / "repo.git"
            try:
                _gitCmd(
                    [
                        "clone",
                        "--shared",
                        "--bare",
                        str(self.git_dir.resolve()),
                        str(snapshot_path),
                    ]
                )
            except GitCmdFailed as exc:
                raise RepoError(str(exc), cause=exc) from exc

            snapshot = Repo(snapshot_path)
            snapshot._run(["update-ref", "refs/heads/main", head])
            rev_args = ["refs/heads/main"]
            if predecessor_head is not None:
                rev_args.insert(0, f"^{predecessor_head}")
            snapshot.create_bundle(path, rev_args)

    def verify_bundle(self, path: Union[str, pathlib.Path]):
        """Check that the bundle at path is valid and its prerequisites are present."""
        self._run(["bundle", "verify", str(path)])

    def bundle_heads(self, path: Union[str, pathlib.Path]) -> Dict[str, str]:
        """Return the refs a bundle advertises, without importing any object."""
        result = self._run(["bundle", "list-heads", str(path)])
        return self._parse_ref_lines(result.stdout)

    def bundle_prerequisites(self, path: Union[str, pathlib.Path]) -> Set[str]:
        """Return the object ids the bundle at path declares as prerequisites.

        Unlike verify_bundle, this works when the prerequisites are absent,
        which is the state a backward chain walk is in when it needs them.
        """
        return parse_bundle_prerequisites(path)

    def import_bundle(self, path: Union[str, pathlib.Path]) -> Dict[str, str]:
        """Import a bundle's objects and return the refs it advertised.

        Creates no ref and writes no FETCH_HEAD; the caller decides what, if
        anything, points at the imported commits.
        """
        result = self._run(["bundle", "unbundle", str(path)])
        return self._parse_ref_lines(result.stdout)

    @staticmethod
    def _parse_ref_lines(text: str) -> Dict[str, str]:
        """Parse `<sha> <ref>` lines as printed by list-heads and unbundle."""
        heads = {}
        for line in text.splitlines():
            sha, _, ref_name = line.strip().partition(" ")
            if not ref_name:
                continue
            heads[ref_name] = sha
        return heads

    # ------------------------------------------------------------------ #
    # Forward-only ref movement
    # ------------------------------------------------------------------ #

    def advance_ref(self, ref_name: str, new_sha: str) -> RefAdvanceResult:
        """Move ref_name to new_sha, but only forward.

        An absent ref is created, an ancestor ref advances, an equal ref is
        unchanged, and a ref that already descends from new_sha is retained as
        "stale". Divergence raises RefDivergedError and moves nothing.

        Every write is a `git update-ref <name> <new> <old>` compare-and-swap,
        so a writer that slips in between the read and the write loses rather
        than being overwritten. Losing means rereading, up to a fixed number of
        attempts, after which the contention itself is the answer. A failure
        that leaves the ref unchanged is retried within the same bound because
        a live lock can fail before its writer moves the ref. If the attempts
        end without a definite disposition, any such unexplained failure is
        preserved as a hard RepoError; only pure observed CAS losses become
        RefAdvanceContendedError.
        """
        # Git reads the null object id as "delete this ref", and the delete
        # succeeds, so an unguarded null would report a write that never
        # happened.
        if set(new_sha) == {"0"}:
            raise RepoError(
                f"cannot advance {ref_name} to the null object id {new_sha}"
            )

        def classify(current: Optional[str]) -> Optional[RefAdvanceResult]:
            if current == new_sha:
                return RefAdvanceResult(ref_name, "unchanged", current, new_sha)
            if current is not None:
                if self.is_ancestor(new_sha, current):
                    return RefAdvanceResult(ref_name, "stale", current, current)
                if not self.is_ancestor(current, new_sha):
                    raise RefDivergedError(ref_name, current, new_sha)
            return None

        previous = self.resolve_ref(ref_name)
        hard_failure = None
        for _ in range(REF_ADVANCE_ATTEMPTS):
            outcome = classify(previous)
            if outcome is not None:
                return outcome
            # "" as the old value asserts that the ref does not exist yet.
            result = self._run(
                ["update-ref", ref_name, new_sha, previous or ""],
                raise_on_error=False,
            )
            if result.returncode == 0:
                disposition = "advanced" if previous else "created"
                return RefAdvanceResult(ref_name, disposition, previous, new_sha)
            observed = self.resolve_ref(ref_name)
            if observed == previous:
                hard_failure = result
            previous = observed

        outcome = classify(previous)
        if outcome is not None:
            return outcome
        if hard_failure is not None:
            raise RepoError(
                f"update-ref {ref_name} -> {new_sha} failed: "
                f"{hard_failure.stderr.strip()}"
            )
        raise RefAdvanceContendedError(ref_name, REF_ADVANCE_ATTEMPTS)

    # ------------------------------------------------------------------ #
    # Work-tree operations (require work_tree to be set)
    # ------------------------------------------------------------------ #

    def status(self) -> List[Dict[str, str]]:
        """Return porcelain status as list of dicts with 'xy' and 'path'."""
        result = self._run_wt(
            ["status", "--porcelain"], method_name="status"
        )
        entries = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            entries.append({"xy": line[:2], "path": line[3:]})
        return entries

    def stage(self, files: Optional[List[str]] = None):
        """Stage files for commit. If files is None, stages everything."""
        if files is None:
            self._run_wt(["add", "--all"], method_name="stage")
        else:
            self._run_wt(["add", "--"] + list(files), method_name="stage")

    def commit(self, message: str) -> Optional[str]:
        """Commit staged changes. Returns the new SHA, or None if nothing staged."""
        check = self._run_wt(
            ["diff", "--cached", "--quiet"], raise_on_error=False, method_name="commit"
        )
        if check.returncode == 0:
            return None
        self._run_wt(["commit", "-m", message], method_name="commit")
        return self.head()

    def work_tree_paths_differ_from_head(self, paths: List[str]) -> bool:
        """Return True if work-tree content at paths differs from HEAD.

        Compares the work tree, not the index, so an unstaged modification at
        one of the named paths counts as a difference. Changes outside paths
        are ignored, staged or not.

        Requires HEAD to resolve; `git diff HEAD` is fatal in a repo with no
        commits, so callers must check has_commits() first.
        """
        paths = list(paths)
        if not paths:
            raise ValueError("paths must contain at least one path")
        check = self._run_wt(
            ["diff", "--quiet", "HEAD", "--"] + paths,
            raise_on_error=False,
            method_name="work_tree_paths_differ_from_head",
        )
        return check.returncode != 0

    def commit_paths(self, paths: List[str], message: str) -> Optional[str]:
        """Commit work-tree content at paths. Returns the new SHA, or None if no-op.

        This is not commit() with a filter: commit() commits the index, while
        this commits work-tree content at the named paths (`git commit -- <paths>`)
        and ignores unrelated index state. Unrelated staged entries are neither
        committed nor unstaged.

        The no-op decision delegates to work_tree_paths_differ_from_head, so it
        inherits that method's precondition: the named paths must already be
        tracked. For an untracked path the check reports nothing to do while
        the commit itself would error.
        """
        paths = list(paths)
        if not paths:
            raise ValueError("paths must contain at least one path")
        if not self.work_tree_paths_differ_from_head(paths):
            return None
        self._run_wt(
            ["commit", "-m", message, "--"] + paths, method_name="commit_paths"
        )
        return self.head()

    def checkout_head(self):
        """Refresh work tree to HEAD (git checkout HEAD -- .)."""
        self._run_wt(["checkout", "HEAD", "--", "."], method_name="checkout_head")

    def checkout_branch(self, branch: str, start_point: Optional[str] = None):
        """Create or reset branch to start_point (or HEAD if omitted)."""
        args = ["checkout", "-B", branch]
        if start_point is not None:
            args.append(start_point)
        self._run_wt(args, method_name="checkout_branch")

    def merge(self, ref: str):
        """Merge ref into the current branch. Raises ConflictError on conflicts."""
        result = self._run_wt(
            ["merge", ref], raise_on_error=False, method_name="merge"
        )
        if result.returncode != 0:
            paths = self.conflict_paths()
            if paths:
                raise ConflictError(paths)
            # Non-conflict failure — wrap as generic RepoError
            raise RepoError(f"merge {ref!r} failed (exit {result.returncode})")

    def conflict_paths(self) -> List[str]:
        """Return list of paths with unresolved conflicts."""
        result = self._run_wt(
            ["diff", "--name-only", "--diff-filter=U"],
            method_name="conflict_paths",
        )
        return [p for p in result.stdout.splitlines() if p.strip()]
