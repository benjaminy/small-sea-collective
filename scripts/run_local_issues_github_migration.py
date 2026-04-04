#!/usr/bin/env python3
"""End-to-end local-issues -> GitHub Issues migration runner.

Run this in your own terminal after `gh auth login` succeeds locally.

It will:
1. create/update the GitHub labels
2. import open issues from `Issues/`
3. import closed issues from `Issues/Done/` as closed `legacy` issues
4. write a GitHub issue mapping file under `Archive/local-issues/`
5. prepend local files with `Migrated to GitHub #X`
6. update `Issues/README.md` to point to GitHub as canonical
7. move the local issue files into `Archive/local-issues/`

Usage:
    python3 scripts/run_local_issues_github_migration.py --dry-run
    python3 scripts/run_local_issues_github_migration.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from migrate_local_issues_to_github import MAPPING_PATH, ROOT


ISSUES_DIR = ROOT / "Issues"
DONE_DIR = ISSUES_DIR / "Done"
ARCHIVE_DIR = ROOT / "Archive" / "local-issues"
README_PATH = ISSUES_DIR / "README.md"
IMPORT_SCRIPT = ROOT / "scripts" / "migrate_local_issues_to_github.py"


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without changing GitHub or local files")
    parser.add_argument("--repo", help="GitHub repo slug, e.g. owner/name")
    return parser.parse_args()


def load_mapping() -> dict:
    if not MAPPING_PATH.exists():
        raise RuntimeError(
            f"Expected mapping file at {MAPPING_PATH}. "
            "The import step may have failed before writing it."
        )
    return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))


def mapping_by_source(mapping: dict) -> dict[str, dict]:
    return {entry["source_path"]: entry for entry in mapping["issues"]}


def prepend_migration_note(path: Path, github_number: int, *, dry_run: bool) -> None:
    original = path.read_text(encoding="utf-8")
    note = f"> Migrated to GitHub issue #{github_number}.\n\n"
    if original.startswith(note):
        return
    if dry_run:
        print(f"DRY RUN: would prepend migration note to {path.relative_to(ROOT)}")
        return
    path.write_text(note + original, encoding="utf-8")


def update_issues_readme(*, dry_run: bool) -> None:
    new_text = """# Issues

GitHub Issues is now the canonical issue tracker for Small Sea Collective.

## Canonical Tracker

- Active and historical issues now live on GitHub Issues for this repository.
- The old repo-local markdown issues were migrated and then archived.

## Archived Local Issues

- Archived copies of the old local issue markdown files live in `Archive/local-issues/`.
- `Archive/local-issues/github-issue-map.json` records the mapping from legacy local files to GitHub issue numbers.
"""
    if dry_run:
        print(f"DRY RUN: would rewrite {README_PATH.relative_to(ROOT)}")
        return
    README_PATH.write_text(new_text, encoding="utf-8")


def archive_issue_files(mapping: dict, *, dry_run: bool) -> None:
    archive_open = ARCHIVE_DIR / "open"
    archive_closed = ARCHIVE_DIR / "done"
    by_source = mapping_by_source(mapping)

    for path in sorted(ISSUES_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        entry = by_source[str(path.relative_to(ROOT))]
        prepend_migration_note(path, entry["github_issue_number"], dry_run=dry_run)
        destination = archive_open / path.name
        if dry_run:
            print(f"DRY RUN: would move {path.relative_to(ROOT)} -> {destination.relative_to(ROOT)}")
        else:
            archive_open.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))

    for path in sorted(DONE_DIR.glob("*.md")):
        entry = by_source[str(path.relative_to(ROOT))]
        prepend_migration_note(path, entry["github_issue_number"], dry_run=dry_run)
        destination = archive_closed / path.name
        if dry_run:
            print(f"DRY RUN: would move {path.relative_to(ROOT)} -> {destination.relative_to(ROOT)}")
        else:
            archive_closed.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))


def validate(mapping: dict) -> None:
    entries = mapping["issues"]
    open_count = sum(1 for e in entries if e["state"] == "open")
    closed_count = sum(1 for e in entries if e["state"] == "closed")
    if open_count != 20:
        raise RuntimeError(f"Expected 20 open migrated issues, found {open_count}")
    if closed_count != 11:
        raise RuntimeError(f"Expected 11 closed migrated issues, found {closed_count}")


def run_import(repo: str | None, *, dry_run: bool) -> None:
    cmd = ["python3", str(IMPORT_SCRIPT)]
    if repo:
        cmd.extend(["--repo", repo])
    if dry_run:
        cmd.append("--dry-run")
    result = run(cmd)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")


def main() -> int:
    args = parse_args()

    run_import(args.repo, dry_run=args.dry_run)
    if args.dry_run:
        print("DRY RUN: skipping mapping validation and local archive steps")
        return 0

    mapping = load_mapping()
    validate(mapping)
    update_issues_readme(dry_run=False)
    archive_issue_files(mapping, dry_run=False)
    print(f"Migration complete. Mapping file: {MAPPING_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
