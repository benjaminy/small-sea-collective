#!/usr/bin/env python3
"""Run Pi/Bonsai jobs one after another, each under a time limit.

Usage: pi-run.py [--timeout SECONDS] [--max-rows N] [--deadline ISO8601]
                 [--cwd DIR] NAME...

Each NAME is a folder under $BONSAI_JOBS_DIR holding task.md, written by the
caller. Use fresh job names for each attempt. This script appends the output contract to the task, so task.md
should describe only the question and the files to look at.

For each job it writes, in the job folder:
  prompt.md     the full prompt Pi received
  result.md     Pi's answer (Pi writes this; any old copy is deleted first)
  stdout.jsonl  Pi's event stream; read it with pi-status.py, not directly
  stderr.txt
  process.json  pid, start/finish times, exit code, whether it timed out

Pi runs in --cwd (default: the current directory); result paths are relative
to it. The queue stops early if the server stops answering, and skips jobs
that would start with less than a minute before --deadline.
At queue completion it prints checked results through pi-status.py.
"""

import argparse
import datetime as dt
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import bonsai_env as B

CONTRACT = """

## Worker restrictions

Do not edit repository files or write scripts.
You may write only the required result file.

## Output contract

Write your answer to this file and nowhere else: {result}
Write the whole file in one step, after you have finished investigating.
Do not write a draft or skeleton first.

Use this format, one finding per line:

path:line | exact text copied from that line | your claim

- `path` is relative to {cwd}.
- The middle part must be copied exactly from that line of that file.
- Write at most {max_rows} findings.
- If there are no findings, write one line containing only NONE.
- Lines starting with # are ignored.
- The last line of the file must be exactly: {sentinel}
"""


def pi_profile(jobs):
    """A Pi profile that knows only the local Bonsai provider."""
    profile = jobs / "pi-profile"
    profile.mkdir(exist_ok=True)
    B.write_json(profile / "models.json", {
        "providers": {
            "bonsai": {
                "baseUrl": B.BASE_URL,
                "api": "openai-completions",
                "apiKey": "local",
                "models": [{
                    "id": B.MODEL,
                    "name": f"{B.MODEL} (local)",
                    "reasoning": True,
                    "input": ["text"],
                    "contextWindow": B.CTX,
                    "maxTokens": 4096,
                }],
            }
        }
    })
    return profile


def run_job(job, prompt, cwd, timeout, env, max_rows):
    for name in ("result.md", "process.json"):
        (job / name).unlink(missing_ok=True)
    (job / "prompt.md").write_text(prompt)
    args = [
        B.PI_BIN, "--provider", "bonsai", "--model", B.MODEL,
        "--offline", "--no-session", "--print", "--mode", "json", prompt,
    ]
    record = dict(
        state="running", started=time.time(), timeout=timeout, cwd=str(cwd),
        max_rows=max_rows, model=B.MODEL, harness="pi",
    )
    with (job / "stdout.jsonl").open("w") as out, (job / "stderr.txt").open("w") as err:
        # Pi waits at startup for stdin to reach EOF. A pipe or socket that is
        # still open when it starts makes it wait for ever: it writes nothing,
        # never contacts the model, and closing the pipe afterwards does not
        # release it. Callers under a harness usually have exactly that kind of
        # stdin, so give Pi /dev/null rather than whatever we inherited.
        p = subprocess.Popen(
            args, cwd=cwd, env=env, stdout=out, stderr=err,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        record["pid"] = p.pid
        B.write_json(job / "process.json", record)
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
        finally:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(p.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    p.wait(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    pass
            p.wait()
    record.update(state="finished", exit_code=p.returncode, finished=time.time())
    if (job / "stdout.jsonl").stat().st_size == 0:
        # Not a slow job: Pi never emitted its opening event, so it never ran.
        record["no_output"] = True
    B.write_json(job / "process.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="+")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--max-rows", type=int, default=15)
    parser.add_argument("--deadline", type=dt.datetime.fromisoformat)
    parser.add_argument("--cwd", default=os.getcwd())
    opts = parser.parse_args()

    if len(set(opts.names)) != len(opts.names):
        parser.error("job names must be distinct")
    jobs = B.jobs_dir()
    cwd = os.path.abspath(opts.cwd)
    deadline = opts.deadline.timestamp() if opts.deadline else None
    for name in opts.names:
        if not (jobs / name / "task.md").is_file():
            sys.exit(f"missing {jobs / name / 'task.md'}")
        if (jobs / name / "process.json").exists():
            sys.exit(f"{name}: already attempted; use a fresh job name")

    env = {k: v for k, v in os.environ.items() if k not in B.CLOUD_KEYS}
    env["PI_CODING_AGENT_DIR"] = str(pi_profile(jobs))

    for name in opts.names:
        B.write_json(jobs / name / "process.json", dict(
            state="queued", cwd=cwd, max_rows=opts.max_rows,
            model=B.MODEL, harness="pi",
        ))
    for name in opts.names:
        job = jobs / name
        timeout = opts.timeout
        if deadline is not None:
            timeout = min(timeout, int(deadline - time.time()) - 5)
            if timeout < 60:
                B.write_json(job / "process.json", dict(
                    state="skipped", reason="deadline too close", model=B.MODEL,
                    harness="pi", cwd=cwd, max_rows=opts.max_rows,
                ))
                continue
        if B.server_models() is None:
            print(f"{name}: server not answering at {B.BASE_URL}; queue stopped")
            for pending in opts.names[opts.names.index(name):]:
                B.write_json(jobs / pending / "process.json", dict(
                    state="skipped", reason="server unavailable", model=B.MODEL,
                    harness="pi", cwd=cwd, max_rows=opts.max_rows,
                ))
            break
        prompt = (job / "task.md").read_text() + CONTRACT.format(
            result=job / "result.md", cwd=cwd, max_rows=opts.max_rows,
            sentinel=B.SENTINEL,
        )
        record = run_job(job, prompt, cwd, timeout, env, opts.max_rows)
        elapsed = round(record["finished"] - record["started"])
        how = "timed out" if record.get("timed_out") else f"exit {record['exit_code']}"
        print(f"{name}: {how} after {elapsed} s")
    return subprocess.run([
        sys.executable, str(Path(__file__).with_name("pi-status.py")), *opts.names,
    ], env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
