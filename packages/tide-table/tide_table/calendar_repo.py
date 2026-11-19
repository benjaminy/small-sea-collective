"""A team calendar kept as a git repository, one .ics file per event.

Each teammate has their own copy. Saving an event commits it. Sync uses Cod
Sync: `publish` sends local history to the teammate's own store, `fetch`
brings a teammate's history into a local ref, and `merge` combines it with
local history.

When both sides changed the same event file, `merge` asks `merge_event` to
combine the two versions, using the git merge base as the common version.
If any event cannot be merged that way, `merge` changes nothing and raises
`EventConflict`; the teammate's history stays in its ref for later.

With a signer (see hub_signer.py), every commit is signed and Cod Sync
accepts a fetched or published history only if the signer approves it.
Without one, commits are unsigned and nothing is checked.

`publish`, `fetch` and `merge` are coroutines; saving and deleting are not
yet, because the CalDAV server that calls them still runs on threads.
"""

import pathlib
import subprocess

from cod_sync.aio.git import git_cmd_binary
from cod_sync.aio.protocol import CodSync
from cod_sync.aio.repo import Repo as AioRepo
from cod_sync.repo import Repo, RepoError

from tide_table.merge import Conflict, Unsupported, merge_event


class EventConflict(Exception):
    def __init__(self, paths):
        super().__init__(f"events changed on both sides in ways that do not merge: {paths}")
        self.paths = paths


def peer_ref(teammate_id_hex):
    return f"refs/peers/{teammate_id_hex}/main"


class CalendarRepo:
    def __init__(self, path, signer=None):
        self.path = pathlib.Path(path)
        self.git_dir = self.path / ".git"
        self.signer = signer
        self.repo = Repo(self.git_dir, self.path, env=signer.env if signer else None)
        self.aio_repo = AioRepo(self.git_dir, self.path, env=self.repo.env)

    @classmethod
    def create(cls, path, author, signer=None):
        path = pathlib.Path(path)
        path.mkdir(parents=True, exist_ok=True)
        Repo.init(path / ".git")
        cal = cls(path, signer)
        cal.repo.config("user.name", author)
        cal.repo.config("user.email", f"{author.lower()}@tide-table.invalid")
        # Git must not merge event text line by line: it would happily combine
        # a new DTSTART from one side with a new DTEND from the other. With
        # merge unset, every event both sides changed comes back as a conflict
        # for `merge` to hand to merge_event.
        (cal.git_dir / "info").mkdir(exist_ok=True)
        (cal.git_dir / "info" / "attributes").write_text("*.ics -merge\n")
        return cal

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", "--git-dir", str(self.git_dir), "--work-tree", str(self.path), *args],
            capture_output=True, check=check, env=self.repo.env,
        )

    def read(self, name):
        return (self.path / name).read_bytes()

    def commit(self, message):
        """Commit staged changes. Returns the new SHA, or None if nothing is staged."""
        return self.repo.commit(self._message(message))

    def _message(self, message):
        return self.signer.message(message) if self.signer else message

    def save(self, name, data, message):
        (self.path / name).write_bytes(data)
        self.repo.stage([name])
        return self.commit(message)

    def delete(self, name, message):
        self._git("rm", "-q", "--", name)
        return self.commit(message)

    async def publish(self, store):
        """Publish main to store, an async store (cod_sync.aio.store)."""
        return await CodSync(self.aio_repo, store, verifier=self.signer).publish()

    async def fetch(self, store, teammate_id_hex):
        """Fetch the teammate's history from store into their parked ref; return its head."""
        cod_sync = CodSync(self.aio_repo, store, verifier=self.signer)
        return (await cod_sync.fetch(pin_to_ref=peer_ref(teammate_id_hex))).pinned_head

    async def _agit(self, *args, check=True):
        return await git_cmd_binary(["--git-dir", str(self.git_dir), "--work-tree", str(self.path), *args],
                                    raise_on_error=check, env=self.repo.env)

    async def merge(self, ref):
        """Merge ref into main. Returns the paths merged event by event."""
        theirs = await self.aio_repo.resolve_ref(ref)
        if not await self.aio_repo.has_commits():
            await self._agit("checkout", "-B", "main", theirs)
            return []
        if await self.aio_repo.is_ancestor(theirs, "HEAD"):
            return []
        # Teammates who each created events before their first sync have
        # histories with no common commit; each event is its own file, so
        # merging them is safe.
        git_merge = await self._agit("merge", "--no-commit", "--no-edit", "--allow-unrelated-histories", theirs,
                                     check=False)
        conflicts = await self.aio_repo.conflict_paths()
        if git_merge.returncode and (git_merge.returncode != 1 or not conflicts):
            raise RepoError(f"merge {ref!r} failed (exit {git_merge.returncode}): {git_merge.stderr.strip()}")
        merged, unresolved = [], []
        for path in conflicts:
            stages = await self._stages(path)
            result = None
            if path.endswith(".ics") and set(stages) == {1, 2, 3}:
                try:
                    result = merge_event(stages[1], stages[2], stages[3])
                except Unsupported:
                    pass
            if result is None or isinstance(result, Conflict):
                unresolved.append(path)
                continue
            (self.path / path).write_bytes(result.text)
            await self.aio_repo.stage([path])
            merged.append(path)
        if unresolved:
            await self._agit("merge", "--abort")
            raise EventConflict(unresolved)
        if (await self._agit("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)).returncode == 0:
            await self._agit("commit", "-m", self._message(f"Merge {ref}"))
        return merged

    async def _stages(self, path):
        """Base (1), ours (2) and theirs (3) versions of a conflicted path."""
        out = (await self._agit("ls-files", "-u", "-z", "--", path)).stdout.decode()
        stages = {}
        for entry in filter(None, out.split("\0")):
            info, _ = entry.split("\t", 1)
            _mode, sha, stage = info.split()
            stages[int(stage)] = (await self._agit("cat-file", "blob", sha)).stdout
        return stages
