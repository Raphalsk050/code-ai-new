"""The git baseline runs on the turn's critical path, so it must stay off the loop.

Before this was a thread, every turn paid the full ten-second fuse before the
model was called: on Windows the proactor loop's ``communicate()`` never
completed for these calls inside the running app, though the same command took
0.1s from a shell.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

from code_ai.core.git_baseline import GitBaseline


def _completed(stdout: bytes = b"", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["git"], code, stdout=stdout)


async def test_a_git_call_leaves_the_event_loop_free(tmp_path: Path, monkeypatch) -> None:
    baseline = GitBaseline(tmp_path)

    def slow(*_args, **_kwargs):
        time.sleep(0.25)
        return _completed(b"deadbeef\n")

    monkeypatch.setattr(subprocess, "run", slow)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(ticker())
    result = await baseline._git("stash", "create")
    beat.cancel()

    assert result is not None and result.strip() == "deadbeef"
    # A call that held the loop would let through one tick at most.
    assert ticks >= 5, f"the loop only ticked {ticks} times during a 0.25s git call"


async def test_git_never_inherits_stdin(tmp_path: Path, monkeypatch) -> None:
    # A git that decides to prompt would otherwise hold the turn until the fuse.
    seen: dict = {}

    def capture(*args, **kwargs):
        seen.update(kwargs)
        seen["argv"] = args[0]
        return _completed(b"")

    monkeypatch.setattr(subprocess, "run", capture)
    await GitBaseline(tmp_path)._git("rev-parse", "HEAD")

    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["timeout"] > 0
    assert seen["argv"][:3] == ["git", "-C", str(tmp_path)]


async def test_a_failing_git_is_no_answer_rather_than_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(b"nope", code=128))
    assert await GitBaseline(tmp_path)._git("stash", "create") is None


async def test_a_git_that_overruns_its_fuse_is_no_answer(tmp_path: Path, monkeypatch) -> None:
    def expired(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=10.0)

    monkeypatch.setattr(subprocess, "run", expired)
    assert await GitBaseline(tmp_path)._git("stash", "create") is None


async def test_a_missing_git_is_no_answer(tmp_path: Path, monkeypatch) -> None:
    def absent(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", absent)
    assert await GitBaseline(tmp_path)._git("status") is None


async def test_capture_without_a_repository_leaves_no_baseline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(b"", code=128))
    baseline = GitBaseline(tmp_path)
    await baseline.capture()
    assert await baseline.changed_paths() == ()
