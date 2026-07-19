from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from code_ai.tools.terminal.manager import PersistentTerminalManager


class FakePtySession:
    """In-memory PtySession: queued chunks come back from read_nonblocking."""

    def __init__(self) -> None:
        self.pending: list[str] = []
        self.sent: list[str] = []
        self.lines: list[str] = []
        self.controls: list[str] = []
        self.alive = True
        self.terminated = False

    def send(self, text: str) -> None:
        self.sent.append(text)

    def send_line(self, text: str) -> None:
        self.lines.append(text)

    def send_control(self, key: str) -> None:
        self.controls.append(key)

    def resize(self, rows: int, cols: int) -> None:
        self.size = (rows, cols)

    def read_nonblocking(self, size: int) -> str:
        return self.pending.pop(0) if self.pending else ""

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False


def make_manager() -> tuple[PersistentTerminalManager, list[FakePtySession]]:
    sessions: list[FakePtySession] = []

    def factory(*, cwd: Path, command: str | None, rows: int, cols: int) -> FakePtySession:
        session = FakePtySession()
        sessions.append(session)
        return session

    return PersistentTerminalManager(session_factory=factory), sessions


def test_manager_renders_backend_output_through_pyte(tmp_path) -> None:
    manager, sessions = make_manager()
    session_id = manager.create(cwd=tmp_path)
    sessions[0].pending.append("hello world\r\n$ ")

    screen = manager.read_screen(session_id)

    assert "hello world" in str(screen["screen"])
    assert screen["closed"] is False
    assert screen["rows"] == 24 and screen["columns"] == 80


def test_manager_delegates_input_to_the_backend_session(tmp_path) -> None:
    manager, sessions = make_manager()
    session_id = manager.create(cwd=tmp_path)

    manager.send_text(session_id, "ls")
    manager.send_enter(session_id)
    manager.interrupt(session_id)
    manager.terminate(session_id)

    backend = sessions[0]
    assert backend.sent == ["ls"]
    assert backend.lines == [""]
    assert backend.controls == ["c"]
    assert backend.terminated is True


def test_manager_poll_reports_only_changed_screens(tmp_path) -> None:
    manager, sessions = make_manager()
    session_id = manager.create(cwd=tmp_path)
    sessions[0].pending.append("first\r\n")

    changed = manager.poll()
    assert [item["session_id"] for item in changed] == [session_id]
    assert "first" in str(changed[0]["screen"])

    # Nothing new: a second poll must be silent (no duplicate UI updates).
    assert manager.poll() == []

    sessions[0].pending.append("second\r\n")
    changed = manager.poll()
    assert len(changed) == 1
    assert "second" in str(changed[0]["screen"])


def test_manager_poll_skips_closed_sessions(tmp_path) -> None:
    manager, sessions = make_manager()
    session_id = manager.create(cwd=tmp_path)
    sessions[0].pending.append("output\r\n")
    manager.terminate(session_id)

    assert manager.poll() == []


def test_latest_session_id_skips_closed_sessions(tmp_path) -> None:
    manager, _ = make_manager()
    first = manager.create(cwd=tmp_path)
    second = manager.create(cwd=tmp_path)

    assert manager.latest_session_id() == second
    manager.terminate(second)
    assert manager.latest_session_id() == first
    manager.close_all()
    assert manager.latest_session_id() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY backend")
def test_windows_backend_runs_a_real_shell(tmp_path) -> None:
    # End-to-end proof that the interactive terminal works on Windows: spawn
    # the real default shell through pywinpty/ConPTY, type a command, and read
    # its output back through the pyte screen. This exact flow raised
    # "POSIX-only" before the backend Strategy existed.
    pytest.importorskip("winpty")
    manager = PersistentTerminalManager()
    session_id = manager.create(cwd=tmp_path)
    try:
        manager.send_text(session_id, "echo marcador123")
        manager.send_enter(session_id)
        deadline = time.monotonic() + 15
        screen = ""
        while time.monotonic() < deadline:
            screen = str(manager.read_screen(session_id)["screen"])
            if "marcador123" in screen:
                break
            time.sleep(0.2)
        assert "marcador123" in screen
    finally:
        manager.close_all()
