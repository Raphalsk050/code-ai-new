from __future__ import annotations

import asyncio
from pathlib import Path

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.terminal import (
    InterruptTerminalTool,
    ReadScreenTool,
    SendTerminalTextTool,
    StartTerminalTool,
    TerminalEnterTool,
    TerminateTerminalTool,
)
from code_ai.util.paths import WorkspacePolicy


class FakeTerminalManager:
    def __init__(self) -> None:
        self.created: tuple[Path, object] | None = None
        self.sent_text: list[tuple[str, str]] = []
        self.entered: list[str] = []
        self.interrupted: list[str] = []
        self.terminated: list[str] = []

    def create(
        self, *, cwd: Path, command: object = None, rows: int = 24, cols: int = 80, env=None
    ) -> str:
        self.created = (cwd, command)
        return "term-1"

    def send_text(self, session_id: str, text: str) -> None:
        self.sent_text.append((session_id, text))

    def send_enter(self, session_id: str) -> None:
        self.entered.append(session_id)

    def interrupt(self, session_id: str) -> None:
        self.interrupted.append(session_id)

    def terminate(self, session_id: str) -> None:
        self.terminated.append(session_id)

    def read_screen(self, session_id: str, *, include_cursor: bool = True) -> dict[str, object]:
        return {
            "session_id": session_id,
            "rows": 24,
            "columns": 80,
            "screen": "ready",
            "closed": False,
        }


def make_context(tmp_path, manager: FakeTerminalManager) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        terminal_manager=manager,
    )


async def test_atomic_terminal_tools_delegate_to_manager(tmp_path) -> None:
    manager = FakeTerminalManager()
    context = make_context(tmp_path, manager)

    started = await StartTerminalTool().execute({"cwd": "."}, context)
    await SendTerminalTextTool().execute({"session_id": "term-1", "text": "ls"}, context)
    await TerminalEnterTool().execute({"session_id": "term-1"}, context)
    await InterruptTerminalTool().execute({"session_id": "term-1"}, context)
    screen = await ReadScreenTool().execute({"session_id": "term-1"}, context)
    terminated = await TerminateTerminalTool().execute({"session_id": "term-1"}, context)

    assert started["session_id"] == "term-1"
    assert manager.created == (tmp_path, None)
    assert manager.sent_text == [("term-1", "ls")]
    assert manager.entered == ["term-1"]
    assert manager.interrupted == ["term-1"]
    assert screen["screen"] == "ready"
    assert terminated == {"session_id": "term-1", "status": "terminated"}
    assert manager.terminated == ["term-1"]
