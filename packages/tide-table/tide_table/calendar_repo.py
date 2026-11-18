"""A team calendar kept as a git repository, one .ics file per event.

Each teammate has their own copy. Saving an event commits it. Sync uses Cod
Sync: `publish` sends local history to the teammate's own store, `fetch`
brings a teammate's history into a local ref, and `merge` combines it with
local history.

When both sides changed the same event file, `merge` asks `merge_event` to
combine the two versions, using the git merge base as the common version.
If any event cannot be merged that way, `merge` changes nothing and raises
`EventConflict`; the teammate's history stays in its ref for later.
"""

import pathlib
import subprocess

from cod_sync.protocol import CodSync
from cod_sync.repo import Repo, RepoError

from tide_table.merge import Conflict, Unsupported, merge_event


class EventConflict(Exception):
    def __init__(self, paths):
        super().__init__(f"events changed on both sides in ways that do not merge: {paths}")
        self.paths = paths


def peer_ref(teammate_id_hex):
    return f"refs/peers/{teammate_id_hex}/main"


class CalendarRepo:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.git_dir = self.path / ".git"
        self.repo = Repo(self.git_dir, self.path)

    @classmethod
    def create(cls, path, author):
        path = pathlib.Path(path)
        path.mkdir(parents=True, exist_ok=True)
        Repo.init(path / ".git")
        cal = cls(path)
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
            capture_output=True, check=check,
        )

    def read(self, name):
        return (self.path / name).read_bytes()

    def save(self, name, data, message):
        (self.path / name).write_bytes(data)
        self.repo.stage([name])
        return self.repo.commit(message)

    def delete(self, name, message):
        self._git("rm", "-q", "--", name)
        return self.repo.commit(message)

    def publish(self, store):
        return CodSync(self.repo, store).publish()

    def fetch(self, store, teammate_id_hex):
        return CodSync(self.repo, store).fetch(pin_to_ref=peer_ref(teammate_id_hex)).pinned_head

    def merge(self, ref):
        """Merge ref into main. Returns the paths merged event by event."""
        theirs = self.repo.resolve_ref(ref)
        if not self.repo.has_commits():
            self._git("checkout", "-B", "main", theirs)
            return []
        if self.repo.is_ancestor(theirs, "HEAD"):
            return []
        # Teammates who each created events before their first sync have
        # histories with no common commit; each event is its own file, so
        # merging them is safe.
        git_merge = self._git("merge", "--no-commit", "--no-edit", "--allow-unrelated-histories", theirs, check=False)
        conflicts = self.repo.conflict_paths()
        if git_merge.returncode and (git_merge.returncode != 1 or not conflicts):
            detail = git_merge.stderr.decode(errors="replace").strip()
            raise RepoError(f"merge {ref!r} failed (exit {git_merge.returncode}): {detail}")
        merged, unresolved = [], []
        for path in conflicts:
            stages = self._stages(path)
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
            self.repo.stage([path])
            merged.append(path)
        if unresolved:
            self._git("merge", "--abort")
            raise EventConflict(unresolved)
        if self._git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0:
            self._git("commit", "--no-edit")
        return merged

    def _stages(self, path):
        """Base (1), ours (2) and theirs (3) versions of a conflicted path."""
        out = self._git("ls-files", "-u", "-z", "--", path).stdout.decode()
        stages = {}
        for entry in filter(None, out.split("\0")):
            info, _ = entry.split("\t", 1)
            _mode, sha, stage = info.split()
            stages[int(stage)] = self._git("cat-file", "blob", sha).stdout
        return stages
