#!/usr/bin/env python3
"""
Compare how SQLite configuration affects storage-level stability under git.

Different SQLite settings (journal mode, auto-vacuum, page size) change how
much of the database file is rewritten on each transaction. This matters when
the DB is tracked in git, because smaller binary diffs mean smaller bundles
and more efficient sync (e.g. via CornCob).

For each experimental condition, we:
  1. Create a subdirectory with a fresh git repo
  2. Create a SQLite DB with that condition's PRAGMA settings
  3. Initialize a simple TODO-app schema
  4. Iterate N rounds of random inserts, updates, and deletes, committing to
     git after each round
  5. Report: total repo size, number of git objects, and average diff size
"""

import argparse
import itertools
import os
import pathlib
import random
import sqlite3
import subprocess
import string
import sys


# ---------------------------------------------------------------------------
# Schema and random data helpers
# ---------------------------------------------------------------------------

SCHEMA = """\
CREATE TABLE IF NOT EXISTS project (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS todo (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    body        TEXT,
    done        INTEGER NOT NULL DEFAULT 0,
    priority    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tag (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS todo_tag (
    todo_id INTEGER NOT NULL REFERENCES todo(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tag(id)  ON DELETE CASCADE,
    PRIMARY KEY (todo_id, tag_id)
);
"""

SAMPLE_PROJECTS = [
    "Groceries", "Work", "Hobby", "Fitness", "Reading",
    "Garden", "Recipes", "Travel", "Budget", "Music",
]

SAMPLE_TAGS = [
    "urgent", "low-priority", "blocked", "quick-win", "research",
    "errand", "phone-call", "email", "weekend", "someday",
]

WORDS = [
    "fix", "buy", "call", "check", "review", "update", "clean", "send",
    "read", "write", "plan", "schedule", "organize", "prepare", "finish",
    "start", "learn", "practice", "build", "install", "replace", "order",
    "return", "cancel", "renew", "submit", "file", "sort", "pack", "ship",
]

NOUNS = [
    "report", "ticket", "invoice", "package", "letter", "form", "permit",
    "appointment", "prescription", "subscription", "contract", "proposal",
    "budget", "spreadsheet", "presentation", "document", "reservation",
    "password", "backup", "filter", "battery", "lightbulb", "shelf",
]


def random_title():
    return f"{random.choice(WORDS)} {random.choice(NOUNS)}"


def random_body():
    if random.random() < 0.3:
        return None
    length = random.randint(1, 4)
    return ". ".join(
        " ".join(random.choices(WORDS + NOUNS, k=random.randint(3, 8)))
        for _ in range(length)
    )


def seed_db(conn, rng, n_seed_todos=500):
    """Insert the initial set of projects, tags, and a batch of seed todos."""
    conn.executemany(
        "INSERT OR IGNORE INTO project (name) VALUES (?)",
        [(p,) for p in SAMPLE_PROJECTS],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO tag (name) VALUES (?)",
        [(t,) for t in SAMPLE_TAGS],
    )

    cur = conn.cursor()
    project_ids = [r[0] for r in cur.execute("SELECT id FROM project").fetchall()]
    tag_ids = [r[0] for r in cur.execute("SELECT id FROM tag").fetchall()]

    for _ in range(n_seed_todos):
        proj = rng.choice(project_ids)
        title = random_title()
        body = random_body()
        priority = rng.randint(0, 3)
        cur.execute(
            "INSERT INTO todo (project_id, title, body, priority) VALUES (?, ?, ?, ?)",
            (proj, title, body, priority),
        )
        new_id = cur.lastrowid
        if tag_ids and rng.random() < 0.6:
            n_tags = rng.randint(1, 3)
            chosen = rng.sample(tag_ids, min(n_tags, len(tag_ids)))
            cur.executemany(
                "INSERT OR IGNORE INTO todo_tag (todo_id, tag_id) VALUES (?, ?)",
                [(new_id, t) for t in chosen],
            )

    conn.commit()


def do_round(conn, rng):
    """One round of random mutations: inserts, updates, deletes.

    Each round adds 50-150 new todos, updates ~10% of existing ones,
    and deletes a fraction of completed items.  This is enough to push
    the DB across many pages and exercise B-tree splits/rebalances.
    """
    cur = conn.cursor()

    project_ids = [r[0] for r in cur.execute("SELECT id FROM project").fetchall()]
    tag_ids = [r[0] for r in cur.execute("SELECT id FROM tag").fetchall()]
    todo_ids = [r[0] for r in cur.execute("SELECT id FROM todo").fetchall()]

    # --- Inserts: 50-150 new todos ---
    n_inserts = rng.randint(50, 150)
    for _ in range(n_inserts):
        proj = rng.choice(project_ids)
        title = random_title()
        body = random_body()
        priority = rng.randint(0, 3)
        cur.execute(
            "INSERT INTO todo (project_id, title, body, priority) VALUES (?, ?, ?, ?)",
            (proj, title, body, priority),
        )
        new_id = cur.lastrowid
        if tag_ids and rng.random() < 0.6:
            n_tags = rng.randint(1, 3)
            chosen = rng.sample(tag_ids, min(n_tags, len(tag_ids)))
            cur.executemany(
                "INSERT OR IGNORE INTO todo_tag (todo_id, tag_id) VALUES (?, ?)",
                [(new_id, t) for t in chosen],
            )

    # Refresh todo list after inserts
    todo_ids = [r[0] for r in cur.execute("SELECT id FROM todo").fetchall()]

    # --- Updates: ~10% of todos ---
    if todo_ids:
        n_updates = rng.randint(1, max(1, len(todo_ids) // 10))
        for tid in rng.sample(todo_ids, min(n_updates, len(todo_ids))):
            action = rng.random()
            if action < 0.3:
                cur.execute(
                    "UPDATE todo SET done = 1, updated_at = datetime('now') WHERE id = ?",
                    (tid,),
                )
            elif action < 0.6:
                new_pri = rng.randint(0, 3)
                cur.execute(
                    "UPDATE todo SET priority = ?, updated_at = datetime('now') WHERE id = ?",
                    (new_pri, tid),
                )
            else:
                new_body = random_body()
                cur.execute(
                    "UPDATE todo SET body = ?, updated_at = datetime('now') WHERE id = ?",
                    (new_body, tid),
                )

    # --- Deletes: remove some completed todos ---
    if todo_ids and rng.random() < 0.5:
        done_ids = [
            r[0] for r in cur.execute("SELECT id FROM todo WHERE done = 1").fetchall()
        ]
        if done_ids:
            n_deletes = rng.randint(1, max(1, len(done_ids) // 3))
            to_delete = rng.sample(done_ids, min(n_deletes, len(done_ids)))
            cur.executemany("DELETE FROM todo WHERE id = ?", [(d,) for d in to_delete])

    conn.commit()


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(repo_dir, *args):
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
    return result


def git_init(repo_dir):
    git(repo_dir, "init", "-b", "main")
    git(repo_dir, "config", "user.email", "experiment@example.com")
    git(repo_dir, "config", "user.name", "Experiment")


def git_commit(repo_dir, message):
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-m", message)


def git_repo_stats(repo_dir):
    """Gather stats about the git repo."""
    # Total size of .git directory
    git_dir = pathlib.Path(repo_dir) / ".git"
    total_size = sum(f.stat().st_size for f in git_dir.rglob("*") if f.is_file())

    # Number of objects (rough: count loose + packed)
    result = git(repo_dir, "rev-list", "--objects", "--all")
    n_objects = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0

    # Pack the repo first so we get a fair compressed size
    git(repo_dir, "gc", "--aggressive", "--prune=now")
    packed_size = sum(f.stat().st_size for f in git_dir.rglob("*") if f.is_file())

    # Average diff size (in bytes) between consecutive commits
    result = git(repo_dir, "log", "--oneline")
    commits = result.stdout.strip().splitlines()
    n_commits = len(commits)

    diff_sizes = []
    if n_commits > 1:
        result = git(repo_dir, "log", "--format=%H")
        shas = result.stdout.strip().splitlines()
        for i in range(len(shas) - 1):
            diff_result = git(repo_dir, "diff", "--stat", "--binary", shas[i + 1], shas[i])
            # Use the raw diff size as a proxy
            raw_diff = git(repo_dir, "diff", "--binary", shas[i + 1], shas[i])
            diff_sizes.append(len(raw_diff.stdout.encode("utf-8", errors="replace")))

    avg_diff = sum(diff_sizes) / len(diff_sizes) if diff_sizes else 0

    return {
        "commits": n_commits,
        "objects": n_objects,
        "git_size_raw": total_size,
        "git_size_packed": packed_size,
        "avg_diff_bytes": avg_diff,
    }


# ---------------------------------------------------------------------------
# Experimental conditions
# ---------------------------------------------------------------------------

CONDITIONS = {
    "journal_mode": ["delete", "wal", "truncate"],
    "auto_vacuum": ["none", "full", "incremental"],
    "page_size": [1024, 4096, 16384],
}


def apply_pragmas(conn, journal_mode, auto_vacuum, page_size):
    """Apply PRAGMA settings. Must be done before any tables are created."""
    # page_size must be set before anything else, and needs a vacuum to take effect
    # on an empty db this is basically free
    conn.execute(f"PRAGMA page_size = {page_size}")

    auto_vacuum_map = {"none": 0, "full": 1, "incremental": 2}
    conn.execute(f"PRAGMA auto_vacuum = {auto_vacuum_map[auto_vacuum]}")

    # Vacuum to apply page_size and auto_vacuum on the fresh DB
    conn.execute("VACUUM")

    conn.execute(f"PRAGMA journal_mode = {journal_mode}")


def condition_name(journal_mode, auto_vacuum, page_size):
    return f"jrnl-{journal_mode}_vacuum-{auto_vacuum}_page-{page_size}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_experiment(scratch_dir, n_rounds, seed):
    scratch = pathlib.Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)

    combos = list(itertools.product(
        CONDITIONS["journal_mode"],
        CONDITIONS["auto_vacuum"],
        CONDITIONS["page_size"],
    ))

    print(f"Running {len(combos)} conditions x {n_rounds} rounds (seed={seed})")
    print()

    results = []

    for journal_mode, auto_vacuum, page_size in combos:
        name = condition_name(journal_mode, auto_vacuum, page_size)
        cond_dir = scratch / name
        cond_dir.mkdir(parents=True, exist_ok=True)

        print(f"  {name} ...", end="", flush=True)

        # Init git repo
        git_init(cond_dir)

        # Create and configure DB
        db_path = cond_dir / "todo.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        apply_pragmas(conn, journal_mode, auto_vacuum, page_size)
        conn.executescript(SCHEMA)
        seed_rng = random.Random(seed)
        seed_db(conn, seed_rng)
        conn.close()

        # If WAL mode, checkpoint and remove WAL before committing
        if journal_mode == "wal":
            c = sqlite3.connect(str(db_path))
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()

        git_commit(cond_dir, "initial schema and seed data")

        # Run mutation rounds
        rng = random.Random(seed)
        for i in range(n_rounds):
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA foreign_keys = ON")
            # Re-apply journal mode each open (WAL is persistent, others need it)
            conn.execute(f"PRAGMA journal_mode = {journal_mode}")
            do_round(conn, rng)
            conn.close()

            # Checkpoint WAL before git commit so the DB is a single file
            if journal_mode == "wal":
                c = sqlite3.connect(str(db_path))
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.close()

            git_commit(cond_dir, f"round {i + 1}")

        # Gather stats
        db_size = db_path.stat().st_size
        stats = git_repo_stats(cond_dir)
        stats["condition"] = name
        stats["journal_mode"] = journal_mode
        stats["auto_vacuum"] = auto_vacuum
        stats["page_size"] = page_size
        stats["db_size"] = db_size
        results.append(stats)
        print(
            f" db={db_size:>10,}  "
            f"packed={stats['git_size_packed']:>10,}  "
            f"avg_diff={stats['avg_diff_bytes']:>10,.0f}  "
            f"commits={stats['commits']}"
        )

    # Summary table
    print()
    print("=" * 120)
    print(
        f"{'Condition':<45} "
        f"{'DB Size':>10} "
        f"{'Packed':>10} "
        f"{'Avg Diff':>10} "
        f"{'Objects':>8} "
        f"{'Commits':>8}"
    )
    print("-" * 120)

    results.sort(key=lambda r: r["git_size_packed"])
    for r in results:
        print(
            f"{r['condition']:<45} "
            f"{r['db_size']:>10,} "
            f"{r['git_size_packed']:>10,} "
            f"{r['avg_diff_bytes']:>10,.0f} "
            f"{r['objects']:>8,} "
            f"{r['commits']:>8}"
        )

    print("-" * 120)
    best = results[0]
    worst = results[-1]
    print(f"Best packed size:  {best['condition']} ({best['git_size_packed']:,} bytes)")
    print(f"Worst packed size: {worst['condition']} ({worst['git_size_packed']:,} bytes)")
    print(f"Ratio: {worst['git_size_packed'] / best['git_size_packed']:.1f}x")


def main():
    parser = argparse.ArgumentParser(
        description="Compare SQLite storage stability under git across different PRAGMA settings."
    )
    parser.add_argument(
        "scratch_dir",
        help="Directory to create experiment subdirectories in",
    )
    parser.add_argument(
        "-n", "--rounds",
        type=int,
        default=30,
        help="Number of mutation rounds per condition (default: 30)",
    )
    parser.add_argument(
        "-s", "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()
    run_experiment(args.scratch_dir, args.rounds, args.seed)


if __name__ == "__main__":
    main()
