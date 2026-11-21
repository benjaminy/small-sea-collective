"""Async Git subprocess execution: the async twin of cod_sync.git."""

import locale
import logging
import os
import signal
import subprocess

import anyio

from cod_sync.git import GitCmdFailed

logger = logging.getLogger("cod_sync")

STOP_GRACE = 5  # seconds a cancelled git gets to exit before SIGKILL


async def git_cmd(git_params, raise_on_error=True, env=None):
    return await _run(git_params, text=True, raise_on_error=raise_on_error, env=env)


async def git_cmd_binary(git_params, raise_on_error=True, env=None):
    """Run a git command whose stdout must survive as exact bytes.

    stderr is still decoded, because it only ever carries git's own diagnostics.
    """
    return await _run(git_params, text=False, raise_on_error=raise_on_error, env=env)


def _decode(data):
    # The same decoding subprocess.run(text=True) does.
    text = data.decode(locale.getpreferredencoding(False))
    return text.replace("\r\n", "\n").replace("\r", "\n")


async def _run(git_params, *, text, raise_on_error, env):
    git_cmd = ["git"] + list(git_params)
    # Git's own process group, so cancelling can stop the children git starts
    # (ssh, hooks, the signing program) as well as git.
    proc = await anyio.open_process(
        git_cmd, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        start_new_session=True,
    )
    stdout, stderr = bytearray(), bytearray()
    async with proc:
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_drain, proc.stdout, stdout)
                tg.start_soon(_drain, proc.stderr, stderr)
            await proc.wait()
        finally:
            if proc.returncode is None:
                # Cancelled or failed while git still runs. Shielded, so the
                # cancellation that brought us here does not cut the stop short.
                with anyio.CancelScope(shield=True):
                    await _stop(proc)
    stdout, stderr = bytes(stdout), bytes(stderr)
    if text:
        stdout, stderr = _decode(stdout), _decode(stderr)
    result = subprocess.CompletedProcess(git_cmd, proc.returncode, stdout, stderr)
    if result.returncode != 0:
        err = stderr if text else stderr.decode("utf-8", "replace")
        exn = GitCmdFailed(git_params, result.returncode, stdout, err)
        if raise_on_error:
            raise exn
        logger.debug(str(exn))
    return result


async def _drain(stream, buffer):
    async for chunk in stream:
        buffer.extend(chunk)


async def _stop(proc):
    """Stop git, and the children it started, after the caller stopped waiting.

    SIGTERM first, because git then removes its lock files (index.lock and
    the like) before exiting; SIGKILL would leave them and break the next
    write. SIGKILL only for a process group still running after the grace
    period. The whole group, because a child still holding git's output
    pipes would keep wait() blocked, and a child that let go of them would
    outlive git unnoticed.
    """
    _signal_group(proc, signal.SIGTERM)
    with anyio.move_on_after(STOP_GRACE) as grace:
        await _group_gone(proc)
    if grace.cancelled_caught:
        _signal_group(proc, signal.SIGKILL)
        await _group_gone(proc)


async def _group_gone(proc):
    """Return once git and every process left in its group have exited."""
    await proc.wait()
    while _signal_group(proc, 0):
        await anyio.sleep(0.05)


def _signal_group(proc, sig):
    """Send sig to git's process group; return False if the group is gone."""
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return False
    return True
