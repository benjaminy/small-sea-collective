"""Local git repository wrapper.

Repo wraps a (git_dir, work_tree) pair and exposes generic DVCS methods.
gitCmd remains a private implementation detail; callers use Repo instead.

work_tree=None means CACHED mode (bare-style, no checkout files).
Work-tree-requiring methods raise NoWorkTreeError in that mode.
"""

import pathlib
from typing import List, Optional, Dict, Union

from cod_sync.protocol import GitCmdFailed, gitCmd as _gitCmd


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
