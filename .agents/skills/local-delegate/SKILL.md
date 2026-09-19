---
name: local-delegate
description: Run bounded research jobs with a local model when saving frontier quota justifies the delay and supervision. Includes a Pi/Bonsai runner for work that can continue while the coordinator waits.
---

# Local delegation

Use local inference when it removes enough frontier work to pay for task preparation, checking, and likely fallback.
A deterministic search or script may be cheaper still.
The coordinator chooses the work and judges the findings; the helpers handle execution and evidence formatting.

## When it helps

Consider task fit and the value of conserving quota separately.
Use the human's conservation guidance and recent usage information, including expected work before reset, rather than deciding from the remaining percentage alone.
Without pressure to conserve, prefer less delay and supervision.
During a quota pause, useful local jobs can make progress, but preparation and review still count.

Within an autonomous voyage, inherit its operating agreement for reserves, delay, deadlines, resources, and resumption.
Keep jobs under the voyage root and use its checked background execution and wake mechanism.
Before pausing, record the jobs directory in the current orientation so a fresh coordinator can recover outstanding work.
Do not create a separate pacing policy or scheduler for this skill.

## Choose a small useful question

Give the worker a bounded scope, relevant files, and the evidence that would let you skip work.
Prefer positive findings that are cheap to check independently.
For example: find up to three places in these files where an error is caught and execution continues; cite each and describe the continuation.
A short answer does not guarantee a cheap investigation.
Absence and completeness claims require checking search coverage, which can erase the saving.

The current Pi/Bonsai setup is best treated as a research assistant for finding evidence.
Start with read-only investigation; keep design decisions and code changes with the coordinator.
Try a new task category with one small job whose usefulness is cheap to evaluate before queuing more.
Reported trials found a useful dependency inventory, while a trust-policy probe and lockfile comparison timed out.
These observations guide task sizing; they do not establish permanent limits on local models.

## Run with Pi and Bonsai

Bonsai has run at about 10 tokens per second with a 32k context window; compaction has tended to derail its work.
Keep context and requested output small.
Local inference uses no subscription quota, but it consumes host resources.

Use Python 3.11 or newer and resolve script paths relative to this skill directory.
Set the environment variables documented in `scripts/bonsai_env.py`; `BONSAI_JOBS_DIR` is required.
During a voyage, use a directory such as `VOYAGE_ROOT/local-jobs`.

1. Run `scripts/bonsai-server.py check`.
   If needed, `up` starts the server using `BONSAI_HOME`; loading takes about a minute and substantial memory.
   Keep it running while more jobs are likely; use `down` to stop a server started for this work when finished.
2. Create a fresh `$BONSAI_JOBS_DIR/NAME/task.md` for each question.
   The runner appends the read-only restrictions and output contract; do not repeat them.
   Those restrictions are worker instructions, not a filesystem sandbox.
3. Launch `scripts/pi-run.py --timeout 1200 --max-rows 15 --cwd REPO NAME1 NAME2 ...` through the environment's checked background mechanism.
   Jobs run serially and the runner prints checked results when the queue ends.
   Use `--deadline ISO8601` to bound the queue, allowing time for review before the voyage ends.
   Execution and completion notifications depend on the calling harness; the runner does not wake a coordinator itself.
4. Wait for the completion notice or arranged wake.
   Queue work before pausing; nothing launches it during the pause.
   Do other frontier work only when it is worth its quota independently; do not poll in a loop.
5. On resumption, `scripts/pi-status.py` lists unfinished jobs.
   Pass job names for their status and checked results, or `--all` to inspect the whole jobs directory.
   Do not load `stdout.jsonl` into frontier context; transcripts have reached 1–2 MB per job.

Use fresh job names for new attempts so earlier evidence remains available.
The scripts record execution facts; record lessons about usefulness and checking effort in existing voyage notes, or `$BONSAI_JOBS_DIR/log.md` for standalone work, when they will inform later choices.
No fixed per-job report is required.

## Use the evidence

The worker writes `result.md` with at most the requested number of rows:

```text
path:line | exact text copied from that line | claim
...
END-OF-RESULT
```

`NONE` followed by the sentinel means the worker found nothing; it is not evidence that nothing exists.
The checker verifies the format and whether each non-empty quote matches its cited line after normalizing whitespace.
It does not prove the claim or establish search coverage.
Execution status and result validation are reported separately.

Sample findings to decide whether further review is worthwhile, and verify claims that will drive a decision.
Do not infer completeness from a sample.
If checking costs as much as doing the work directly, reconsider the task fit.

Do not routinely salvage failed jobs.
When a small result could still help, `pi-status.py --inspect NAME` exposes bounded rows whose citations match, even if other rows failed or execution timed out.
Treat these as leads requiring review, not an accepted answer.
Otherwise do the work directly, drop it, or try one smaller task when there is a concrete reason to expect success.
Avoid repeating an unchanged failure or reading a large transcript merely because the work already consumed time.
