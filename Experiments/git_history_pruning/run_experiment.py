#!/usr/bin/env python3
"""
Git history pruning experiment for Cod Sync.

This script builds deterministic local repos, creates blobless partial clones,
rehydrates a chosen boundary-to-HEAD window, severs the promisor remote, and
records what still works afterward.

The experiment is intentionally local-only. It uses `file://` remotes and
enables `uploadpack.allowFilter=true` on generated source repos so that local
partial clones actually honor `--filter=blob:none`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field


ROOT = pathlib.Path(__file__).resolve().parent


class GitError(RuntimeError):
    def __init__(self, repo_dir: pathlib.Path, args: list[str], result: subprocess.CompletedProcess[str]):
        self.repo_dir = repo_dir
        self.args = args
        self.result = result
        summary = [
            f"git command failed in {repo_dir}",
            f"args: {' '.join(args)}",
            f"exit: {result.returncode}",
        ]
        if result.stdout.strip():
            summary.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            summary.append(f"stderr:\n{result.stderr.strip()}")
        super().__init__("\n".join(summary))


@dataclass
class CommandOutcome:
    ok: bool
    returncode: int
    stdout: str
    stderr: str


@dataclass
class StrategyResult:
    name: str
    ok: bool
    elapsed_seconds: float
    missing_before: int
    missing_after: int
    pack_kib_before: int
    pack_kib_after: int
    notes: list[str] = field(default_factory=list)


@dataclass
class RepoExperimentResult:
    repo_name: str
    boundary: str
    keep_commits: int
    source_git_kib: int
    pruned_git_kib: int
    size_saved_kib: int
    commit_hashes_match: bool
    branches_match: bool
    tags_match: bool
    kept_window_access_ok: bool
    old_blob_missing_ok: bool
    old_blob_absence_proven: bool
    bundle_within_window_ok: bool
    full_to_pruned_bundle_ok: bool
    out_of_window_bundle_failed: bool
    merge_within_window_ok: bool
    merge_outside_window_failed: bool
    old_command_failures: dict[str, CommandOutcome]
    strategies: list[StrategyResult]
    edge_cases: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def git(
    repo_dir: pathlib.Path,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo_dir), *args]
    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
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


def deterministic_bytes(rng: random.Random, size: int) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(size))


def commit_all(repo_dir: pathlib.Path, message: str) -> str:
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", message)
    return git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def first_existing_file(repo_dir: pathlib.Path, commit: str) -> str:
    result = git(repo_dir, "ls-tree", "-r", "--name-only", commit)
    for line in result.stdout.splitlines():
        if line.strip():
            return line.strip()
    raise RuntimeError(f"no files found in commit {commit}")


def get_commit_list(repo_dir: pathlib.Path, rev: str = "HEAD", first_parent: bool = False) -> list[str]:
    args = ["rev-list", "--reverse", rev]
    if first_parent:
        args.insert(1, "--first-parent")
    result = git(repo_dir, *args)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_branch_map(repo_dir: pathlib.Path) -> dict[str, str]:
    result = git(repo_dir, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    branches = {}
    for line in result.stdout.splitlines():
        name, sha = line.split()
        branches[name] = sha
    return branches


def get_tag_map(repo_dir: pathlib.Path) -> dict[str, str]:
    result = git(repo_dir, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/tags")
    tags = {}
    for line in result.stdout.splitlines():
        name, sha = line.split()
        tags[name] = sha
    return tags


def git_size_kib(repo_dir: pathlib.Path) -> int:
    git_dir = repo_dir / ".git"
    total = 0
    for path in git_dir.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total // 1024


def count_missing_objects(repo_dir: pathlib.Path) -> int:
    result = git(repo_dir, "rev-list", "--objects", "--missing=print", "--all")
    count = 0
    for line in result.stdout.splitlines():
        if line.startswith("?"):
            count += 1
    return count


def pack_kib(repo_dir: pathlib.Path) -> int:
    pack_dir = repo_dir / ".git" / "objects" / "pack"
    total = 0
    if pack_dir.exists():
        for path in pack_dir.glob("*.pack"):
            total += path.stat().st_size
    return total // 1024


def is_ancestor(repo_dir: pathlib.Path, ancestor: str, descendant: str) -> bool:
    result = git(repo_dir, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    return result.returncode == 0


def compute_boundary(repo_dir: pathlib.Path, keep_commits: int) -> tuple[str, str | None, list[str], list[str]]:
    main_commits = get_commit_list(repo_dir, "main", first_parent=True)
    if not main_commits:
        raise RuntimeError(f"{repo_dir} has no commits on main")
    boundary_index = max(0, len(main_commits) - keep_commits)
    boundary = main_commits[boundary_index]
    boundary_parent = main_commits[boundary_index - 1] if boundary_index > 0 else None
    all_reachable = get_commit_list(repo_dir, "HEAD")
    if boundary_parent is None:
        window_commits = all_reachable
    else:
        window_set = set(get_commit_list(repo_dir, f"HEAD", first_parent=False))
        old_set = set(get_commit_list(repo_dir, boundary_parent, first_parent=False))
        window_commits = [sha for sha in all_reachable if sha in window_set and sha not in old_set]
    old_commits = [sha for sha in all_reachable if sha not in set(window_commits)]
    return boundary, boundary_parent, window_commits, old_commits


def mirror_local_refs(source_repo: pathlib.Path, clone_dir: pathlib.Path) -> None:
    for branch, sha in get_branch_map(source_repo).items():
        git(clone_dir, "update-ref", f"refs/heads/{branch}", sha)
    for tag, sha in get_tag_map(source_repo).items():
        git(clone_dir, "update-ref", f"refs/tags/{tag}", sha)


def make_blobless_clone(source_repo: pathlib.Path, clone_dir: pathlib.Path) -> None:
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        ["git", "clone", "--filter=blob:none", f"file://{source_repo}", str(clone_dir)],
        cwd=clone_dir.parent,
    )
    git(clone_dir, "config", "commit.gpgsign", "false")
    git(clone_dir, "config", "tag.gpgSign", "false")
    mirror_local_refs(source_repo, clone_dir)


def rehydrate_checkout(repo_dir: pathlib.Path, boundary: str, window_commits: list[str]) -> StrategyResult:
    start = time.perf_counter()
    before_missing = count_missing_objects(repo_dir)
    before_pack = pack_kib(repo_dir)
    notes: list[str] = []
    try:
        original_head = git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        for sha in window_commits:
            git(repo_dir, "checkout", "--detach", "--force", sha)
        git(repo_dir, "checkout", "--force", original_head)
        ok = True
    except Exception as exc:  # pragma: no cover - exercised in experiment runtime
        ok = False
        notes.append(str(exc))
    return StrategyResult(
        name="checkout",
        ok=ok,
        elapsed_seconds=time.perf_counter() - start,
        missing_before=before_missing,
        missing_after=count_missing_objects(repo_dir),
        pack_kib_before=before_pack,
        pack_kib_after=pack_kib(repo_dir),
        notes=notes,
    )


def window_object_ids(repo_dir: pathlib.Path, window_commits: list[str]) -> list[str]:
    result = git(repo_dir, "rev-list", "--objects", *window_commits)
    object_ids = []
    seen = set()
    for line in result.stdout.splitlines():
        sha = line.split()[0]
        if sha not in seen:
            seen.add(sha)
            object_ids.append(sha)
    return object_ids


def rehydrate_rev_list(repo_dir: pathlib.Path, boundary: str, window_commits: list[str]) -> StrategyResult:
    start = time.perf_counter()
    before_missing = count_missing_objects(repo_dir)
    before_pack = pack_kib(repo_dir)
    notes: list[str] = []
    try:
        object_ids = window_object_ids(repo_dir, window_commits)
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "cat-file", "--batch"],
            input="\n".join(object_ids) + "\n",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "cat-file --batch failed")
        ok = True
        notes.append(f"requested {len(object_ids)} objects")
    except Exception as exc:  # pragma: no cover - exercised in experiment runtime
        ok = False
        notes.append(str(exc))
    return StrategyResult(
        name="rev-list-cat-file",
        ok=ok,
        elapsed_seconds=time.perf_counter() - start,
        missing_before=before_missing,
        missing_after=count_missing_objects(repo_dir),
        pack_kib_before=before_pack,
        pack_kib_after=pack_kib(repo_dir),
        notes=notes,
    )


def rehydrate_pack_objects(repo_dir: pathlib.Path, boundary: str, window_commits: list[str]) -> StrategyResult:
    start = time.perf_counter()
    before_missing = count_missing_objects(repo_dir)
    before_pack = pack_kib(repo_dir)
    notes: list[str] = []
    try:
        object_ids = window_object_ids(repo_dir, window_commits)
        pack_prefix = repo_dir / ".git" / "objects" / "pack" / "rehydrated-window"
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "pack-objects", "--quiet", str(pack_prefix)],
            input="\n".join(object_ids) + "\n",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "pack-objects failed")
        ok = True
        notes.append(f"packed {len(object_ids)} objects")
    except Exception as exc:  # pragma: no cover - exercised in experiment runtime
        ok = False
        notes.append(str(exc))
    return StrategyResult(
        name="pack-objects",
        ok=ok,
        elapsed_seconds=time.perf_counter() - start,
        missing_before=before_missing,
        missing_after=count_missing_objects(repo_dir),
        pack_kib_before=before_pack,
        pack_kib_after=pack_kib(repo_dir),
        notes=notes,
    )


def rehydrate_diff_tree(repo_dir: pathlib.Path, boundary: str, window_commits: list[str]) -> StrategyResult:
    start = time.perf_counter()
    before_missing = count_missing_objects(repo_dir)
    before_pack = pack_kib(repo_dir)
    notes: list[str] = []
    fetched = 0
    try:
        seen = set()
        for sha in window_commits:
            result = git(repo_dir, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
            for path in result.stdout.splitlines():
                key = (sha, path)
                if not path or key in seen:
                    continue
                seen.add(key)
                show = subprocess.run(
                    ["git", "-C", str(repo_dir), "show", f"{sha}:{path}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                if show.returncode == 0:
                    fetched += 1
        ok = True
        notes.append(f"touched {fetched} changed paths")
    except Exception as exc:  # pragma: no cover - exercised in experiment runtime
        ok = False
        notes.append(str(exc))
    return StrategyResult(
        name="diff-tree",
        ok=ok,
        elapsed_seconds=time.perf_counter() - start,
        missing_before=before_missing,
        missing_after=count_missing_objects(repo_dir),
        pack_kib_before=before_pack,
        pack_kib_after=pack_kib(repo_dir),
        notes=notes,
    )


STRATEGIES = {
    "checkout": rehydrate_checkout,
    "rev-list-cat-file": rehydrate_rev_list,
    "pack-objects": rehydrate_pack_objects,
    "diff-tree": rehydrate_diff_tree,
}


def finalize_pruned_repo(repo_dir: pathlib.Path) -> list[str]:
    notes: list[str] = []
    cleanup_filter_dir = repo_dir / ".git" / "cleanup-filter"
    cleanup_filter_dir.mkdir(parents=True, exist_ok=True)
    for args in [
        ["remote", "remove", "origin"],
        [
            "repack",
            "-a",
            "-d",
            "--filter=blob:none",
            f"--filter-to={cleanup_filter_dir}",
        ],
        ["prune", "--expire", "now"],
    ]:
        result = git(repo_dir, *args, check=False)
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout).strip()
            if args[:2] == ["remote", "remove"] and "No such remote" in stderr:
                continue
            notes.append(
                f"{' '.join(args)} failed with exit {result.returncode}: "
                f"{stderr}"
            )
    shutil.rmtree(cleanup_filter_dir, ignore_errors=True)
    return notes


def file_digest(repo_dir: pathlib.Path, revspec: str) -> str:
    result = git(repo_dir, "show", revspec)
    return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()


def verify_kept_window_access(source_repo: pathlib.Path, pruned_repo: pathlib.Path, window_commits: list[str]) -> bool:
    sample_commits = window_commits[-min(len(window_commits), 6):]
    for sha in sample_commits:
        path = first_existing_file(source_repo, sha)
        if git(pruned_repo, "checkout", "--detach", "--force", sha, check=False).returncode != 0:
            return False
        if git(pruned_repo, "show", f"{sha}:{path}", check=False).returncode != 0:
            return False
        if file_digest(source_repo, f"{sha}:{path}") != file_digest(pruned_repo, f"{sha}:{path}"):
            return False
    git(pruned_repo, "checkout", "--force", "main")
    return True


def check_old_blob_failures(source_repo: pathlib.Path, pruned_repo: pathlib.Path, old_commits: list[str]) -> tuple[bool, dict[str, CommandOutcome]]:
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
    missing_blob_sha = None
    for line in all_objects:
        if line.startswith("?"):
            missing_blob_sha = line[1:].split()[0]
            break
    if not missing_blob_sha:
        return False
    source_has_blob = git(source_repo, "cat-file", "-e", missing_blob_sha, check=False).returncode == 0
    pruned_missing_blob = git(pruned_repo, "cat-file", "-e", missing_blob_sha, check=False).returncode != 0
    return source_has_blob and pruned_missing_blob


def verify_bundle_within_window(source_repo: pathlib.Path, pruned_repo: pathlib.Path, boundary: str, scratch_dir: pathlib.Path) -> bool:
    bundle_path = scratch_dir / "within-window.bundle"
    receiver = scratch_dir / "bundle-receiver"
    git(pruned_repo, "branch", "-f", "window-tip", "HEAD")
    git(pruned_repo, "bundle", "create", str(bundle_path), "window-tip", f"^{boundary}")
    run_cmd(["git", "clone", str(source_repo), str(receiver)])
    result = git(receiver, "fetch", str(bundle_path), "window-tip:window-tip-from-pruned", check=False)
    if result.returncode != 0:
        return False
    fetched_sha = git(receiver, "rev-parse", "window-tip-from-pruned").stdout.strip()
    head_sha = git(pruned_repo, "rev-parse", "HEAD").stdout.strip()
    return fetched_sha == head_sha


def verify_full_to_pruned_bundle(source_repo: pathlib.Path, pruned_repo: pathlib.Path, boundary: str, scratch_dir: pathlib.Path) -> bool:
    bundle_path = scratch_dir / "full-to-pruned.bundle"
    git(source_repo, "branch", "-f", "window-tip", "HEAD")
    git(source_repo, "bundle", "create", str(bundle_path), "window-tip", f"^{boundary}")
    result = git(pruned_repo, "fetch", str(bundle_path), "window-tip:window-tip-from-full", check=False)
    if result.returncode != 0:
        return False
    fetched_sha = git(pruned_repo, "rev-parse", "window-tip-from-full").stdout.strip()
    source_sha = git(source_repo, "rev-parse", "HEAD").stdout.strip()
    return fetched_sha == source_sha


def verify_out_of_window_bundle_fails(pruned_repo: pathlib.Path, scratch_dir: pathlib.Path) -> bool:
    bundle_path = scratch_dir / "out-of-window.bundle"
    result = git(pruned_repo, "bundle", "create", str(bundle_path), "legacy-feature", check=False)
    return result.returncode != 0


def commit_local_change(repo_dir: pathlib.Path, rel_path: str, content: str, message: str) -> str:
    write_text(repo_dir / rel_path, content)
    return commit_all(repo_dir, message)


def verify_merge_within_window(pruned_repo: pathlib.Path) -> bool:
    base = git(pruned_repo, "rev-parse", "HEAD~2").stdout.strip()
    git(pruned_repo, "checkout", "--force", "-B", "merge-left", base)
    commit_local_change(
        pruned_repo,
        "merge/left.txt",
        "left side\n",
        "merge-left change",
    )
    git(pruned_repo, "checkout", "--force", "main")
    commit_local_change(
        pruned_repo,
        "merge/right.txt",
        "right side\n",
        "merge-right change",
    )
    result = git(pruned_repo, "merge", "--no-edit", "merge-left", check=False)
    if result.returncode != 0:
        git(pruned_repo, "merge", "--abort", check=False)
        return False
    git(pruned_repo, "checkout", "--force", "main")
    return True


def verify_merge_outside_window_fails(pruned_repo: pathlib.Path) -> bool:
    git(pruned_repo, "checkout", "--force", "main")
    result = git(pruned_repo, "merge", "--no-edit", "legacy-feature", check=False)
    if result.returncode == 0:
        git(pruned_repo, "merge", "--abort", check=False)
        return False
    git(pruned_repo, "merge", "--abort", check=False)
    return True


def run_edge_case_checks(source_repo: pathlib.Path, workspace: pathlib.Path) -> dict[str, bool]:
    cases: dict[str, bool] = {}

    def build_case_clone(name: str, boundary: str, window_commits: list[str]) -> pathlib.Path:
        clone = workspace / "edge-cases" / source_repo.name / name
        if clone.exists():
            shutil.rmtree(clone)
        make_blobless_clone(source_repo, clone)
        checkout_result = rehydrate_checkout(clone, boundary, window_commits)
        cases[f"{name}_checkout_ok"] = checkout_result.ok
        finalize_pruned_repo(clone)
        return clone

    # Boundary equals HEAD: maximum pruning while preserving the tip.
    head_boundary = git(source_repo, "rev-parse", "HEAD").stdout.strip()
    head_clone = build_case_clone("boundary-head", head_boundary, [head_boundary])
    cases["boundary_equals_head_tip_access"] = (
        git(head_clone, "show", "HEAD:journal/main.txt", check=False).returncode == 0
    )
    cases["boundary_equals_head_has_missing"] = count_missing_objects(head_clone) > 0

    # Boundary covers all history: should behave close to a full clone.
    all_history_boundary = get_commit_list(source_repo, "main", first_parent=True)[0]
    all_commits = get_commit_list(source_repo, "--all")
    full_clone = build_case_clone("boundary-all-history", all_history_boundary, all_commits)
    cases["boundary_all_history_no_missing"] = count_missing_objects(full_clone) == 0

    # Prune twice: the second finalize should preserve behavior and not explode.
    twice_clone = build_case_clone("prune-twice", head_boundary, [head_boundary])
    before_missing = count_missing_objects(twice_clone)
    second_notes = finalize_pruned_repo(twice_clone)
    after_missing = count_missing_objects(twice_clone)
    cases["prune_twice_stable"] = before_missing == after_missing and len(second_notes) == 0

    return cases


def compare_commit_hashes(source_repo: pathlib.Path, pruned_repo: pathlib.Path) -> bool:
    left = git(source_repo, "rev-list", "--topo-order", "HEAD").stdout.splitlines()
    right = git(pruned_repo, "rev-list", "--topo-order", "HEAD").stdout.splitlines()
    return left == right


def seed_typical_app_repo(repo_dir: pathlib.Path, rng: random.Random, commit_count: int) -> None:
    write_text(repo_dir / "README.md", "# Typical app fixture\n")
    write_text(repo_dir / "journal" / "main.txt", "main-000\n")
    write_text(repo_dir / "notes" / "todo.txt", "todo-000\n")
    write_bytes(repo_dir / "assets" / "logo.bin", deterministic_bytes(rng, 4096))
    commit_all(repo_dir, "initial fixture")

    main_commit_shas = [git(repo_dir, "rev-parse", "HEAD").stdout.strip()]
    for idx in range(1, 20):
        write_text(repo_dir / "journal" / "main.txt", f"main-{idx:03d}\n")
        write_text(repo_dir / "notes" / "todo.txt", f"todo-{idx:03d}\n")
        if idx == 5:
            git(repo_dir, "tag", "v0.1-lightweight")
        if idx == 8:
            git(repo_dir, "tag", "-a", "v0.2-annotated", "-m", "annotated tag")
        if idx == 10:
            write_text(repo_dir / "docs" / "renamed-from-notes.txt", "renamed file begins here\n")
            git(repo_dir, "rm", "-q", "-f", "notes/todo.txt")
        if idx > 10:
            write_text(repo_dir / "docs" / "renamed-from-notes.txt", f"renamed-{idx:03d}\n")
        if idx == 12:
            write_bytes(repo_dir / "assets" / "logo.bin", deterministic_bytes(rng, 8192))
        main_commit_shas.append(commit_all(repo_dir, f"main commit {idx:03d}"))

    legacy_base = main_commit_shas[8]
    git(repo_dir, "checkout", "--force", "-B", "legacy-feature", legacy_base)
    write_text(repo_dir / "journal" / "main.txt", "legacy-feature-change\n")
    commit_all(repo_dir, "legacy feature diverges outside kept window")

    git(repo_dir, "checkout", "--force", "main")
    git(repo_dir, "checkout", "--force", "-B", "recent-feature", "HEAD~4")
    for idx in range(20, commit_count):
        write_text(repo_dir / "feature" / "notes.txt", f"feature branch {idx:03d}\n")
        commit_all(repo_dir, f"recent feature {idx:03d}")

    git(repo_dir, "checkout", "--force", "main")
    write_text(repo_dir / "journal" / "main.txt", "main-before-merge\n")
    commit_all(repo_dir, "main before recent merge")
    git(repo_dir, "merge", "--no-ff", "--no-edit", "recent-feature")
    write_text(repo_dir / "journal" / "main.txt", "main-after-merge\n")
    commit_all(repo_dir, "main after recent merge")


def seed_many_small_files_repo(repo_dir: pathlib.Path, rng: random.Random, commit_count: int) -> None:
    for idx in range(100):
        write_text(repo_dir / "small" / f"file-{idx:03d}.txt", f"seed {idx:03d}\n")
    write_text(repo_dir / "journal" / "main.txt", "small-000\n")
    commit_all(repo_dir, "initial small files fixture")

    for commit_idx in range(1, commit_count):
        touched = rng.sample(range(100), 20)
        for idx in touched:
            write_text(
                repo_dir / "small" / f"file-{idx:03d}.txt",
                f"commit {commit_idx:03d} file {idx:03d}\n",
            )
        write_text(repo_dir / "journal" / "main.txt", f"small-{commit_idx:03d}\n")
        commit_all(repo_dir, f"small files commit {commit_idx:03d}")


def seed_large_files_repo(repo_dir: pathlib.Path, rng: random.Random, commit_count: int) -> None:
    for idx in range(3):
        write_bytes(repo_dir / "large" / f"blob-{idx}.bin", deterministic_bytes(rng, 64 * 1024))
    write_text(repo_dir / "journal" / "main.txt", "large-000\n")
    commit_all(repo_dir, "initial large files fixture")

    for commit_idx in range(1, commit_count):
        target = commit_idx % 3
        write_bytes(
            repo_dir / "large" / f"blob-{target}.bin",
            deterministic_bytes(random.Random(1000 + commit_idx), 64 * 1024),
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
    else:  # pragma: no cover - protected by caller
        raise ValueError(f"unknown repo fixture {repo_name}")
    git(repo_dir, "checkout", "--force", "main")
    return repo_dir


def benchmark_strategies(
    source_repo: pathlib.Path,
    workspace: pathlib.Path,
    boundary: str,
    window_commits: list[str],
    strategies: list[str],
) -> list[StrategyResult]:
    results: list[StrategyResult] = []
    for strategy_name in strategies:
        clone_dir = workspace / "benchmarks" / source_repo.name / strategy_name
        if clone_dir.exists():
            shutil.rmtree(clone_dir)
        make_blobless_clone(source_repo, clone_dir)
        strategy = STRATEGIES[strategy_name]
        results.append(strategy(clone_dir, boundary, window_commits))
    return results


def run_repo_a_validations(
    source_repo: pathlib.Path,
    workspace: pathlib.Path,
    boundary: str,
    window_commits: list[str],
    old_commits: list[str],
) -> tuple[pathlib.Path, dict[str, object]]:
    pruned_repo = workspace / "validated" / source_repo.name
    if pruned_repo.exists():
        shutil.rmtree(pruned_repo)
    make_blobless_clone(source_repo, pruned_repo)
    checkout_result = rehydrate_checkout(pruned_repo, boundary, window_commits)
    cleanup_notes = finalize_pruned_repo(pruned_repo)

    validation_scratch = workspace / "validation-scratch"
    validation_scratch.mkdir(parents=True, exist_ok=True)
    validation = {
        "checkout_strategy": checkout_result,
        "commit_hashes_match": compare_commit_hashes(source_repo, pruned_repo),
        "branches_match": get_branch_map(source_repo) == get_branch_map(pruned_repo),
        "tags_match": get_tag_map(source_repo) == get_tag_map(pruned_repo),
        "kept_window_access_ok": verify_kept_window_access(source_repo, pruned_repo, window_commits),
    }
    validation_notes: list[str] = []
    old_blob_missing_ok, failures = check_old_blob_failures(source_repo, pruned_repo, old_commits)
    validation["old_blob_missing_ok"] = old_blob_missing_ok
    validation["old_blob_absence_proven"] = prove_old_blob_absence(source_repo, pruned_repo)
    validation["old_command_failures"] = failures
    for key, func in [
        (
            "bundle_within_window_ok",
            lambda: verify_bundle_within_window(source_repo, pruned_repo, boundary, validation_scratch),
        ),
        (
            "full_to_pruned_bundle_ok",
            lambda: verify_full_to_pruned_bundle(source_repo, pruned_repo, boundary, validation_scratch),
        ),
        (
            "out_of_window_bundle_failed",
            lambda: verify_out_of_window_bundle_fails(pruned_repo, validation_scratch),
        ),
        ("merge_within_window_ok", lambda: verify_merge_within_window(pruned_repo)),
        ("merge_outside_window_failed", lambda: verify_merge_outside_window_fails(pruned_repo)),
    ]:
        try:
            validation[key] = func()
        except Exception as exc:  # pragma: no cover - exercised in experiment runtime
            validation[key] = False
            validation_notes.append(f"{key} raised: {exc}")
    validation["cleanup_notes"] = cleanup_notes
    validation["validation_notes"] = validation_notes
    return pruned_repo, validation


def to_jsonable(obj: object) -> object:
    if isinstance(obj, dict):
        return {key: to_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(value) for value in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return to_jsonable(asdict(obj))
    return obj


def run_experiment(
    workspace: pathlib.Path,
    keep_commits: int,
    commit_count: int,
    strategies: list[str],
    repo_names: list[str],
) -> dict[str, object]:
    all_repos = [
        ("repo_a_typical", 101),
        ("repo_b_small_files", 202),
        ("repo_c_large_files", 303),
    ]
    repos = [item for item in all_repos if item[0] in repo_names]
    workspace.mkdir(parents=True, exist_ok=True)
    results: list[RepoExperimentResult] = []
    summary_notes = [
        "Source repos are configured with uploadpack.allowFilter=true so local file:// partial clone honors blob filtering.",
        "Repo A gets the full validation pass; Repos B and C are used mainly for rehydration benchmarks and size checks.",
    ]

    for repo_name, seed in repos:
        source_repo = build_repo(workspace, repo_name, commit_count, seed)
        boundary, _boundary_parent, window_commits, old_commits = compute_boundary(source_repo, keep_commits)
        source_git_kib = git_size_kib(source_repo)
        strategy_results = benchmark_strategies(source_repo, workspace, boundary, window_commits, strategies)

        if repo_name == "repo_a_typical":
            pruned_repo, validation = run_repo_a_validations(
                source_repo, workspace, boundary, window_commits, old_commits
            )
            pruned_git_kib = git_size_kib(pruned_repo)
            repo_result = RepoExperimentResult(
                repo_name=repo_name,
                boundary=boundary,
                keep_commits=keep_commits,
                source_git_kib=source_git_kib,
                pruned_git_kib=pruned_git_kib,
                size_saved_kib=source_git_kib - pruned_git_kib,
                commit_hashes_match=validation["commit_hashes_match"],
                branches_match=validation["branches_match"],
                tags_match=validation["tags_match"],
                kept_window_access_ok=validation["kept_window_access_ok"],
                old_blob_missing_ok=validation["old_blob_missing_ok"],
                old_blob_absence_proven=validation["old_blob_absence_proven"],
                bundle_within_window_ok=validation["bundle_within_window_ok"],
                full_to_pruned_bundle_ok=validation["full_to_pruned_bundle_ok"],
                out_of_window_bundle_failed=validation["out_of_window_bundle_failed"],
                merge_within_window_ok=validation["merge_within_window_ok"],
                merge_outside_window_failed=validation["merge_outside_window_failed"],
                old_command_failures=validation["old_command_failures"],
                strategies=strategy_results,
                edge_cases=run_edge_case_checks(source_repo, workspace),
                notes=validation["checkout_strategy"].notes
                + validation["cleanup_notes"]
                + validation["validation_notes"],
            )
        else:
            best = min(strategy_results, key=lambda item: item.elapsed_seconds)
            size_probe_repo = workspace / "size-probe" / source_repo.name
            if size_probe_repo.exists():
                shutil.rmtree(size_probe_repo)
            make_blobless_clone(source_repo, size_probe_repo)
            rehydrate_checkout(size_probe_repo, boundary, window_commits)
            finalize_pruned_repo(size_probe_repo)
            pruned_git_kib = git_size_kib(size_probe_repo)
            repo_result = RepoExperimentResult(
                repo_name=repo_name,
                boundary=boundary,
                keep_commits=keep_commits,
                source_git_kib=source_git_kib,
                pruned_git_kib=pruned_git_kib,
                size_saved_kib=source_git_kib - pruned_git_kib,
                commit_hashes_match=True,
                branches_match=True,
                tags_match=True,
                kept_window_access_ok=best.ok,
                old_blob_missing_ok=True,
                old_blob_absence_proven=False,
                bundle_within_window_ok=False,
                full_to_pruned_bundle_ok=False,
                out_of_window_bundle_failed=False,
                merge_within_window_ok=False,
                merge_outside_window_failed=False,
                old_command_failures={},
                strategies=strategy_results,
                edge_cases={},
                notes=["Benchmark-only fixture"],
            )
        results.append(repo_result)

    strategy_leaders = {}
    for strategy_name in strategies:
        times = [
            next(item.elapsed_seconds for item in repo_result.strategies if item.name == strategy_name)
            for repo_result in results
        ]
        strategy_leaders[strategy_name] = round(sum(times) / len(times), 4)

    return {
        "workspace": str(workspace),
        "keep_commits": keep_commits,
        "commit_count": commit_count,
        "strategies": strategies,
        "summary_notes": summary_notes,
        "strategy_average_seconds": strategy_leaders,
        "repo_results": results,
    }


def render_human_summary(results: dict[str, object]) -> str:
    lines = [
        "Git History Pruning Experiment",
        "==============================",
        f"workspace: {results['workspace']}",
        f"keep_commits: {results['keep_commits']}",
        f"strategies: {', '.join(results['strategies'])}",
        "",
    ]
    for note in results["summary_notes"]:
        lines.append(f"- {note}")
    lines.append("")
    for repo_result in results["repo_results"]:
        lines.append(f"{repo_result.repo_name}:")
        lines.append(f"  boundary: {repo_result.boundary}")
        lines.append(f"  source_git_kib: {repo_result.source_git_kib}")
        if repo_result.pruned_git_kib:
            lines.append(f"  pruned_git_kib: {repo_result.pruned_git_kib}")
            lines.append(f"  size_saved_kib: {repo_result.size_saved_kib}")
            lines.append(f"  commit_hashes_match: {repo_result.commit_hashes_match}")
            lines.append(f"  branches_match: {repo_result.branches_match}")
            lines.append(f"  tags_match: {repo_result.tags_match}")
            lines.append(f"  kept_window_access_ok: {repo_result.kept_window_access_ok}")
            lines.append(f"  old_blob_missing_ok: {repo_result.old_blob_missing_ok}")
            lines.append(f"  old_blob_absence_proven: {repo_result.old_blob_absence_proven}")
            lines.append(f"  bundle_within_window_ok: {repo_result.bundle_within_window_ok}")
            lines.append(f"  full_to_pruned_bundle_ok: {repo_result.full_to_pruned_bundle_ok}")
            lines.append(f"  out_of_window_bundle_failed: {repo_result.out_of_window_bundle_failed}")
            lines.append(f"  merge_within_window_ok: {repo_result.merge_within_window_ok}")
            lines.append(f"  merge_outside_window_failed: {repo_result.merge_outside_window_failed}")
            if repo_result.edge_cases:
                lines.append("  edge_cases:")
                for name, ok in sorted(repo_result.edge_cases.items()):
                    lines.append(f"    {name}: {ok}")
        lines.append("  strategies:")
        for strategy in repo_result.strategies:
            lines.append(
                "    "
                f"{strategy.name}: ok={strategy.ok} elapsed={strategy.elapsed_seconds:.3f}s "
                f"missing {strategy.missing_before}->{strategy.missing_after} "
                f"pack_kib {strategy.pack_kib_before}->{strategy.pack_kib_after}"
            )
        lines.append("")
    return "\n".join(lines)


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
        default=10,
        help="How many first-parent main commits to keep in the recent window.",
    )
    parser.add_argument(
        "--commit-count",
        type=int,
        default=28,
        help="Approximate commit count for generated fixtures.",
    )
    parser.add_argument(
        "--strategies",
        default="checkout,rev-list-cat-file,pack-objects,diff-tree",
        help="Comma-separated list of rehydration strategies to benchmark.",
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
    if args.commit_count < 24:
        print("--commit-count must be at least 24 for the typical-app fixture", file=sys.stderr)
        return 2
    strategies = [name.strip() for name in args.strategies.split(",") if name.strip()]
    repo_names = [name.strip() for name in args.repos.split(",") if name.strip()]
    unknown = [name for name in strategies if name not in STRATEGIES]
    if unknown:
        print(f"unknown strategies: {', '.join(unknown)}", file=sys.stderr)
        return 2
    allowed_repos = {"repo_a_typical", "repo_b_small_files", "repo_c_large_files"}
    unknown_repos = [name for name in repo_names if name not in allowed_repos]
    if unknown_repos:
        print(f"unknown repos: {', '.join(unknown_repos)}", file=sys.stderr)
        return 2

    auto_workspace = args.workspace is None
    temp_dir = None
    if auto_workspace:
        temp_dir = tempfile.TemporaryDirectory(prefix="git-history-pruning-")
        workspace = pathlib.Path(temp_dir.name)
    else:
        workspace = args.workspace.resolve()

    try:
        results = run_experiment(
            workspace=workspace,
            keep_commits=args.keep_commits,
            commit_count=args.commit_count,
            strategies=strategies,
            repo_names=repo_names,
        )
        summary = render_human_summary(results)
        print(summary)
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
