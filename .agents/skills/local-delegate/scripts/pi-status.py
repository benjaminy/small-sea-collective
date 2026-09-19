#!/usr/bin/env python3
"""Summarize Pi/Bonsai jobs in a few lines each, and check their results.

Usage: pi-status.py [--all] [--inspect] [NAME...]

For each job it prints whether Pi is running or finished, how long it took,
how many turns and tool calls it made, whether it compacted its context, and
a verdict on result.md:

  ok        sentinel present, row count within the limit, every citation
            quotes non-empty text that matches its file and line; the rows
            are printed
  failed    no result, no sentinel, too many rows, or bad rows or citations

Execution failures also withhold rows unless --inspect is used.

With --inspect it prints bounded citation-matching rows even after failure.
Without names it lists unfinished jobs; --all includes completed jobs.
It never prints Pi's transcript. Exit status is 0 only if every job is ok.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import bonsai_env as B


def activity(stdout):
    """Count turns, tool calls and compactions in Pi's event stream."""
    turns = tools = tool_errors = compactions = 0
    recent = []
    tokens = None
    try:
        lines = stdout.open()
    except OSError:
        return None
    with lines:
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "turn_end":
                turns += 1
                usage = event.get("message", {}).get("usage") or {}
                tokens = usage.get("totalTokens", tokens)
            elif kind == "tool_execution_start":
                tools += 1
                recent = (recent + [event.get("toolName", "?")])[-4:]
            elif kind == "tool_execution_end" and event.get("isError"):
                tool_errors += 1
            elif kind == "compaction_start":
                compactions += 1
    return dict(
        turns=turns, tools=tools, tool_errors=tool_errors,
        compactions=compactions, recent=recent, tokens=tokens,
    )


def citation_ok(cwd, cite, quote):
    path, _, line = cite.rpartition(":")
    if not quote.strip() or not path or not line.isdigit():
        return False
    try:
        lines = (Path(cwd) / path).read_text(errors="replace").splitlines()
    except OSError:
        return False
    n = int(line)
    if not 1 <= n <= len(lines):
        return False
    squash = lambda s: " ".join(s.split())
    return squash(quote) in squash(lines[n - 1])


def check_result(result, cwd, max_rows):
    """Return a verdict and bounded citation-matching rows, even on failure."""
    try:
        text = result.read_text()
    except OSError:
        return "failed: no result.md", []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    complete = bool(lines and lines[-1] == B.SENTINEL)
    rows = [line for line in (lines[:-1] if complete else lines)
            if not line.startswith("#")]
    if complete and rows == ["NONE"]:
        return "ok: NONE (absence not established)", []
    errors = []
    if not complete:
        errors.append("no final sentinel")
    if not rows:
        errors.append("no findings or NONE")
    if len(rows) > max_rows:
        errors.append(f"{len(rows)} rows, limit {max_rows}")
    valid = []
    bad = 0
    for row in rows:
        parts = row.split(" | ", 2)
        if (len(parts) != 3 or not parts[2].strip()
                or not citation_ok(cwd, parts[0].strip("` "), parts[1].strip("` "))):
            bad += 1
        elif len(valid) < max_rows:
            valid.append(row)
    if bad:
        errors.append(f"{bad} malformed rows or mismatched citations")
    if errors:
        return "failed: " + "; ".join(errors), valid
    return f"ok: {len(rows)} rows, all citations match", valid


def status(job, inspect=False):
    record = B.read_json(job / "process.json")
    if record is None:
        return f"{job.name}: not started", False
    if record["state"] in ("queued", "skipped"):
        return f"{job.name}: {record['state']} {record.get('reason', '')}".rstrip(), False
    now = time.time()
    if record["state"] == "running" and not B.pid_alive(record["pid"]):
        state = "dead (runner killed?)"
    elif record["state"] == "running":
        state = f"running {round(now - record['started'])} s of {record['timeout']} s"
    else:
        elapsed = round(record["finished"] - record["started"])
        how = "timed out" if record.get("timed_out") else f"exit {record['exit_code']}"
        state = f"finished, {how}, {elapsed} s"
    out = [f"{job.name}: {state}"]
    act = activity(job / "stdout.jsonl")
    if act:
        out.append(
            f"  {act['turns']} turns, {act['tools']} tool calls "
            f"({act['tool_errors']} errors), last: {', '.join(act['recent']) or '-'}; "
            f"{act['compactions']} compactions; last context {act['tokens']} tokens"
        )
    ok = False
    if record["state"] == "finished" or inspect:
        verdict, rows = check_result(job / "result.md", record["cwd"], record["max_rows"])
        execution_ok = (record["state"] == "finished"
                        and not record.get("timed_out") and record.get("exit_code") == 0)
        ok = execution_ok and verdict.startswith("ok")
        out.append(f"  result format: {verdict}")
        if record["state"] == "finished":
            record["validation"] = verdict
            B.write_json(job / "process.json", record)
        if ok or inspect:
            if not ok:
                out.append("  inspection only: citation-matching leads from an unsuccessful job")
            out.extend(f"  {row}" for row in rows)
    return "\n".join(out), ok


def main():
    parser = argparse.ArgumentParser(description="Show local job status and checked results.")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--all", action="store_true", help="include completed and skipped jobs")
    parser.add_argument("--inspect", action="store_true", help="show bounded matching rows after failure")
    opts = parser.parse_args()
    jobs = B.jobs_dir()
    selected = [jobs / name for name in opts.names]
    if not opts.names:
        for job in sorted(jobs.iterdir()):
            if not (job / "task.md").is_file():
                continue
            record = B.read_json(job / "process.json")
            if opts.all or record is None or record["state"] in ("queued", "running"):
                selected.append(job)
    all_ok = True
    for job in selected:
        text, ok = status(job, inspect=opts.inspect)
        print(text)
        all_ok = all_ok and ok
    if not selected:
        print("No matching jobs.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
