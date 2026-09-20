"""Closing a terminal has to leave the UI knowing it is closed.

The bug: ``terminate`` marked the session closed and the very next line read the
screen to publish it - but reads refused closed sessions, so the exception
escaped, the call came back as a JSON-RPC error, and no event was ever emitted.
The panel went on drawing a live terminal that was already dead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_ai.tools.terminal.manager import (
    PersistentTerminalManager,
    TerminalSession,
    TerminalSessionError,
)


class _Child:
    def __init__(self) -> None:
        self.terminated = 0
        self.alive = True

    def terminate(self) -> None:
        self.terminated += 1
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive

    def read_nonblocking(self, _size):
        raise OSError("nothing to read")


class _Screen:
    display = ["$ "]
    cursor = type("C", (), {"x": 0, "y": 0})()


def _manager_with_session(tmp_path: Path) -> tuple[PersistentTerminalManager, _Child]:
    manager = PersistentTerminalManager()
    child = _Child()
    manager._sessions["s1"] = TerminalSession(
        session_id="s1", child=child, screen=_Screen(), stream=object(),
        cwd=tmp_path, rows=24, cols=80,
    )
    return manager, child


def test_a_closed_session_can_still_be_read(tmp_path: Path) -> None:
    manager, _ = _manager_with_session(tmp_path)
    manager.terminate("s1")

    screen = manager.read_screen("s1")
    assert screen["closed"] is True
    assert screen["session_id"] == "s1"


def test_terminating_twice_is_not_an_error(tmp_path: Path) -> None:
    manager, child = _manager_with_session(tmp_path)
    manager.terminate("s1")
    manager.terminate("s1")

    # The second one is a no-op, not a second kill and not a raise.
    assert child.terminated == 1


def test_terminating_something_that_never_existed_still_raises(tmp_path: Path) -> None:
    manager, _ = _manager_with_session(tmp_path)
    with pytest.raises(TerminalSessionError, match="Unknown terminal session"):
        manager.terminate("nope")


def test_writing_to_a_closed_session_is_still_refused(tmp_path: Path) -> None:
    # Reads describe a dead session; writes to one are a real mistake.
    manager, _ = _manager_with_session(tmp_path)
    manager.terminate("s1")
    with pytest.raises(TerminalSessionError, match="closed"):
        manager.send_text("s1", "ls")


def test_a_closed_session_stops_being_the_latest(tmp_path: Path) -> None:
    manager, _ = _manager_with_session(tmp_path)
    assert manager.latest_session_id() == "s1"
    manager.terminate("s1")
    assert manager.latest_session_id() is None


def test_polling_skips_a_closed_session(tmp_path: Path) -> None:
    manager, _ = _manager_with_session(tmp_path)
    manager.terminate("s1")
    assert manager.poll() == []
