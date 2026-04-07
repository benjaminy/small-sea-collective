#!/usr/bin/env python3
"""
Exact-snapshot tag-aware git history pruning experiment for Cod Sync.

This script builds deterministic local repos, creates blobless partial clones,
rehydrates a fixed recent boundary-to-HEAD window plus selected tagged exact
snapshots, severs the promisor remote, and records how storage and behavior
change across a grid of tag-density / tag-placement scenarios.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import tarfile
from dataclasses import asdict, dataclass, field


KEEP_COMMITS_DEFAULT = 20
DENSITIES = [0.0, 0.10, 0.25, 0.50, 1.0]
PLACEMENTS = ["recent-biased", "evenly-spaced", "old-biased", "binary-heavy-milestones"]


class GitError(RuntimeError):
    def __init__(self, repo_dir: pathlib.Path, args: list[str], result: subprocess.CompletedProcess[str]):
        self.repo_dir = repo_dir
        self.args = args
        self.result = result
        bits = [
            f"git command failed in {repo_dir}",
            f"args: {' '.join(args)}",
            f"exit: {result.returncode}",
        ]
        if result.stdout.strip():
            bits.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            bits.append(f"stderr:\n{result.stderr.strip()}")
        super().__init__("\n".join(bits))


@dataclass
class CommandOutcome:
    ok: bool
    returncode: int
    stdout: str
    stderr: str


@dataclass
class ScenarioResult:
    scenario_name: str
    density_label: str
    placement: str
    retained_tag_count: int
    retained_tag_count_outside_window: int
    selected_commits: list[str]
    selected_tags: dict[str, str]
    source_git_kib: int
    pruned_git_kib: int
    size_saved_kib: int
    savings_retained_vs_baseline_ratio: float | None
    unique_protected_blob_count: int
    unique_protected_blob_inflated_bytes: int
    compressed_snapshot_corpus_kib: int
    pruned_to_compressed_snapshot_ratio: float | None
    overlap_blob_count: int
    overlap_blob_inflated_bytes: int
    commit_hashes_match: bool
    branches_match: bool
    tags_match: bool
    kept_window_access_ok: bool
    retained_snapshot_access_ok: bool
    old_blob_missing_ok: bool
    old_blob_absence_proven: bool
    representative_failures: dict[str, CommandOutcome] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class RepoExperimentResult:
    repo_name: str
    keep_commits: int
    mainline_commit_count: int
    source_git_kib: int
    baseline_boundary: str
    scenarios: list[ScenarioResult]


def git(
    repo_dir: pathlib.Path,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return _git(repo_dir, *args, input_text=input_text, check=check, as_text=True)


def _git(
    repo_dir: pathlib.Path,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
    as_text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        input=input_text,
        capture_output=True,
        text=as_text,
    )
    if check and result.returncode != 0:
        raise GitError(repo_dir, list(args), result)
    return result


def run_cmd(args: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(args)}\n"
            f"exit: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_bytes(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def deterministic_bytes(rng: random.Random, size: int) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(size))


def configure_repo(repo_dir: pathlib.Path) -> None:
    git(repo_dir, "config", "user.name", "Codex Experiment")
    git(repo_dir, "config", "user.email", "codex@example.com")
    git(repo_dir, "config", "commit.gpgsign", "false")
    git(repo_dir, "config", "tag.gpgSign", "false")
    git(repo_dir, "config", "uploadpack.allowFilter", "true")
    git(repo_dir, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(repo_dir, "config", "core.autocrlf", "false")


def init_repo(repo_dir: pathlib.Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    git(repo_dir.parent, "init", "-q", "--initial-branch=main", repo_dir.name)
    configure_repo(repo_dir)


def commit_all(repo_dir: pathlib.Path, message: str) -> str:
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", message)
    return git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def get_commit_list(repo_dir: pathlib.Path, rev: str = "HEAD", first_parent: bool = False) -> list[str]:
    args = ["rev-list", "--reverse", rev]
    if first_parent:
        args.insert(1, "--first-parent")
    result = git(repo_dir, *args)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_branch_map(repo_dir: pathlib.Path) -> dict[str, str]:
    result = git(repo_dir, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    data: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if line.strip():
            name, sha = line.split()
            data[name] = sha
    return data


def get_tag_map(repo_dir: pathlib.Path) -> dict[str, str]:
    result = git(repo_dir, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/tags")
    data: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if line.strip():
            name, sha = line.split()
            data[name] = sha
    return data


def git_size_kib(repo_dir: pathlib.Path) -> int:
    total = 0
    for path in (repo_dir / ".git").rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total // 1024


def count_missing_objects(repo_dir: pathlib.Path) -> int:
    result = git(repo_dir, "rev-list", "--objects", "--missing=print", "--all")
    return sum(1 for line in result.stdout.splitlines() if line.startswith("?"))


def mirror_local_refs(source_repo: pathlib.Path, clone_dir: pathlib.Path) -> None:
    for branch, sha in get_branch_map(source_repo).items():
        git(clone_dir, "update-ref", f"refs/heads/{branch}", sha)
    for tag, sha in get_tag_map(source_repo).items():
        git(clone_dir, "update-ref", f"refs/tags/{tag}", sha)


def make_blobless_clone(source_repo: pathlib.Path, clone_dir: pathlib.Path) -> None:
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(["git", "clone", "--filter=blob:none", f"file://{source_repo}", str(clone_dir)], cwd=clone_dir.parent)
    git(clone_dir, "config", "commit.gpgsign", "false")
    git(clone_dir, "config", "tag.gpgSign", "false")
    mirror_local_refs(source_repo, clone_dir)


def compute_boundary(repo_dir: pathlib.Path, keep_commits: int) -> tuple[str, str | None, list[str], list[str], list[str]]:
    mainline_commits = get_commit_list(repo_dir, "main", first_parent=True)
    if not mainline_commits:
        raise RuntimeError(f"{repo_dir} has no commits on main")
    boundary_index = max(0, len(mainline_commits) - keep_commits)
    boundary = mainline_commits[boundary_index]
    boundary_parent = mainline_commits[boundary_index - 1] if boundary_index > 0 else None
    all_reachable = get_commit_list(repo_dir, "HEAD")
    if boundary_parent is None:
        window_commits = all_reachable
    else:
        window_set = set(get_commit_list(repo_dir, "HEAD"))
        old_set = set(get_commit_list(repo_dir, boundary_parent))
        window_commits = [sha for sha in all_reachable if sha in window_set and sha not in old_set]
    old_commits = [sha for sha in all_reachable if sha not in set(window_commits)]
    return boundary, boundary_parent, mainline_commits, window_commits, old_commits


def finalize_pruned_repo(repo_dir: pathlib.Path) -> list[str]:
    notes: list[str] = []
    cleanup_filter_dir = repo_dir / ".git" / "cleanup-filter"
    cleanup_filter_dir.mkdir(parents=True, exist_ok=True)
    for args in [
        ["remote", "remove", "origin"],
        ["repack", "-a", "-d", "--filter=blob:none", f"--filter-to={cleanup_filter_dir}"],
        ["prune", "--expire", "now"],
    ]:
        result = git(repo_dir, *args, check=False)
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout).strip()
            if args[:2] == ["remote", "remove"] and "No such remote" in stderr:
                continue
            notes.append(f"{' '.join(args)} failed with exit {result.returncode}: {stderr}")
    shutil.rmtree(cleanup_filter_dir, ignore_errors=True)
    return notes


def file_digest(repo_dir: pathlib.Path, revspec: str) -> str:
    result = _git(repo_dir, "show", revspec, as_text=False)
    return hashlib.sha256(result.stdout).hexdigest()


def ls_tree_entries(repo_dir: pathlib.Path, rev: str) -> list[dict[str, object]]:
    result = git(repo_dir, "ls-tree", "-r", "-l", rev)
    entries: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        mode, obj_type, sha, size = meta.split()
        size_value = 0 if size == "-" else int(size)
        entries.append(
            {
                "mode": mode,
                "type": obj_type,
                "sha": sha,
                "size": size_value,
                "path": path,
            }
        )
    return entries


def representative_paths(repo_dir: pathlib.Path, rev: str, include_largest_blob: bool) -> list[str]:
    entries = [entry for entry in ls_tree_entries(repo_dir, rev) if entry["type"] == "blob"]
    if not entries:
        return []
    entries.sort(key=lambda entry: entry["path"])
    paths = [entries[0]["path"], entries[-1]["path"]]
    if include_largest_blob:
        largest = max(entries, key=lambda entry: (entry["size"], entry["path"]))
        paths.append(largest["path"])
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def snapshot_blob_map(repo_dir: pathlib.Path, commits: list[str]) -> dict[str, int]:
    blobs: dict[str, int] = {}
    for commit in commits:
        for entry in ls_tree_entries(repo_dir, commit):
            if entry["type"] != "blob":
                continue
            blobs.setdefault(entry["sha"], int(entry["size"]))
    return blobs


def changed_blob_bytes_by_commit(repo_dir: pathlib.Path, mainline_commits: list[str]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for index, commit in enumerate(mainline_commits):
        parent = mainline_commits[index - 1] if index > 0 else None
        args = ["diff-tree", "--root", "-r", "--name-only", "--no-commit-id", commit]
        if parent is not None:
            args.append(parent)
            args.append(commit)
            args = ["diff-tree", "--root", "-r", "--name-only", "--no-commit-id", parent, commit]
        result = git(repo_dir, *args)
        changed_paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        total = 0
        for path in changed_paths:
            ls_tree = git(repo_dir, "ls-tree", "-l", commit, path, check=False)
            if ls_tree.returncode != 0 or not ls_tree.stdout.strip():
                continue
            meta, _path = ls_tree.stdout.strip().split("\t", 1)
            _mode, obj_type, _sha, size = meta.split()
            if obj_type == "blob" and size != "-":
                total += int(size)
        sizes[commit] = total
    return sizes


def export_commit_snapshot(repo_dir: pathlib.Path, commit: str, target_dir: pathlib.Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(repo_dir), "archive", commit],
        capture_output=True,
        check=True,
    )
    proc = subprocess.run(
        ["tar", "-xf", "-", "-C", str(target_dir)],
        input=archive.stdout,
        capture_output=True,
        check=True,
    )
    _ = proc


def compressed_snapshot_corpus_kib(
    repo_dir: pathlib.Path,
    snapshot_commits: list[str],
    corpus_root: pathlib.Path,
) -> int:
    if corpus_root.exists():
        shutil.rmtree(corpus_root)
    corpus_root.mkdir(parents=True, exist_ok=True)
    snapshots_dir = corpus_root / "snapshots"
    for index, commit in enumerate(snapshot_commits, start=1):
        export_commit_snapshot(repo_dir, commit, snapshots_dir / f"{index:03d}-{commit[:12]}")
    tar_path = corpus_root / "snapshots.tar.bz2"
    with tarfile.open(tar_path, "w:bz2") as tar:
        tar.add(snapshots_dir, arcname="snapshots")
    return tar_path.stat().st_size // 1024


def evenly_spaced_selection(commits: list[str], count: int) -> list[str]:
    if count <= 0:
        return []
    if count >= len(commits):
        return list(commits)
    if count == 1:
        return [commits[len(commits) // 2]]
    picks: list[str] = []
    for idx in range(count):
        pos = round(idx * (len(commits) - 1) / (count - 1))
        picks.append(commits[pos])
    deduped: list[str] = []
    seen: set[str] = set()
    for commit in picks:
        if commit not in seen:
            seen.add(commit)
            deduped.append(commit)
    if len(deduped) < count:
        for commit in commits:
            if commit not in seen:
                seen.add(commit)
                deduped.append(commit)
            if len(deduped) == count:
                break
    return deduped


def count_for_density(mainline_count: int, density: float) -> int:
    if density <= 0.0:
        return 0
    if density >= 1.0:
        return mainline_count
    return max(1, min(mainline_count, int(round(mainline_count * density))))


def select_retained_commits(
    mainline_commits: list[str],
    density: float,
    placement: str,
    keep_commits: int,
    changed_blob_bytes: dict[str, int],
) -> list[str]:
    count = count_for_density(len(mainline_commits), density)
    if count == 0:
        return []
    if count >= len(mainline_commits):
        return list(mainline_commits)
    if placement == "evenly-spaced":
        return evenly_spaced_selection(mainline_commits, count)
    if placement == "recent-biased":
        tail_width = min(len(mainline_commits), max(keep_commits, count * 3))
        return evenly_spaced_selection(mainline_commits[-tail_width:], count)
    if placement == "old-biased":
        head_width = min(len(mainline_commits), max(keep_commits, count * 3))
        return evenly_spaced_selection(mainline_commits[:head_width], count)
    if placement == "binary-heavy-milestones":
        ranked = sorted(
            mainline_commits,
            key=lambda commit: (changed_blob_bytes.get(commit, 0), commit),
            reverse=True,
        )
        return sorted(ranked[:count], key=lambda commit: mainline_commits.index(commit))
    raise ValueError(f"unknown placement: {placement}")


def scenario_definitions() -> list[tuple[str, float, str]]:
    items: list[tuple[str, float, str]] = [("baseline-0pct", 0.0, "none")]
    for density in DENSITIES:
        if density in {0.0, 1.0}:
            continue
        pct = int(round(density * 100))
        for placement in PLACEMENTS:
            items.append((f"{pct:02d}pct-{placement}", density, placement))
    items.append(("all-mainline-100pct", 1.0, "all-mainline"))
    return items


def add_scenario_tags(repo_dir: pathlib.Path, selected_commits: list[str]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for index, commit in enumerate(selected_commits, start=1):
        name = f"snapshot-{index:03d}"
        if index % 2 == 0:
            git(repo_dir, "tag", "-a", name, commit, "-m", f"snapshot tag {index:03d}")
        else:
            git(repo_dir, "tag", name, commit)
        tags[name] = commit
    return tags


def clone_repo(source_repo: pathlib.Path, clone_dir: pathlib.Path) -> pathlib.Path:
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(["git", "clone", "--quiet", str(source_repo), str(clone_dir)], cwd=clone_dir.parent)
    configure_repo(clone_dir)
    return clone_dir


def rehydrate_exact_snapshots(repo_dir: pathlib.Path, refs: list[str]) -> list[str]:
    notes: list[str] = []
    original_head = git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    try:
        for ref in refs:
            result = git(repo_dir, "checkout", "--detach", "--force", ref, check=False)
            if result.returncode != 0:
                notes.append(f"checkout failed for {ref}: {(result.stderr or result.stdout).strip()}")
    finally:
        git(repo_dir, "checkout", "--force", original_head, check=False)
    return notes


def verify_kept_window_access(source_repo: pathlib.Path, pruned_repo: pathlib.Path, window_commits: list[str], repo_name: str) -> bool:
    sample_commits = window_commits[-min(len(window_commits), 6):]
    include_largest = repo_name in {"repo_a_typical", "repo_c_large_files"}
    for commit in sample_commits:
        paths = representative_paths(source_repo, commit, include_largest)
        if git(pruned_repo, "checkout", "--detach", "--force", commit, check=False).returncode != 0:
            return False
        for path in paths:
            if _git(pruned_repo, "show", f"{commit}:{path}", check=False, as_text=False).returncode != 0:
                return False
            if file_digest(source_repo, f"{commit}:{path}") != file_digest(pruned_repo, f"{commit}:{path}"):
                return False
    git(pruned_repo, "checkout", "--force", "main", check=False)
    return True


def verify_retained_snapshot_access(
    source_repo: pathlib.Path,
    pruned_repo: pathlib.Path,
    selected_tags: dict[str, str],
    repo_name: str,
) -> bool:
    include_largest = repo_name in {"repo_a_typical", "repo_c_large_files"}
    for tag_name in selected_tags:
        if git(pruned_repo, "checkout", "--detach", "--force", tag_name, check=False).returncode != 0:
            return False
        for path in representative_paths(source_repo, tag_name, include_largest):
            if _git(pruned_repo, "show", f"{tag_name}:{path}", check=False, as_text=False).returncode != 0:
                return False
            if file_digest(source_repo, f"{tag_name}:{path}") != file_digest(pruned_repo, f"{tag_name}:{path}"):
                return False
    git(pruned_repo, "checkout", "--force", "main", check=False)
    return True


def check_old_blob_failures(
    source_repo: pathlib.Path,
    pruned_repo: pathlib.Path,
    old_commits: list[str],
) -> tuple[bool, dict[str, CommandOutcome]]:
    if not old_commits:
        return True, {}
    all_objects = git(pruned_repo, "rev-list", "--objects", "--missing=print", "--all").stdout.splitlines()
    source_objects = git(source_repo, "rev-list", "--objects", "--all").stdout.splitlines()
    missing_blob_sha = None
    missing_blob_path = None
    for line in all_objects:
        if not line.startswith("?"):
            continue
        sha = line[1:].split()[0]
        for source_entry in source_objects:
            if source_entry.startswith(sha + " "):
                missing_blob_sha = sha
                missing_blob_path = source_entry.split(" ", 1)[1]
                break
        if missing_blob_path:
            break
    old_commit = old_commits[-1]
    if missing_blob_sha and missing_blob_path:
        for candidate in reversed(old_commits):
            ls_tree = git(source_repo, "ls-tree", candidate, missing_blob_path, check=False)
            if ls_tree.returncode != 0 or not ls_tree.stdout.strip():
                continue
            parts = ls_tree.stdout.strip().split()
            if len(parts) >= 3 and parts[2] == missing_blob_sha:
                old_commit = candidate
                break
    path = missing_blob_path or "journal/main.txt"
    failures: dict[str, CommandOutcome] = {}
    commands = {
        "show": ["show", f"{old_commit}:{path}"],
        "diff": ["diff", f"{old_commit}..HEAD", "--", path],
        "log-p": ["log", "-p", "--max-count=1", old_commit, "--", path],
        "checkout": ["checkout", "--detach", "--force", old_commit],
    }
    all_failed = True
    for name, args in commands.items():
        result = git(pruned_repo, *args, check=False)
        outcome = CommandOutcome(
            ok=result.returncode == 0,
            returncode=result.returncode,
            stdout=result.stdout[-4000:],
            stderr=result.stderr[-4000:],
        )
        failures[name] = outcome
        if outcome.ok:
            all_failed = False
    git(pruned_repo, "checkout", "--force", "main", check=False)
    return all_failed, failures


def prove_old_blob_absence(source_repo: pathlib.Path, pruned_repo: pathlib.Path) -> bool:
    all_objects = git(pruned_repo, "rev-list", "--objects", "--missing=print", "--all").stdout.splitlines()
    for line in all_objects:
        if not line.startswith("?"):
            continue
        missing_blob_sha = line[1:].split()[0]
        source_has_blob = git(source_repo, "cat-file", "-e", missing_blob_sha, check=False).returncode == 0
        pruned_missing_blob = git(pruned_repo, "cat-file", "-e", missing_blob_sha, check=False).returncode != 0
        return source_has_blob and pruned_missing_blob
    return False


def seed_typical_app_repo(repo_dir: pathlib.Path, rng: random.Random, commit_count: int) -> None:
    write_text(repo_dir / "README.md", "# Typical app fixture\n")
    write_text(repo_dir / "journal" / "main.txt", "main-000\n")
    write_text(repo_dir / "notes" / "todo.txt", "todo-000\n")
    write_bytes(repo_dir / "assets" / "logo.bin", deterministic_bytes(rng, 4096))
    commit_all(repo_dir, "initial fixture")

    main_count = max(commit_count, 80)
    main_shas = [git(repo_dir, "rev-parse", "HEAD").stdout.strip()]
    for idx in range(1, 56):
        write_text(repo_dir / "journal" / "main.txt", f"main-{idx:03d}\n")
        if idx < 10:
            write_text(repo_dir / "notes" / "todo.txt", f"todo-{idx:03d}\n")
        elif idx == 10:
            write_text(repo_dir / "docs" / "renamed-from-notes.txt", "renamed file begins here\n")
            git(repo_dir, "rm", "-q", "-f", "notes/todo.txt")
        else:
            write_text(repo_dir / "docs" / "renamed-from-notes.txt", f"renamed-{idx:03d}\n")
        if idx in {12, 34, 52}:
            write_bytes(repo_dir / "assets" / "logo.bin", deterministic_bytes(random.Random(1000 + idx), 8192 + idx * 64))
        if idx in {18, 41}:
            write_bytes(repo_dir / "assets" / f"milestone-{idx:03d}.bin", deterministic_bytes(random.Random(2000 + idx), 24 * 1024))
        main_shas.append(commit_all(repo_dir, f"main commit {idx:03d}"))

    git(repo_dir, "checkout", "--force", "-B", "legacy-feature", main_shas[20])
    write_text(repo_dir / "journal" / "legacy.txt", "legacy branch state\n")
    commit_all(repo_dir, "legacy feature diverges outside kept window")

    git(repo_dir, "checkout", "--force", "main")
    for idx in range(56, 72):
        write_text(repo_dir / "journal" / "main.txt", f"main-{idx:03d}\n")
        write_text(repo_dir / "docs" / "renamed-from-notes.txt", f"renamed-{idx:03d}\n")
        if idx in {60, 68}:
            write_bytes(repo_dir / "assets" / "logo.bin", deterministic_bytes(random.Random(3000 + idx), 12 * 1024))
        main_shas.append(commit_all(repo_dir, f"main commit {idx:03d}"))

    git(repo_dir, "checkout", "--force", "-B", "recent-feature", "HEAD~4")
    for idx in range(72, 78):
        write_text(repo_dir / "feature" / "notes.txt", f"feature branch {idx:03d}\n")
        if idx in {74, 77}:
            write_bytes(repo_dir / "feature" / f"artifact-{idx:03d}.bin", deterministic_bytes(random.Random(4000 + idx), 16 * 1024))
        commit_all(repo_dir, f"recent feature {idx:03d}")

    git(repo_dir, "checkout", "--force", "main")
    for idx in range(78, main_count - 2):
        write_text(repo_dir / "journal" / "main.txt", f"main-{idx:03d}\n")
        write_text(repo_dir / "docs" / "renamed-from-notes.txt", f"renamed-{idx:03d}\n")
        if idx in {84, 88}:
            write_bytes(repo_dir / "assets" / "release.bin", deterministic_bytes(random.Random(5000 + idx), 32 * 1024))
        main_shas.append(commit_all(repo_dir, f"main commit {idx:03d}"))

    git(repo_dir, "merge", "--no-ff", "--no-edit", "recent-feature")
    main_shas.append(git(repo_dir, "rev-parse", "HEAD").stdout.strip())
    write_text(repo_dir / "journal" / "main.txt", f"main-{main_count - 1:03d}\n")
    write_text(repo_dir / "docs" / "renamed-from-notes.txt", f"renamed-{main_count - 1:03d}\n")
    commit_all(repo_dir, f"main commit {main_count - 1:03d}")


def seed_many_small_files_repo(repo_dir: pathlib.Path, rng: random.Random, commit_count: int) -> None:
    for idx in range(120):
        write_text(repo_dir / "small" / f"file-{idx:03d}.txt", f"seed {idx:03d}\n")
    write_text(repo_dir / "journal" / "main.txt", "small-000\n")
    commit_all(repo_dir, "initial small files fixture")
    for commit_idx in range(1, commit_count):
        touched = rng.sample(range(120), 30)
        for idx in touched:
            write_text(repo_dir / "small" / f"file-{idx:03d}.txt", f"commit {commit_idx:03d} file {idx:03d}\n")
        write_text(repo_dir / "journal" / "main.txt", f"small-{commit_idx:03d}\n")
        commit_all(repo_dir, f"small files commit {commit_idx:03d}")


def seed_large_files_repo(repo_dir: pathlib.Path, rng: random.Random, commit_count: int) -> None:
    for idx in range(4):
        write_bytes(repo_dir / "large" / f"blob-{idx}.bin", deterministic_bytes(rng, 96 * 1024))
    write_text(repo_dir / "journal" / "main.txt", "large-000\n")
    commit_all(repo_dir, "initial large files fixture")
    for commit_idx in range(1, commit_count):
        target = commit_idx % 4
        write_bytes(
            repo_dir / "large" / f"blob-{target}.bin",
            deterministic_bytes(random.Random(7000 + commit_idx), 96 * 1024),
        )
        if commit_idx % 9 == 0:
            write_bytes(
                repo_dir / "large" / "release.bin",
                deterministic_bytes(random.Random(9000 + commit_idx), 48 * 1024),
            )
        write_text(repo_dir / "journal" / "main.txt", f"large-{commit_idx:03d}\n")
        commit_all(repo_dir, f"large files commit {commit_idx:03d}")


def build_repo(workspace: pathlib.Path, repo_name: str, commit_count: int, seed: int) -> pathlib.Path:
    repo_dir = workspace / "sources" / repo_name
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    init_repo(repo_dir)
    rng = random.Random(seed)
    if repo_name == "repo_a_typical":
        seed_typical_app_repo(repo_dir, rng, commit_count)
    elif repo_name == "repo_b_small_files":
        seed_many_small_files_repo(repo_dir, rng, commit_count)
    elif repo_name == "repo_c_large_files":
        seed_large_files_repo(repo_dir, rng, commit_count)
    else:
        raise ValueError(f"unknown repo fixture {repo_name}")
    git(repo_dir, "checkout", "--force", "main")
    return repo_dir


def run_scenario(
    repo_name: str,
    base_source_repo: pathlib.Path,
    workspace: pathlib.Path,
    keep_commits: int,
    scenario_name: str,
    density: float,
    placement: str,
    mainline_commits: list[str],
    window_commits: list[str],
    old_commits: list[str],
    changed_blob_bytes: dict[str, int],
    baseline_blob_map: dict[str, int],
    baseline_saved_kib: int | None,
) -> ScenarioResult:
    selected_commits = select_retained_commits(mainline_commits, density, placement, keep_commits, changed_blob_bytes)
    scenario_source = clone_repo(base_source_repo, workspace / "scenario-sources" / repo_name / scenario_name)
    selected_tags = add_scenario_tags(scenario_source, selected_commits)

    pruned_repo = workspace / "pruned" / repo_name / scenario_name
    if pruned_repo.exists():
        shutil.rmtree(pruned_repo)
    make_blobless_clone(scenario_source, pruned_repo)

    rehydrate_refs = list(window_commits) + list(selected_tags.keys())
    notes = rehydrate_exact_snapshots(pruned_repo, rehydrate_refs)
    notes.extend(finalize_pruned_repo(pruned_repo))

    tag_blob_map = snapshot_blob_map(scenario_source, selected_commits)
    protected_blob_map = dict(baseline_blob_map)
    for sha, size in tag_blob_map.items():
        protected_blob_map.setdefault(sha, size)
    overlap_shas = set(baseline_blob_map) & set(tag_blob_map)
    overlap_bytes = sum(baseline_blob_map[sha] for sha in overlap_shas)
    retained_outside_window = [sha for sha in selected_commits if sha not in set(mainline_commits[-keep_commits:])]
    old_unretained = [sha for sha in old_commits if sha not in set(selected_commits)]
    old_blob_missing_ok, failures = check_old_blob_failures(scenario_source, pruned_repo, old_unretained)
    protected_snapshots = list(dict.fromkeys(window_commits + selected_commits))
    compressed_corpus_kib = compressed_snapshot_corpus_kib(
        scenario_source,
        protected_snapshots,
        workspace / "compressed-corpus" / repo_name / scenario_name,
    )

    pruned_git_kib = git_size_kib(pruned_repo)
    source_git_kib = git_size_kib(scenario_source)
    size_saved_kib = source_git_kib - pruned_git_kib
    ratio = None if baseline_saved_kib in {None, 0} else round(size_saved_kib / baseline_saved_kib, 4)
    compressed_ratio = None if compressed_corpus_kib == 0 else round(pruned_git_kib / compressed_corpus_kib, 4)

    return ScenarioResult(
        scenario_name=scenario_name,
        density_label="baseline" if density == 0.0 else ("100%" if density == 1.0 else f"{int(round(density * 100))}%"),
        placement=placement,
        retained_tag_count=len(selected_tags),
        retained_tag_count_outside_window=len(retained_outside_window),
        selected_commits=selected_commits,
        selected_tags=selected_tags,
        source_git_kib=source_git_kib,
        pruned_git_kib=pruned_git_kib,
        size_saved_kib=size_saved_kib,
        savings_retained_vs_baseline_ratio=ratio,
        unique_protected_blob_count=len(protected_blob_map),
        unique_protected_blob_inflated_bytes=sum(protected_blob_map.values()),
        compressed_snapshot_corpus_kib=compressed_corpus_kib,
        pruned_to_compressed_snapshot_ratio=compressed_ratio,
        overlap_blob_count=len(overlap_shas),
        overlap_blob_inflated_bytes=overlap_bytes,
        commit_hashes_match=get_commit_list(scenario_source, "HEAD") == get_commit_list(pruned_repo, "HEAD"),
        branches_match=get_branch_map(scenario_source) == get_branch_map(pruned_repo),
        tags_match=get_tag_map(scenario_source) == get_tag_map(pruned_repo),
        kept_window_access_ok=verify_kept_window_access(scenario_source, pruned_repo, window_commits, repo_name),
        retained_snapshot_access_ok=verify_retained_snapshot_access(scenario_source, pruned_repo, selected_tags, repo_name),
        old_blob_missing_ok=old_blob_missing_ok,
        old_blob_absence_proven=prove_old_blob_absence(scenario_source, pruned_repo),
        representative_failures=failures,
        notes=notes,
    )


def run_experiment(workspace: pathlib.Path, keep_commits: int, commit_count: int, repo_names: list[str]) -> dict[str, object]:
    all_repos = [
        ("repo_a_typical", 101),
        ("repo_b_small_files", 202),
        ("repo_c_large_files", 303),
    ]
    repos = [item for item in all_repos if item[0] in repo_names]
    workspace.mkdir(parents=True, exist_ok=True)
    results: list[RepoExperimentResult] = []

    for repo_name, seed in repos:
        source_repo = build_repo(workspace, repo_name, commit_count, seed)
        boundary, _boundary_parent, mainline_commits, window_commits, old_commits = compute_boundary(source_repo, keep_commits)
        baseline_blob_map = snapshot_blob_map(source_repo, window_commits)
        changed_blob_bytes = changed_blob_bytes_by_commit(source_repo, mainline_commits)
        scenarios: list[ScenarioResult] = []
        baseline_saved_kib: int | None = None
        for scenario_name, density, placement in scenario_definitions():
            scenario_result = run_scenario(
                repo_name=repo_name,
                base_source_repo=source_repo,
                workspace=workspace,
                keep_commits=keep_commits,
                scenario_name=scenario_name,
                density=density,
                placement=placement,
                mainline_commits=mainline_commits,
                window_commits=window_commits,
                old_commits=old_commits,
                changed_blob_bytes=changed_blob_bytes,
                baseline_blob_map=baseline_blob_map,
                baseline_saved_kib=baseline_saved_kib,
            )
            scenarios.append(scenario_result)
            if scenario_name == "baseline-0pct":
                baseline_saved_kib = scenario_result.size_saved_kib
        results.append(
            RepoExperimentResult(
                repo_name=repo_name,
                keep_commits=keep_commits,
                mainline_commit_count=len(mainline_commits),
                source_git_kib=git_size_kib(source_repo),
                baseline_boundary=boundary,
                scenarios=scenarios,
            )
        )

    return {
        "workspace": str(workspace),
        "keep_commits": keep_commits,
        "commit_count": commit_count,
        "scenario_definitions": [
            {"scenario_name": name, "density": density, "placement": placement}
            for name, density, placement in scenario_definitions()
        ],
        "summary_notes": [
            "Candidate retained tags are limited to first-parent mainline commits on main.",
            "The retained guarantee is exact snapshot readability only, not tagged-to-HEAD history usability.",
            "The baseline kept window is fixed at the most recent keep_commits first-parent commits on main.",
        ],
        "repo_results": results,
    }


def render_human_summary(results: dict[str, object]) -> str:
    lines = [
        "Exact-Snapshot Tag-Aware Git History Pruning Experiment",
        "=======================================================",
        f"workspace: {results['workspace']}",
        f"keep_commits: {results['keep_commits']}",
        f"commit_count: {results['commit_count']}",
        "",
    ]
    for note in results["summary_notes"]:
        lines.append(f"- {note}")
    lines.append("")
    for repo_result in results["repo_results"]:
        lines.append(f"{repo_result.repo_name}:")
        lines.append(f"  mainline_commit_count: {repo_result.mainline_commit_count}")
        lines.append(f"  baseline_boundary: {repo_result.baseline_boundary}")
        baseline = next(item for item in repo_result.scenarios if item.scenario_name == "baseline-0pct")
        lines.append(
            f"  baseline: pruned_git_kib={baseline.pruned_git_kib} size_saved_kib={baseline.size_saved_kib} "
            f"protected_blobs={baseline.unique_protected_blob_count}"
        )
        lines.append("  scenarios:")
        for scenario in repo_result.scenarios:
            ratio = "n/a" if scenario.savings_retained_vs_baseline_ratio is None else f"{scenario.savings_retained_vs_baseline_ratio:.3f}"
            lines.append(
                "    "
                f"{scenario.scenario_name}: tags={scenario.retained_tag_count} "
                f"outside_window={scenario.retained_tag_count_outside_window} "
                f"pruned_git_kib={scenario.pruned_git_kib} saved={scenario.size_saved_kib} "
                f"compressed_corpus_kib={scenario.compressed_snapshot_corpus_kib} "
                f"pruned_to_corpus={scenario.pruned_to_compressed_snapshot_ratio if scenario.pruned_to_compressed_snapshot_ratio is not None else 'n/a'} "
                f"saved_vs_baseline={ratio} protected_blobs={scenario.unique_protected_blob_count} "
                f"protected_inflated_bytes={scenario.unique_protected_blob_inflated_bytes} "
                f"overlap_blobs={scenario.overlap_blob_count} retained_ok={scenario.retained_snapshot_access_ok} "
                f"old_missing={scenario.old_blob_missing_ok}"
            )
        lines.append("")
    return "\n".join(lines)


def to_jsonable(obj: object) -> object:
    if isinstance(obj, dict):
        return {key: to_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(value) for value in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return to_jsonable(asdict(obj))
    return obj


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=pathlib.Path,
        help="Directory for generated repos and artifacts. Defaults to a temp directory.",
    )
    parser.add_argument(
        "--keep-commits",
        type=int,
        default=KEEP_COMMITS_DEFAULT,
        help="How many first-parent main commits to keep in the recent window.",
    )
    parser.add_argument(
        "--commit-count",
        type=int,
        default=96,
        help="Approximate mainline commit count for generated fixtures.",
    )
    parser.add_argument(
        "--repos",
        default="repo_a_typical,repo_b_small_files,repo_c_large_files",
        help="Comma-separated list of fixture repos to run.",
    )
    parser.add_argument(
        "--json-out",
        type=pathlib.Path,
        help="Optional path for machine-readable JSON results.",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Do not delete the auto-created temp workspace at the end.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.keep_commits < 1:
        print("--keep-commits must be at least 1", file=sys.stderr)
        return 2
    if args.commit_count < 80:
        print("--commit-count must be at least 80 so the 20-commit kept window is a meaningful minority of history", file=sys.stderr)
        return 2

    repo_names = [name.strip() for name in args.repos.split(",") if name.strip()]
    allowed_repos = {"repo_a_typical", "repo_b_small_files", "repo_c_large_files"}
    unknown_repos = [name for name in repo_names if name not in allowed_repos]
    if unknown_repos:
        print(f"unknown repos: {', '.join(unknown_repos)}", file=sys.stderr)
        return 2

    auto_workspace = args.workspace is None
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if auto_workspace:
        temp_dir = tempfile.TemporaryDirectory(prefix="git-history-pruning-tags-")
        workspace = pathlib.Path(temp_dir.name)
    else:
        workspace = args.workspace.resolve()

    try:
        results = run_experiment(
            workspace=workspace,
            keep_commits=args.keep_commits,
            commit_count=args.commit_count,
            repo_names=repo_names,
        )
        print(render_human_summary(results))
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(to_jsonable(results), indent=2, sort_keys=True),
                encoding="utf-8",
            )
    finally:
        if temp_dir is not None and not args.keep_workspace:
            temp_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
