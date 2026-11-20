"""A team calendar kept as one SQLite file in a git repository.

Each teammate has their own copy. The file, calendar.sqlite, has one row per
event: a random UUIDv7 that identifies the event across teammates, the name
CalDAV clients use for it, its UID, and its iCalendar text as the client
wrote it. Saving or deleting an event commits the file.

Sync uses Cod Sync: `publish` sends local history to the teammate's own
store, `fetch` brings a teammate's history into a local ref, and `merge`
combines it with local history. Git merges calendar.sqlite with Tide Table's
merge driver (merge_driver.py), which merges event by event. If any event
cannot be merged, `merge` changes nothing and raises `EventConflict`; the
teammate's history stays in its ref for later.

With a signer (see hub_signer.py), every commit is signed and Cod Sync
accepts a fetched or published history only if the signer approves it.
Without one, commits are unsigned and nothing is checked.

Everything that runs git is a coroutine, on cod_sync's async Repo. Reads
open the file afresh each time, because a merge or checkout replaces it.
"""

import json
import os
import pathlib
import re
import secrets
import sqlite3
import struct
import sys
import time

from cod_sync.aio.git import git_cmd_binary
from cod_sync.aio.protocol import CodSync
from cod_sync.aio.repo import Repo
from cod_sync.repo import RepoError

DB = "calendar.sqlite"
SCHEMA = "CREATE TABLE IF NOT EXISTS events (id BLOB PRIMARY KEY, name TEXT NOT NULL, uid TEXT, ics BLOB NOT NULL)"


class EventConflict(Exception):
    def __init__(self, conflicts):
        super().__init__(f"events changed on both sides in ways that do not merge: {conflicts}")
        self.conflicts = conflicts


def peer_ref(teammate_id_hex):
    return f"refs/peers/{teammate_id_hex}/main"


def uuid7():
    """A UUIDv7 (time-ordered, random) as 16 bytes; the same scheme as small_sea_note_to_self.ids."""
    rand = secrets.token_bytes(10)
    return (struct.pack(">Q", int(time.time() * 1000))[2:]
            + bytes([0x70 | (rand[0] & 0x0F), rand[1], 0x80 | (rand[2] & 0x3F)]) + rand[3:])


def event_uid(data):
    """The UID property of an iCalendar object, or None if it has none."""
    # The value follows the first colon outside any quoted parameter, and may itself contain colons.
    match = re.search(rb'^UID(?:;(?:[^:"\r\n]|"[^"\r\n]*")*)?:(.*?)\r?$', data.replace(b"\r\n ", b""), re.M)
    return match[1].decode("utf-8", "replace") if match else None


class CalendarRepo:
    def __init__(self, path, signer=None):
        self.path = pathlib.Path(path)
        self.git_dir = self.path / ".git"
        self.db = self.path / DB
        self.signer = signer
        self.repo = Repo(self.git_dir, self.path, env=signer.env if signer else None)

    @classmethod
    async def create(cls, path, author, signer=None):
        path = pathlib.Path(path)
        path.mkdir(parents=True, exist_ok=True)
        await Repo.init(path / ".git")
        cal = cls(path, signer)
        await cal.repo.config("user.name", author)
        await cal.repo.config("user.email", f"{author.lower()}@tide-table.invalid")
        await cal.repo.config("merge.tide-table.name", "Tide Table calendar merge")
        await cal.repo.config("merge.tide-table.driver",
                              f"{sys.executable} -m tide_table.merge_driver %O %A %B %L %P")
        info = cal.git_dir / "info"
        info.mkdir(exist_ok=True)
        (info / "attributes").write_text(f"{DB} merge=tide-table\n")
        (info / "exclude").write_text(f"{DB}-journal\n")
        return cal

    # --- reading -------------------------------------------------------------

    def _rows(self, sql, args=()):
        if not self.db.exists():
            return []
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def names(self):
        return [name for (name,) in self._rows("SELECT name FROM events ORDER BY name")]

    def read(self, name):
        """The event's iCalendar text, or None if there is no such event."""
        rows = self._rows("SELECT ics FROM events WHERE name = ?", (name,))
        return rows[0][0] if rows else None

    # --- writing -------------------------------------------------------------

    async def commit(self, message):
        """Commit staged changes. Returns the new SHA, or None if nothing is staged."""
        return await self.repo.commit(await self._message(message))

    async def _message(self, message):
        return await self.signer.message(message) if self.signer else message

    def _write(self, sql, args):
        conn = sqlite3.connect(self.db)
        try:
            with conn:
                conn.execute(SCHEMA)
                conn.execute(sql, args)
        finally:
            conn.close()

    async def save(self, name, data, message):
        """Create or replace the event called name."""
        rows = self._rows("SELECT id FROM events WHERE name = ?", (name,))
        event_id = rows[0][0] if rows else uuid7()
        self._write("INSERT OR REPLACE INTO events (id, name, uid, ics) VALUES (?, ?, ?, ?)",
                    (event_id, name, event_uid(data), data))
        await self.repo.stage([DB])
        return await self.commit(message)

    async def delete(self, name, message):
        self._write("DELETE FROM events WHERE name = ?", (name,))
        await self.repo.stage([DB])
        return await self.commit(message)

    # --- sync ----------------------------------------------------------------

    async def publish(self, store):
        """Publish main to store, an async store (cod_sync.aio.store)."""
        return await CodSync(self.repo, store, verifier=self.signer).publish()

    async def fetch(self, store, teammate_id_hex):
        """Fetch the teammate's history from store into their parked ref; return its head."""
        cod_sync = CodSync(self.repo, store, verifier=self.signer)
        return (await cod_sync.fetch(pin_to_ref=peer_ref(teammate_id_hex))).pinned_head

    async def _git(self, *args, check=True, env=None):
        return await git_cmd_binary(["--git-dir", str(self.git_dir), "--work-tree", str(self.path), *args],
                                    raise_on_error=check, env=env or self.repo.env)

    async def merge(self, ref):
        """Merge ref into main. Returns the names of events merged field by field."""
        theirs = await self.repo.resolve_ref(ref)
        if not await self.repo.has_commits():
            await self._git("checkout", "-B", "main", theirs)
            return []
        if await self.repo.is_ancestor(theirs, "HEAD"):
            return []
        report_path = self.git_dir / "tide-table-merge.json"
        report_path.unlink(missing_ok=True)
        env = {**(self.repo.env or os.environ), "TIDE_TABLE_MERGE_REPORT": str(report_path)}
        # Teammates who each created events before their first sync have
        # histories with no common commit; the driver then merges against an
        # empty calendar, which is safe.
        git_merge = await self._git("merge", "--no-commit", "--no-edit", "--allow-unrelated-histories", theirs,
                                    check=False, env=env)
        report = json.loads(report_path.read_text()) if report_path.exists() else {"merged": [], "conflicts": []}
        if git_merge.returncode:
            await self._git("merge", "--abort", check=False)
            if report["conflicts"]:
                raise EventConflict(report["conflicts"])
            raise RepoError(f"merge {ref!r} failed (exit {git_merge.returncode}): {git_merge.stderr.strip()}")
        if (await self._git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)).returncode == 0:
            await self._git("commit", "-m", await self._message(f"Merge {ref}"))
        return report["merged"]
