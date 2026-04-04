#!/usr/bin/env python3
"""Migrate repo-local issue markdown files into GitHub Issues.

This script follows the branch plan for the `issues-to-github` migration:

- open issues in `Issues/` become open GitHub Issues
- closed issues in `Issues/Done/` become closed GitHub Issues with the `legacy`
  label and a standard closure comment
- labels are created if missing
- a mapping file is written incrementally so the run can be resumed

Usage:
    python3 scripts/migrate_local_issues_to_github.py --dry-run
    python3 scripts/migrate_local_issues_to_github.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OPEN_DIR = ROOT / "Issues"
CLOSED_DIR = ROOT / "Issues" / "Done"
MAPPING_PATH = ROOT / "Archive" / "local-issues" / "github-issue-map.json"

TYPE_LABELS = {
    "task": "type:task",
    "bug": "type:bug",
    "idea": "type:idea",
    "question": "type:question",
    "spec": "type:spec",
    "design": "type:design",
}

PRIORITY_LABELS = {
    "high": "priority:high",
    "medium": "priority:medium",
    "low": "priority:low",
}

LABEL_SPECS = {
    "type:task": {"color": "1f6feb", "description": "Concrete implementation work"},
    "type:bug": {"color": "d73a4a", "description": "Incorrect behavior or missing handling"},
    "type:idea": {"color": "a371f7", "description": "Potential improvement or proposal"},
    "type:question": {"color": "fbca04", "description": "Open question or decision"},
    "type:spec": {"color": "0e8a16", "description": "Specification or documentation work"},
    "type:design": {"color": "5319e7", "description": "Architecture or design work"},
    "priority:high": {"color": "b60205", "description": "High priority"},
    "priority:medium": {"color": "d93f0b", "description": "Medium priority"},
    "priority:low": {"color": "fbca04", "description": "Low priority"},
    "legacy": {"color": "6e7781", "description": "Migrated closed issue from repo-local tracker"},
}

STANDARD_CLOSING_COMMENT = (
    "Closing as part of the migration from repo-local issues. "
    "This issue was already resolved before GitHub Issues became the canonical tracker."
)


@dataclass
class IssueRecord:
    issue_id: str
    title: str
    issue_type: str | None
    priority: str | None
    status: str | None
    source_path: Path
    repo_relative_path: str
    is_closed: bool
    body_markdown: str

    @property
    def labels(self) -> list[str]:
        labels: list[str] = []
        if self.issue_type:
            labels.append(TYPE_LABELS[self.issue_type])
        if self.priority:
            labels.append(PRIORITY_LABELS[self.priority])
        if self.is_closed:
            labels.append("legacy")
        return labels

    @property
    def github_body(self) -> str:
        lines = [
            "_Migrated from the repo-local issue tracker._",
            "",
            f"- Legacy local ID: `{self.issue_id}`",
            f"- Source file: `{self.repo_relative_path}`",
        ]
        if self.is_closed:
            lines.append("- Original state: resolved in repo-local tracker.")
        lines.extend(["", self.body_markdown.strip(), ""])
        return "\n".join(lines)

    @property
    def mapping_key(self) -> str:
        return self.repo_relative_path


def run(cmd: list[str], *, check: bool = True, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def repo_slug() -> str:
    result = run(["git", "remote", "get-url", "origin"])
    remote = result.stdout.strip()
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$", remote)
    if not match:
        raise RuntimeError(f"Could not determine GitHub repo from remote: {remote}")
    return f"{match.group('owner')}/{match.group('repo')}"


def ensure_auth() -> None:
    result = run(["gh", "auth", "status"], check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "GitHub CLI is not authenticated. Run `gh auth login -h github.com` "
            "and then rerun this script."
        )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_issue(path: Path, *, is_closed: bool) -> IssueRecord:
    text = read_text(path)
    meta, body = parse_frontmatter(text)

    if not meta:
        meta, body = parse_legacy_header(text)

    issue_id = meta.get("id")
    title = meta.get("title")
    issue_type = meta.get("type")
    priority = meta.get("priority") or None
    status = meta.get("status") or None

    if issue_id is None or title is None:
        raise RuntimeError(f"Missing required metadata in {path}")
    if issue_type is not None and issue_type not in TYPE_LABELS:
        raise RuntimeError(f"Unsupported issue type {issue_type!r} in {path}")
    if priority is not None and priority not in PRIORITY_LABELS:
        raise RuntimeError(f"Unsupported priority {priority!r} in {path}")

    return IssueRecord(
        issue_id=issue_id,
        title=title,
        issue_type=issue_type,
        priority=priority,
        status=status,
        source_path=path,
        repo_relative_path=str(path.relative_to(ROOT)),
        is_closed=is_closed,
        body_markdown=body.strip(),
    )


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    meta: dict[str, str] = {}
    end_index = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = idx
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    if end_index is None:
        return {}, text
    body = "\n".join(lines[end_index + 1 :])
    return meta, body


def parse_legacy_header(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines:
        return {}, text

    header_match = re.match(r"^#\s+(?P<id>\d{4})\s+·\s+(?P<type>\w+)\s+·\s+(?P<title>.+)$", lines[0].strip())
    if not header_match:
        return {}, text

    meta = {
        "id": header_match.group("id"),
        "type": header_match.group("type"),
        "title": header_match.group("title"),
    }

    body_start = 1
    if len(lines) > 1 and lines[1].strip().startswith("**Status:**"):
        status_text = lines[1].split(":", 1)[1].strip().strip("*").lower()
        meta["status"] = "closed" if status_text in {"done", "closed"} else status_text
        body_start = 2

    body = "\n".join(lines[body_start:]).lstrip()
    return meta, body


def gather_issues() -> list[IssueRecord]:
    issues: list[IssueRecord] = []
    for path in sorted(OPEN_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        issues.append(parse_issue(path, is_closed=False))
    for path in sorted(CLOSED_DIR.glob("*.md")):
        issues.append(parse_issue(path, is_closed=True))
    return issues


def load_mapping() -> dict[str, Any]:
    if not MAPPING_PATH.exists():
        return {"repo": repo_slug(), "issues": []}
    return json.loads(read_text(MAPPING_PATH))


def save_mapping(mapping: dict[str, Any]) -> None:
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_PATH.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def existing_issue_keys(mapping: dict[str, Any]) -> set[str]:
    return {item["source_path"] for item in mapping["issues"]}


def ensure_labels(repo: str, *, dry_run: bool) -> None:
    for name, spec in LABEL_SPECS.items():
        cmd = [
            "gh",
            "label",
            "create",
            name,
            "--repo",
            repo,
            "--color",
            spec["color"],
            "--description",
            spec["description"],
            "--force",
        ]
        if dry_run:
            print("DRY RUN:", " ".join(cmd))
        else:
            run(cmd)


def create_issue(repo: str, issue: IssueRecord, *, dry_run: bool) -> tuple[int | None, str | None]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(issue.github_body)
        body_path = Path(handle.name)

    cmd = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        issue.title,
        "--body-file",
        str(body_path),
    ]
    for label in issue.labels:
        cmd.extend(["--label", label])

    try:
        if dry_run:
            print(f"\nDRY RUN CREATE {issue.issue_id} {issue.title}")
            print("Labels:", ", ".join(issue.labels) if issue.labels else "(none)")
            print(issue.github_body)
            return None, None

        result = run(cmd)
    finally:
        body_path.unlink(missing_ok=True)

    url = result.stdout.strip().splitlines()[-1]
    match = re.search(r"/issues/(?P<number>\d+)$", url)
    if not match:
        raise RuntimeError(f"Could not parse issue URL from gh output: {url}")
    return int(match.group("number")), url


def comment_and_close_issue(repo: str, number: int, *, dry_run: bool) -> None:
    comment_cmd = [
        "gh",
        "issue",
        "comment",
        str(number),
        "--repo",
        repo,
        "--body",
        STANDARD_CLOSING_COMMENT,
    ]
    close_cmd = [
        "gh",
        "issue",
        "close",
        str(number),
        "--repo",
        repo,
    ]
    if dry_run:
        print("DRY RUN:", " ".join(comment_cmd))
        print("DRY RUN:", " ".join(close_cmd))
        return
    run(comment_cmd)
    run(close_cmd)


def migrate(repo: str, issues: list[IssueRecord], *, dry_run: bool) -> None:
    mapping = load_mapping()
    seen_keys = existing_issue_keys(mapping)

    ordered = sorted(issues, key=sort_key)
    for issue in ordered:
        if issue.mapping_key in seen_keys:
            print(f"Skipping {issue.repo_relative_path}; already present in mapping file.")
            continue

        number, url = create_issue(repo, issue, dry_run=dry_run)
        if issue.is_closed:
            if number is not None:
                comment_and_close_issue(repo, number, dry_run=dry_run)
            elif dry_run:
                print("DRY RUN: would add standard closing comment and close issue")

        if dry_run:
            continue

        entry = {
            "legacy_local_id": issue.issue_id,
            "source_path": issue.repo_relative_path,
            "github_issue_number": number,
            "github_issue_url": url,
            "state": "closed" if issue.is_closed else "open",
            "title": issue.title,
            "labels": issue.labels,
        }
        mapping["issues"].append(entry)
        save_mapping(mapping)
        seen_keys.add(issue.mapping_key)
        print(f"Migrated {issue.issue_id} -> #{number}")


def sort_key(issue: IssueRecord) -> tuple[int, int, str]:
    # Open issues first, then closed legacy issues. Within each bucket, follow
    # the plan's priority order, then legacy local ID order.
    state_rank = 1 if issue.is_closed else 0
    priority_rank = {"high": 0, "medium": 1, "low": 2, None: 3}[issue.priority]
    return (state_rank, priority_rank, issue.issue_id)


def print_inventory(issues: list[IssueRecord]) -> None:
    print(f"Open issues: {sum(not issue.is_closed for issue in issues)}")
    print(f"Closed issues: {sum(issue.is_closed for issue in issues)}")
    print()
    for issue in sorted(issues, key=sort_key):
        state = "closed" if issue.is_closed else "open"
        print(
            f"{issue.issue_id} [{state}] {issue.title} "
            f"(type={issue.issue_type or '-'}, priority={issue.priority or '-'})"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub repo slug, e.g. owner/name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making GitHub changes")
    parser.add_argument(
        "--inventory-only",
        action="store_true",
        help="Print the parsed issue inventory and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    issues = gather_issues()

    if args.inventory_only:
        print_inventory(issues)
        return 0

    repo = args.repo or repo_slug()

    if not args.dry_run:
        ensure_auth()

    ensure_labels(repo, dry_run=args.dry_run)
    migrate(repo, issues, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
