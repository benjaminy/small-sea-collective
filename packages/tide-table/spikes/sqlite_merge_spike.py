"""Step 0 of moving Tide Table's calendar into one SQLite file in git.

Two questions:
1. When histories cross, git merges the candidate bases into one base before
   the real merge. Does it call a custom merge driver for that inner merge?
2. With the calendar in one SQLite file and an event-aware driver, does the
   crossed-history delete (see DIARY.md, 2026-11-19) merge cleanly?

Plain git repos fetch straight from each other; Cod Sync and signing are
beside the point here. Run:

    uv run python spikes/sqlite_merge_spike.py WORKDIR

`driver` is the subcommand git runs as the merge driver. It logs each call
to $SPIKE_LOG, then merges like the planned Tide Table driver: Splice Merge
row deltas, `merge_event` for an event both sides edited, and failure (exit 1)
for any other row conflict.
"""

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

from splice_merge.core import apply_delta, compute_delta, reconcile_deltas, sqlite_to_json

from tide_table.merge import Merged, Unsupported, merge_event

FIXTURES = pathlib.Path(__file__).parent.parent / "tests" / "fixtures" / "move-vs-location"
DB = "calendar.sqlite"
EVENT = "event.ics"


# --- the merge driver -------------------------------------------------------

def _events(path):
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT name, ics FROM events"))


def _summary(ics):
    """The fields this spike edits, to show which version a file holds."""
    if ics is None:
        return "deleted"
    lines = ics.decode().splitlines()
    lines = lines[lines.index("BEGIN:VEVENT"):]  # skip the time zone's own DTSTART
    pick = lambda p: next((l.split(":", 1)[1] for l in lines if l.startswith(p)), "-")
    return f"start={pick('DTSTART')[-6:]} location={pick('LOCATION')}"


def driver(base, ours, theirs, marker, path):
    # Git widens the conflict markers by 2 for each level of merging bases, so
    # a marker size above the default 7 means this call builds a merged base.
    log = {"path": path, "marker": int(marker), **{side: _summary(_events(f).get(EVENT))
                            for side, f in (("base", base), ("ours", ours), ("theirs", theirs))}}
    b, o, t = (sqlite_to_json(f) for f in (base, ours, theirs))
    cleaned, conflicts = reconcile_deltas(compute_delta(b, o), compute_delta(b, t))
    unresolved = []
    for c in conflicts:
        name = c.key[0][1]
        if c.table == "events" and c.kind == "update/update":
            versions = [_events(f)[name] for f in (base, ours, theirs)]
            try:
                result = merge_event(*versions)
            except Unsupported:
                result = None
            if isinstance(result, Merged):
                row = {"name": name, "ics": {"__blob__": result.text.hex()}}
                cleaned.setdefault("events", {"inserts": {}, "deletes": {}, "updates": {}})["updates"][c.key] = row
                continue
        unresolved.append(f"{c.kind} {name}")
    log["unresolved"] = unresolved
    with open(os.environ["SPIKE_LOG"], "a") as f:
        f.write(json.dumps(log) + "\n")
    if unresolved:
        sys.exit(1)
    apply_delta(ours, cleaned)


# --- the scenario ------------------------------------------------------------

def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r


def create(repo, author):
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", author)
    git(repo, "config", "user.email", f"{author}@spike.invalid")
    git(repo, "config", "merge.tide.driver", f"{sys.executable} {__file__} driver %O %A %B %L %P")
    (repo / ".git" / "info" / "attributes").write_text(f"{DB} merge=tide\n")


def save(repo, ics, message):
    with sqlite3.connect(repo / DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS events (name TEXT PRIMARY KEY, ics BLOB NOT NULL)")
        if ics is None:
            conn.execute("DELETE FROM events WHERE name = ?", (EVENT,))
        else:
            conn.execute("INSERT OR REPLACE INTO events VALUES (?, ?)", (EVENT, ics))
    conn.close()
    git(repo, "add", DB)
    git(repo, "commit", "-q", "-m", message)


def fetch(me, them, name):
    git(me, "fetch", "-q", str(them), f"main:refs/peers/{name}")


def merge(me, name):
    """Merge the fetched peer ref; return True if the merge succeeded."""
    r = git(me, "merge", "--no-edit", f"refs/peers/{name}", check=False)
    if r.returncode:
        git(me, "merge", "--abort", check=False)
        return False
    return True


def event(repo):
    return _events(repo / DB).get(EVENT)


def scenario(root, deleter):
    alice, bob = root / "alice", root / "bob"
    create(alice, "alice")
    create(bob, "bob")
    fixture = lambda n: (FIXTURES / n).read_bytes()

    save(alice, fixture("base.ics"), "create")
    fetch(bob, alice, "alice")
    assert merge(bob, "alice")
    save(alice, fixture("apple-move.ics"), "move")
    save(bob, fixture("thunderbird-location.ics"), "location")
    # Both fetch before either merges, so each makes its own merge commit.
    fetch(alice, bob, "bob")
    fetch(bob, alice, "alice")
    print("Alice merges Bob's location:", merge(alice, "bob"))
    print("Bob merges Alice's move:    ", merge(bob, "alice"))
    print("Both merges hold the same event bytes:", event(alice) == event(bob))
    print("Both merges hold the same file bytes: ",
          git(alice, "rev-parse", "HEAD:" + DB).stdout == git(bob, "rev-parse", "HEAD:" + DB).stdout)

    repos = {"alice": alice, "bob": bob}
    other = "alice" if deleter == "bob" else "bob"
    save(repos[deleter], None, "delete")
    fetch(repos[other], repos[deleter], deleter)
    bases = git(repos[other], "merge-base", "--all", "HEAD", f"refs/peers/{deleter}").stdout.split()
    print(f"Candidate bases for {other}'s merge of the delete:", len(bases))
    print(f"{other} merges {deleter}'s delete:", merge(repos[other], deleter), "->", _summary(event(repos[other])))
    fetch(repos[deleter], repos[other], other)
    print(f"{deleter} merges {other} back:   ", merge(repos[deleter], other), "->", _summary(event(repos[deleter])))
    print("Same history:", git(alice, "rev-parse", "HEAD").stdout == git(bob, "rev-parse", "HEAD").stdout)


def rooms(root):
    """Alice and Bob each keep their own room after a real conflict, then sync again.

    Their disagreement should come back as a conflict, not vanish.
    """
    alice, bob = root / "alice", root / "bob"
    create(alice, "alice")
    create(bob, "bob")
    location = (FIXTURES / "thunderbird-location.ics").read_bytes()
    save(alice, (FIXTURES / "base.ics").read_bytes(), "create")
    fetch(bob, alice, "alice")
    assert merge(bob, "alice")
    save(alice, location.replace(b"over theere", b"Room A"), "Room A")
    save(bob, location.replace(b"over theere", b"Room B"), "Room B")
    fetch(alice, bob, "bob")
    fetch(bob, alice, "alice")
    print("Alice merges Bob's room:", merge(alice, "bob"))
    print("Bob merges Alice's room:", merge(bob, "alice"))
    # Each resolves the conflict by keeping their own room.
    git(alice, "merge", "-q", "-s", "ours", "--no-edit", "refs/peers/bob")
    git(bob, "merge", "-q", "-s", "ours", "--no-edit", "refs/peers/alice")
    fetch(alice, bob, "bob")
    print("Alice merges Bob's resolution:", merge(alice, "bob"), "->", _summary(event(alice)))


def run(root, title, play):
    shutil.rmtree(root, ignore_errors=True)
    os.environ["SPIKE_LOG"] = str(root / "driver.log")
    root.mkdir(parents=True)
    print(f"=== {title}")
    play(root)
    print("Driver calls (marker size: base / ours / theirs):")
    for line in (root / "driver.log").read_text().splitlines():
        call = json.loads(line)
        print(f"  {call['marker']:2}: base {call['base']} | ours {call['ours']} | theirs {call['theirs']}"
              f"{'  UNRESOLVED ' + str(call['unresolved']) if call['unresolved'] else ''}")
    print()


def main():
    if sys.argv[1] == "driver":
        driver(*sys.argv[2:7])
        return
    workdir = pathlib.Path(sys.argv[1])
    for deleter in ("bob", "alice"):
        run(workdir / f"{deleter}-deletes", f"{deleter} deletes", lambda root: scenario(root, deleter))
    run(workdir / "rooms", "each keeps their own room", rooms)


if __name__ == "__main__":
    main()
