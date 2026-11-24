"""Micro tests for cod_sync.async_git."""

import os

import anyio
import pytest

import cod_sync.async_git as async_git
from cod_sync.git import GitCmdFailed

pytestmark = pytest.mark.anyio


async def test_text_output_and_failure():
    result = await async_git.git_cmd(["--version"])
    assert result.returncode == 0 and result.stdout.startswith("git version")

    with pytest.raises(GitCmdFailed):
        await async_git.git_cmd(["no-such-subcommand"])
    result = await async_git.git_cmd(["no-such-subcommand"], raise_on_error=False)
    assert result.returncode != 0 and isinstance(result.stderr, str)


async def _cancel_git(git_params, ready, timeout=5):
    """Run git, cancel it once ready() is true, and return how git_cmd ended.

    Fails if stopping takes longer than timeout seconds.
    """
    outcome = []

    async def run():
        try:
            await async_git.git_cmd(git_params)
        except anyio.get_cancelled_exc_class():
            outcome.append("cancelled")
            raise
        outcome.append("returned")

    with anyio.fail_after(timeout):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            while not ready():
                await anyio.sleep(0.01)
            tg.cancel_scope.cancel()
    return outcome


async def _assert_gone(pid):
    """Fail unless pid is gone within two seconds.

    A killed child may linger briefly as a zombie until launchd or init reaps it.
    """
    with anyio.fail_after(2):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await anyio.sleep(0.05)


async def test_cancelling_the_caller_stops_git(monkeypatch):
    started = []
    real_open = anyio.open_process

    async def recording_open(*args, **kwargs):
        proc = await real_open(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(anyio, "open_process", recording_open)
    # A shell alias makes git itself wait for its child.
    outcome = await _cancel_git(["-c", "alias.slow=!sleep 30", "slow"], lambda: started)

    assert outcome == ["cancelled"]
    assert started[0].returncode is not None


async def test_cancelled_write_releases_its_lock(tmp_path):
    repo = tmp_path / "repo"
    await async_git.git_cmd(["init", "-q", str(repo)])
    (repo / ".gitattributes").write_text("*.txt filter=slow\n")
    (repo / "a.txt").write_text("a\n")
    # A clean filter that stalls keeps `git add` holding index.lock.
    slow_add = ["-C", str(repo), "-c", "filter.slow.clean=sleep 30; cat", "add", "a.txt"]
    outcome = await _cancel_git(slow_add, (repo / ".git" / "index.lock").exists, timeout=10)

    assert outcome == ["cancelled"]
    assert not (repo / ".git" / "index.lock").exists()
    await async_git.git_cmd(["-C", str(repo), "-c", "filter.slow.clean=cat", "add", "a.txt"])


async def test_cancelling_kills_children_that_ignore_sigterm(monkeypatch):
    monkeypatch.setattr(async_git, "STOP_GRACE", 0.2)
    # The ignored SIGTERM is inherited by sleep, which keeps git's pipes open.
    start = anyio.current_time()
    outcome = await _cancel_git(
        ["-c", "alias.stubborn=!trap '' TERM; sleep 30", "stubborn"],
        lambda: anyio.current_time() - start > 0.3,
    )
    assert outcome == ["cancelled"]


async def test_cancelling_after_git_exits_stops_children_holding_its_output(monkeypatch, tmp_path):
    monkeypatch.setattr(async_git, "STOP_GRACE", 0.2)
    started = []
    real_open = anyio.open_process

    async def recording_open(*args, **kwargs):
        proc = await real_open(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(anyio, "open_process", recording_open)
    pid_file = tmp_path / "pid"
    # git and its shell exit at once; the background sleep keeps git's pipes open.
    alias = f"!sleep 30 & echo $! > {pid_file}"
    outcome = await _cancel_git(
        ["-c", f"alias.lingering={alias}", "lingering"],
        lambda: (
            started and started[0].returncode is not None
            and pid_file.exists() and pid_file.read_text().strip()
        ),
    )

    assert outcome == ["cancelled"]
    await _assert_gone(int(pid_file.read_text()))


async def test_cancelling_kills_children_that_outlive_git(monkeypatch, tmp_path):
    monkeypatch.setattr(async_git, "STOP_GRACE", 0.2)
    pid_file = tmp_path / "pid"
    # The background sleep ignores SIGTERM and holds none of git's pipes, so
    # git and its shell exit on SIGTERM and leave it running in their group.
    alias = f"!(trap '' TERM; exec sleep 30) >/dev/null 2>&1 & echo $! > {pid_file}; wait"
    outcome = await _cancel_git(
        ["-c", f"alias.orphan={alias}", "orphan"],
        lambda: pid_file.exists() and pid_file.read_text().strip(),
    )

    assert outcome == ["cancelled"]
    await _assert_gone(int(pid_file.read_text()))
