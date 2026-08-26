"""How the file tools behave when other software on the host holds a file.

Windows is where this bites - an antivirus, a search indexer, a sync client or
a disk-encryption agent opens a file for a moment and an ordinary write fails.
The failures are simulated here rather than provoked, because there is no
Windows runner: the tests raise the exact OSError shape Windows produces and
assert the tools ride it out.
"""

from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path

import pytest

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import EditCodeTool, ReadFileTool, WriteFileTool
from code_ai.util.fileio import ERROR_SHARING_VIOLATION, FileOperationError
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path, **file_io) -> ToolContext:
    settings = {"retry_attempts": 4, "retry_initial_delay_ms": 0, "retry_max_delay_ms": 0}
    settings.update(file_io)
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "file_io": settings}
    )
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def sharing_violation() -> OSError:
    exc = OSError(errno.EACCES, "The process cannot access the file")
    exc.winerror = ERROR_SHARING_VIOLATION
    return exc


def hold_replace(monkeypatch, *, releases_after: int | None) -> dict[str, int]:
    """Make ``os.replace`` behave like a file another process is holding."""

    real = os.replace
    calls = {"n": 0}

    def guarded(source, destination, *args, **kwargs):
        calls["n"] += 1
        if releases_after is None or calls["n"] < releases_after:
            raise sharing_violation()
        return real(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", guarded)
    return calls


async def test_a_write_rides_out_a_lock_that_lets_go(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    hold_replace(monkeypatch, releases_after=3)

    result = await WriteFileTool().execute({"path": "app.py", "content": "new\n"}, context)

    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "new\n"
    assert result["write_attempts"] == 3
    assert "atomic" not in result


async def test_a_write_that_never_gets_the_swap_rewrites_in_place(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    hold_replace(monkeypatch, releases_after=None)

    result = await WriteFileTool().execute({"path": "app.py", "content": "new\n"}, context)

    assert target.read_text(encoding="utf-8") == "new\n"
    # Reported honestly: the write went through, but not as one indivisible step.
    assert result["atomic"] is False
    assert result["write_attempts"] > 1


async def test_a_write_leaves_no_temporary_file_behind_when_it_gives_up(
    tmp_path, monkeypatch
) -> None:
    context = make_context(tmp_path, allow_non_atomic_fallback=False)
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    hold_replace(monkeypatch, releases_after=None)

    with pytest.raises(FileOperationError):
        await WriteFileTool().execute({"path": "app.py", "content": "new\n"}, context)

    assert [p.name for p in tmp_path.iterdir()] == ["app.py"]
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"


async def test_the_failure_says_what_held_the_file(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path, allow_non_atomic_fallback=False)
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    hold_replace(monkeypatch, releases_after=None)

    with pytest.raises(FileOperationError) as caught:
        await WriteFileTool().execute({"path": "app.py", "content": "new\n"}, context)

    message = str(caught.value)
    # The agent reads this and can act on it, instead of retrying blindly.
    assert "another process" in message
    assert "WinError 32" in message
    assert "file_io.retry_attempts" in message


async def test_an_edit_rides_out_a_lock_too(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    hold_replace(monkeypatch, releases_after=2)

    result = await EditCodeTool().execute(
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"}, context
    )

    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert result["changed"] is True
    assert result["write_attempts"] == 2


async def test_a_read_waits_for_a_file_being_encrypted(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    (tmp_path / "app.py").write_text("payload\n", encoding="utf-8")
    real = Path.read_bytes
    calls = {"n": 0}

    def guarded(self):
        calls["n"] += 1
        if calls["n"] < 3:
            raise sharing_violation()
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)

    result = await ReadFileTool().execute({"path": "app.py"}, context)

    assert result["content"] == "payload\n"


async def test_a_missing_file_still_fails_at_once(tmp_path) -> None:
    context = make_context(
        tmp_path,
        retry_attempts=50,
        retry_initial_delay_ms=1000,
        retry_max_delay_ms=1000,
    )

    # Waiting cannot make a missing file appear, so this must not sit through
    # fifty one-second retries before saying so.
    with pytest.raises(Exception) as caught:
        await ReadFileTool().execute({"path": "nope.py"}, context)

    assert not isinstance(caught.value, FileOperationError)
