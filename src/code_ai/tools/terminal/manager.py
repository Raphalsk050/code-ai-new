from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from code_ai.core.errors import TerminalSessionError


@dataclass(slots=True)
class TerminalSession:
    session_id: str
    child: object
    screen: object
    stream: object
    cwd: Path
    rows: int
    cols: int
    closed: bool = False


class PersistentTerminalManager:
    """Owns POSIX PTY sessions and their emulated screens."""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._supported = platform.system() != "Windows"
        self._pexpect = None
        self._pyte = None

    def _load(self) -> None:
        if not self._supported:
            raise TerminalSessionError("Persistent terminal control is POSIX-only in this version.")
        if self._pexpect is None or self._pyte is None:
            try:
                import pexpect  # type: ignore
                import pyte  # type: ignore
            except Exception as exc:
                raise TerminalSessionError(
                    "pexpect and pyte are required for persistent terminals."
                ) from exc
            self._pexpect = pexpect
            self._pyte = pyte

    def create(
        self, *, cwd: Path, command: str | None = None, rows: int = 24, cols: int = 80
    ) -> str:
        self._load()
        shell = command or os.environ.get("SHELL") or "/bin/sh"
        child = self._pexpect.spawn(
            shell,
            cwd=str(cwd),
            dimensions=(rows, cols),
            encoding="utf-8",
            timeout=0,
        )
        screen = self._pyte.Screen(cols, rows)
        stream = self._pyte.Stream(screen)
        session_id = str(uuid4())
        self._sessions[session_id] = TerminalSession(
            session_id, child, screen, stream, cwd, rows, cols
        )
        self._drain(session_id)
        return session_id

    def send_text(self, session_id: str, text: str) -> None:
        session = self._get(session_id)
        session.child.send(text)
        self._drain(session_id)

    def send_enter(self, session_id: str) -> None:
        session = self._get(session_id)
        session.child.sendline("")
        self._drain(session_id)

    def send_control(self, session_id: str, key: str) -> None:
        session = self._get(session_id)
        session.child.sendcontrol(key)
        self._drain(session_id)

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        session = self._get(session_id)
        session.child.setwinsize(rows, cols)
        session.screen.resize(cols, rows)
        session.rows = rows
        session.cols = cols
        self._drain(session_id)

    def interrupt(self, session_id: str) -> None:
        self.send_control(session_id, "c")

    def terminate(self, session_id: str) -> None:
        session = self._get(session_id)
        session.child.terminate(force=True)
        session.closed = True

    def read_screen(self, session_id: str, *, include_cursor: bool = True) -> dict[str, object]:
        session = self._get(session_id)
        self._drain(session_id)
        display = "\n".join(session.screen.display)
        payload: dict[str, object] = {
            "session_id": session_id,
            "rows": session.rows,
            "columns": session.cols,
            "screen": display.rstrip(),
            "closed": session.closed or not session.child.isalive(),
        }
        if include_cursor:
            payload["cursor"] = {"x": session.screen.cursor.x, "y": session.screen.cursor.y}
        return payload

    def close_all(self) -> None:
        for session_id in list(self._sessions):
            try:
                self.terminate(session_id)
            except TerminalSessionError:
                pass

    def _get(self, session_id: str) -> TerminalSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise TerminalSessionError(f"Unknown terminal session: {session_id}")
        if session.closed:
            raise TerminalSessionError(f"Terminal session is closed: {session_id}")
        return session

    def _drain(self, session_id: str) -> None:
        session = self._sessions[session_id]
        while True:
            try:
                data = session.child.read_nonblocking(size=4096, timeout=0)
            except Exception:
                return
            if not data:
                return
            session.stream.feed(data)
