"""Async local git repository wrapper: the async twin of cod_sync.repo.

Same methods and behavior as cod_sync.repo.Repo, with every method that runs
git awaitable. Exceptions, result types and bundle header parsing are shared
with the sync module.

work_tree=None means CACHED mode (bare-style, no checkout files).
Work-tree-requiring methods raise NoWorkTreeError in that mode.
"""

import os
import pathlib
import tempfile
from typing import Dict, List, Optional, Set, Union

from cod_sync.async_git import git_cmd as _git_cmd, git_cmd_binary as _git_cmd_binary
from cod_sync.git import GitCmdFailed
from cod_sync.repo import (  # noqa: F401  (re-exported for callers of this module)
    REF_ADVANCE_ATTEMPTS,
    BundleFormatError,
    ConflictError,
    NoWorkTreeError,
    RefAdvanceContendedError,
    RefAdvanceResult,
    RefCreateResult,
    RefDivergedError,
    RefImmutableConflictError,
    RepoError,
    SignatureRow,
    TreeEntry,
    parse_bundle_prerequisites,
)


class Repo:
    """A local git repository identified by its git_dir and optional work_tree.

    work_tree=None means CACHED mode (bare-style, no checkout files).
    """

    def __init__(
        self, git_dir: Union[str, pathlib.Path], work_tree: Optional[Union[str, pathlib.Path]] = None,
        env=None,
    ):
        self.git_dir = pathlib.Path(git_dir)
        self.work_tree = pathlib.Path(work_tree) if work_tree else None
        self.env = dict(env) if env is not None else None

    def with_work_tree(self, work_tree: Union[str, pathlib.Path]) -> "Repo":
        """Return a new Repo instance with the same git_dir and a new work_tree."""
        return Repo(self.git_dir, work_tree, env=self.env)

    async def config(self, key: str, value: str):
        """Set a config value in the repository."""
        await self._run(["config", key, value])

    async def configure_signing(self, signing_key: Union[str, pathlib.Path]):
        """Sign every commit this repository creates with an SSH private key.

        An Ed25519 key file signs without an ssh-agent, so headless callers
        need no agent. Once this is set a signing failure fails the commit;
        git does not fall back to creating an unsigned one.
        """
        await self.config("gpg.format", "ssh")
        await self.config("user.signingkey", str(signing_key))
        await self.config("commit.gpgsign", "true")

    async def _signing_configured(self) -> bool:
        """Whether commit.gpgsign is on for this repository."""
        result = await self._run(
            ["config", "--type=bool", "--get", "commit.gpgsign"], raise_on_error=False
        )
        if result.returncode not in (0, 1):
            raise RepoError(f"could not read commit.gpgsign: {result.stderr.strip()}")
        return result.stdout.strip() == "true"

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _base_args(self) -> List[str]:
        """git args that identify this repo for any command."""
        return ["--git-dir", str(self.git_dir)]

    def _wt_args(self) -> List[str]:
        """Additional args when a work-tree is needed."""
        return ["--work-tree", str(self.work_tree)]

    async def _run(self, extra_args: List[str], raise_on_error: bool = True):
        """Run a git command with the repo's identity args prepended."""
        try:
            return await _git_cmd(self._base_args() + extra_args, raise_on_error=raise_on_error, env=self.env)
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc

    async def _run_binary(self, extra_args: List[str]):
        """Run a git command whose stdout is bytes rather than text."""
        try:
            return await _git_cmd_binary(self._base_args() + extra_args, env=self.env)
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc

    async def _run_wt(self, extra_args: List[str], raise_on_error: bool = True, method_name: str = "<unknown>"):
        """Run a git command that requires the work-tree."""
        if self.work_tree is None:
            raise NoWorkTreeError(method_name, self.git_dir)
        try:
            return await _git_cmd(
                self._base_args() + self._wt_args() + extra_args,
                raise_on_error=raise_on_error,
                env=self.env,
            )
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc

    # ------------------------------------------------------------------ #
    # Repo setup
    # ------------------------------------------------------------------ #

    @staticmethod
    async def init(git_dir: Union[str, pathlib.Path], initial_branch: str = "main", env=None) -> "Repo":
        """Create a new repo at git_dir with core.bare=false.

        Uses bare-init so that git_dir IS the git directory (no .git/
        subdirectory). Returns a CACHED Repo (work_tree=None).
        """
        git_dir = pathlib.Path(git_dir)
        try:
            await _git_cmd(["init", "--bare", "-b", initial_branch, str(git_dir)], env=env)
            await _git_cmd(["--git-dir", str(git_dir), "config", "core.bare", "false"], env=env)
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc
        return Repo(git_dir, env=env)

    # ------------------------------------------------------------------ #
    # Read-only introspection (safe in CACHED and CHECKED_OUT modes)
    # ------------------------------------------------------------------ #

    async def head(self) -> Optional[str]:
        """Return the SHA of HEAD, or None if the repo has no commits."""
        result = await self._run(["rev-parse", "HEAD"], raise_on_error=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    async def has_commits(self) -> bool:
        """Return True if HEAD resolves to a commit."""
        return await self.head() is not None

    async def resolve_ref(self, ref_name: str) -> Optional[str]:
        """Return the SHA for ref_name, or None if it doesn't exist."""
        result = await self._run(["rev-parse", "--verify", ref_name], raise_on_error=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    async def is_ancestor(self, maybe_ancestor: str, descendant: str = "HEAD") -> bool:
        """Return True if maybe_ancestor is an ancestor of descendant."""
        result = await self._run(
            ["merge-base", "--is-ancestor", maybe_ancestor, descendant],
            raise_on_error=False,
        )
        return result.returncode == 0

    async def signature_report(
        self, rev: str, allowed_signers_file: Union[str, pathlib.Path]
    ) -> tuple:
        """Report the signature verdict for rev and every commit it reaches.

        Returns (rows, diagnostics). Inspect original objects and require a
        complete history. Git exits 0 even for rejected signatures, so retain
        diagnostics as well as the status characters.
        """
        if (await self._run(["rev-parse", "--is-shallow-repository"])).stdout.strip() == "true":
            raise RepoError("signature verification requires a non-shallow repository")
        pathlib.Path(allowed_signers_file).read_bytes()
        # A graft file (.git/info/grafts, or GIT_GRAFT_FILE in the caller's
        # environment) rewrites parent links and could hide an ancestor from
        # this traversal, so force it off.
        env = dict(self.env) if self.env is not None else dict(os.environ)
        env["GIT_GRAFT_FILE"] = os.devnull
        try:
            result = await _git_cmd(
                self._base_args()
                + [
                    "--no-replace-objects",
                    "-c",
                    "gpg.format=ssh",
                    "-c",
                    f"gpg.ssh.allowedSignersFile={allowed_signers_file}",
                    # Setting GIT_GRAFT_FILE above makes Git print its graft
                    # deprecation hint on every call, not just grafted ones;
                    # silence that expected hint so real diagnostics still
                    # surface in stderr.
                    "-c",
                    "advice.graftFileDeprecated=false",
                    "log",
                    "--no-show-signature",
                    "--format=%H%x09%G?%x09%GF",
                    rev,
                ],
                env=env,
            )
        except GitCmdFailed as exc:
            raise RepoError(str(exc), cause=exc) from exc
        rows = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            commit, _, rest = line.partition("\t")
            status, _, fingerprint = rest.partition("\t")
            rows.append(SignatureRow(commit, status, fingerprint))
        return rows, result.stderr

    async def log(self, limit: int = 10) -> List[Dict[str, str]]:
        """Return up to limit log entries as list of dicts with 'sha' and 'message'."""
        result = await self._run(
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

    async def has_commit(self, sha: str) -> bool:
        """Return True if sha names a commit object present in this repo."""
        result = await self._run(
            ["cat-file", "-e", f"{sha}^{{commit}}"], raise_on_error=False
        )
        return result.returncode == 0

    async def merge_base(self, left: str, right: str) -> Optional[str]:
        """Return the best common ancestor of left and right, or None if unrelated."""
        result = await self._run(["merge-base", left, right], raise_on_error=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    # ------------------------------------------------------------------ #
    # Bundles
    # ------------------------------------------------------------------ #

    async def create_bundle(self, path: Union[str, pathlib.Path], rev_args: List[str]):
        """Write a bundle at path covering rev_args (e.g. ["^<sha>", "main"])."""
        await self._run(["bundle", "create", str(path)] + list(rev_args))

    async def create_bundle_from_head(
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
                await _git_cmd(
                    [
                        "clone",
                        "--shared",
                        "--bare",
                        str(self.git_dir.resolve()),
                        str(snapshot_path),
                    ],
                    env=self.env,
                )
            except GitCmdFailed as exc:
                raise RepoError(str(exc), cause=exc) from exc

            snapshot = Repo(snapshot_path)
            await snapshot._run(["update-ref", "refs/heads/main", head])
            rev_args = ["refs/heads/main"]
            if predecessor_head is not None:
                rev_args.insert(0, f"^{predecessor_head}")
            await snapshot.create_bundle(path, rev_args)

    async def verify_bundle(self, path: Union[str, pathlib.Path]):
        """Check the bundle header and that its prerequisites are present."""
        await self._run(["bundle", "verify", str(path)])

    async def bundle_heads(self, path: Union[str, pathlib.Path]) -> Dict[str, str]:
        """Return the refs a bundle advertises, without importing any object."""
        result = await self._run(["bundle", "list-heads", str(path)])
        return self._parse_ref_lines(result.stdout)

    async def bundle_prerequisites(self, path: Union[str, pathlib.Path]) -> Set[str]:
        """Return the object ids the bundle at path declares as prerequisites.

        Unlike verify_bundle, this works when the prerequisites are absent,
        which is the state a backward chain walk is in when it needs them.
        """
        return parse_bundle_prerequisites(path)

    async def import_bundle(self, path: Union[str, pathlib.Path]) -> Dict[str, str]:
        """Import a bundle's objects and return the refs it advertised.

        Creates no ref and writes no FETCH_HEAD; the caller decides what, if
        anything, points at the imported commits.
        """
        result = await self._run(["bundle", "unbundle", str(path)])
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

    async def advance_ref(self, ref_name: str, new_sha: str) -> RefAdvanceResult:
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

        async def classify(current: Optional[str]) -> Optional[RefAdvanceResult]:
            if current == new_sha:
                return RefAdvanceResult(ref_name, "unchanged", current, new_sha)
            if current is not None:
                if await self.is_ancestor(new_sha, current):
                    return RefAdvanceResult(ref_name, "stale", current, current)
                if not await self.is_ancestor(current, new_sha):
                    raise RefDivergedError(ref_name, current, new_sha)
            return None

        previous = await self.resolve_ref(ref_name)
        hard_failure = None
        for _ in range(REF_ADVANCE_ATTEMPTS):
            outcome = await classify(previous)
            if outcome is not None:
                return outcome
            # "" as the old value asserts that the ref does not exist yet.
            result = await self._run(
                ["update-ref", ref_name, new_sha, previous or ""],
                raise_on_error=False,
            )
            if result.returncode == 0:
                disposition = "advanced" if previous else "created"
                return RefAdvanceResult(ref_name, disposition, previous, new_sha)
            observed = await self.resolve_ref(ref_name)
            if observed == previous:
                hard_failure = result
            previous = observed

        outcome = await classify(previous)
        if outcome is not None:
            return outcome
        if hard_failure is not None:
            raise RepoError(
                f"update-ref {ref_name} -> {new_sha} failed: "
                f"{hard_failure.stderr.strip()}"
            )
        raise RefAdvanceContendedError(ref_name, REF_ADVANCE_ATTEMPTS)

    async def list_refs(self, prefix: str) -> Dict[str, str]:
        """Return {ref_name: sha} for every ref under prefix.

        Reads live refs, so a caller that wants durable state does not have to
        remember anything across processes.
        """
        result = await self._run(
            ["for-each-ref", "--format=%(refname) %(objectname)", prefix]
        )
        refs = {}
        for line in result.stdout.splitlines():
            name, _, sha = line.strip().partition(" ")
            if name and sha:
                refs[name] = sha
        return refs

    async def create_ref_immutable(self, ref_name: str, sha: str) -> RefCreateResult:
        """Create ref_name at sha, or verify it already names exactly sha.

        The write is `git update-ref <name> <sha> ""`, whose empty old value
        asserts the ref does not exist yet, so a concurrent creator cannot be
        silently overwritten. Losing that race is not a failure when the winner
        wrote the same SHA: the record the caller wanted exists either way.
        Any other existing SHA raises RefImmutableConflictError regardless of
        ancestry, and nothing is written.
        """
        if set(sha) == {"0"}:
            raise RepoError(
                f"cannot create {ref_name} at the null object id {sha}"
            )

        async def settle(current: Optional[str]) -> Optional[RefCreateResult]:
            if current == sha:
                return RefCreateResult(ref_name, "verified", sha)
            if current is not None:
                raise RefImmutableConflictError(ref_name, current, sha)
            return None

        settled = await settle(await self.resolve_ref(ref_name))
        if settled is not None:
            return settled

        result = await self._run(
            ["update-ref", ref_name, sha, ""], raise_on_error=False
        )
        if result.returncode == 0:
            return RefCreateResult(ref_name, "created", sha)

        settled = await settle(await self.resolve_ref(ref_name))
        if settled is not None:
            return settled
        raise RepoError(
            f"update-ref {ref_name} -> {sha} failed: {result.stderr.strip()}"
        )

    # ------------------------------------------------------------------ #
    # Object and index plumbing (no work tree, no ref movement)
    # ------------------------------------------------------------------ #

    async def tree_entries(self, rev: str) -> List[TreeEntry]:
        """Return the entries of rev's root tree, without recursing.

        Reports mode, object type, object id and the raw path bytes and
        nothing else: which paths a tree is allowed to hold is the calling
        application's rule, not Cod Sync's.
        """
        result = await self._run_binary(["ls-tree", "-z", f"{rev}^{{tree}}"])
        entries = []
        for record in result.stdout.split(b"\x00"):
            if not record:
                continue
            # `<mode> <type> <oid>\t<path>`; the path is separated by a tab and
            # the records by NUL, so no line-oriented parsing can see it.
            meta, _, path = record.partition(b"\t")
            mode, object_type, object_id = meta.decode("ascii").split(" ")
            entries.append(TreeEntry(mode, object_type, object_id, path))
        return entries

    async def blob_at(self, rev: str, path: str, dest: Union[str, pathlib.Path]):
        """Write the blob at rev:path to dest, byte for byte.

        Reads out of the object store, so the index and the work tree are
        untouched and the live file at path is never disturbed.
        """
        result = await self._run_binary(["cat-file", "blob", f"{rev}:{path}"])
        pathlib.Path(dest).write_bytes(result.stdout)

    async def read_tree(self, rev: str):
        """Load rev's tree into the index, leaving the work tree alone."""
        await self._run(["read-tree", f"{rev}^{{tree}}"])

    async def write_tree(self) -> str:
        """Write the current index out as a tree object and return its id."""
        return (await self._run(["write-tree"])).stdout.strip()

    async def commit_tree(self, tree: str, parents: List[str], message: str) -> str:
        """Create a commit object for tree, and move no ref.

        The caller decides whether anything points at the result, which is what
        keeps history construction separable from adopting it.
        """
        args = ["commit-tree", tree]
        if await self._signing_configured():
            # commit-tree is plumbing and ignores commit.gpgsign, so this path
            # has to ask for the signature itself.
            args.append("-S")
        for parent in parents:
            args += ["-p", parent]
        return (await self._run(args + ["-m", message])).stdout.strip()

    # ------------------------------------------------------------------ #
    # Work-tree operations (require work_tree to be set)
    # ------------------------------------------------------------------ #

    async def status(self) -> List[Dict[str, str]]:
        """Return porcelain status as list of dicts with 'xy' and 'path'."""
        result = await self._run_wt(
            ["status", "--porcelain"], method_name="status"
        )
        entries = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            entries.append({"xy": line[:2], "path": line[3:]})
        return entries

    async def stage(self, files: Optional[List[str]] = None):
        """Stage files for commit. If files is None, stages everything."""
        if files is None:
            await self._run_wt(["add", "--all"], method_name="stage")
        else:
            await self._run_wt(["add", "--"] + list(files), method_name="stage")

    async def commit(self, message: str) -> Optional[str]:
        """Commit staged changes. Returns the new SHA, or None if nothing staged."""
        check = await self._run_wt(
            ["diff", "--cached", "--quiet"], raise_on_error=False, method_name="commit"
        )
        if check.returncode == 0:
            return None
        await self._run_wt(["commit", "-m", message], method_name="commit")
        return await self.head()

    async def work_tree_paths_differ_from_head(self, paths: List[str]) -> bool:
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
        check = await self._run_wt(
            ["diff", "--quiet", "HEAD", "--"] + paths,
            raise_on_error=False,
            method_name="work_tree_paths_differ_from_head",
        )
        return check.returncode != 0

    async def commit_paths(self, paths: List[str], message: str) -> Optional[str]:
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
        if not await self.work_tree_paths_differ_from_head(paths):
            return None
        await self._run_wt(
            ["commit", "-m", message, "--"] + paths, method_name="commit_paths"
        )
        return await self.head()

    async def checkout_head(self):
        """Refresh work tree to HEAD (git checkout HEAD -- .)."""
        await self._run_wt(["checkout", "HEAD", "--", "."], method_name="checkout_head")

    async def checkout_branch(self, branch: str, start_point: Optional[str] = None):
        """Create or reset branch to start_point (or HEAD if omitted)."""
        args = ["checkout", "-B", branch]
        if start_point is not None:
            args.append(start_point)
        await self._run_wt(args, method_name="checkout_branch")

    async def merge(self, ref: str, message: Optional[str] = None):
        """Merge ref into the current branch. Raises ConflictError on conflicts."""
        result = await self._run_wt(
            ["merge"] + (["-m", message] if message is not None else []) + [ref],
            raise_on_error=False, method_name="merge"
        )
        if result.returncode != 0:
            paths = await self.conflict_paths()
            if paths:
                raise ConflictError(paths)
            # Non-conflict failure — wrap as generic RepoError
            raise RepoError(f"merge {ref!r} failed (exit {result.returncode})")

    async def conflict_paths(self) -> List[str]:
        """Return list of paths with unresolved conflicts."""
        result = await self._run_wt(
            ["diff", "--name-only", "--diff-filter=U"],
            method_name="conflict_paths",
        )
        return [p for p in result.stdout.splitlines() if p.strip()]
