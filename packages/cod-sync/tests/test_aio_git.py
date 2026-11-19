"""Micro tests for cod_sync.aio.git."""

import asyncio

import pytest

import cod_sync.aio.git as aio_git
from cod_sync.git import GitCmdFailed

pytestmark = pytest.mark.anyio


async def test_text_output_and_failure():
    result = await aio_git.git_cmd(["--version"])
    assert result.returncode == 0 and result.stdout.startswith("git version")

    with pytest.raises(GitCmdFailed):
        await aio_git.git_cmd(["no-such-subcommand"])
    result = await aio_git.git_cmd(["no-such-subcommand"], raise_on_error=False)
    assert result.returncode != 0 and isinstance(result.stderr, str)


async def test_cancelling_the_caller_stops_git(monkeypatch):
    started = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_exec)
    # A shell alias makes git itself wait for its child.
    task = asyncio.create_task(aio_git.git_cmd(["-c", "alias.slow=!sleep 30", "slow"]))
    while not started:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    assert started[0].returncode is not None


async def test_cancelled_write_releases_its_lock(tmp_path):
    repo = tmp_path / "repo"
    await aio_git.git_cmd(["init", "-q", str(repo)])
    (repo / ".gitattributes").write_text("*.txt filter=slow\n")
    (repo / "a.txt").write_text("a\n")
    # A clean filter that stalls keeps `git add` holding index.lock.
    slow_add = ["-C", str(repo), "-c", "filter.slow.clean=sleep 30; cat", "add", "a.txt"]
    task = asyncio.create_task(aio_git.git_cmd(slow_add))
    while not (repo / ".git" / "index.lock").exists():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)

    assert not (repo / ".git" / "index.lock").exists()
    await aio_git.git_cmd(["-C", str(repo), "-c", "filter.slow.clean=cat", "add", "a.txt"])


async def test_cancelling_kills_children_that_ignore_sigterm(monkeypatch):
    monkeypatch.setattr(aio_git, "STOP_GRACE", 0.2)
    # The ignored SIGTERM is inherited by sleep, which keeps git's pipes open.
    task = asyncio.create_task(
        aio_git.git_cmd(["-c", "alias.stubborn=!trap '' TERM; sleep 30", "stubborn"])
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
