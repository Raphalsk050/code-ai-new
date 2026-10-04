from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from code_ai.core.errors import TerminalSessionError
from code_ai.tools.terminal.backends import PtySession, create_pty_session, default_shell
from code_ai.tools.terminal.shell_integration import ShellIntegration, prepare_shell

# Signature of the platform-session factory (see backends.create_pty_session);
# injectable so tests can drive the manager with a fake PTY.
SessionFactory = Callable[..., PtySession]


@dataclass(slots=True)
class TerminalSession:
    session_id: str
    child: PtySession
    screen: object
    stream: object
    cwd: Path
    rows: int
    cols: int
    closed: bool = False
    # Fingerprint of the last screen handed out by poll(), so the poller only
    # emits an update when the rendered display actually changed.
    last_display: str = field(default="", compare=False)
    # Command blocks, from the marks an integrated shell emits.
    integration: ShellIntegration = field(default_factory=ShellIntegration, compare=False)
    #: "bash", "pwsh", ... when the shell was set up to mark its commands.
    shell: str = ""
    #: Where the integration scripts live; removed with the session.
    scratch: Path | None = None


class PersistentTerminalManager:
    """Owns PTY sessions and their emulated screens on every platform.

    The platform-specific spawning/IO lives behind the ``PtySession`` Strategy
    (pexpect on POSIX, pywinpty/ConPTY on Windows — see ``backends``); this
    class only manages session lifecycle and the ``pyte`` screen emulation.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        shell_integration: bool = True,
    ) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._session_factory = session_factory or create_pty_session
        self._shell_integration = shell_integration
        self._pyte = None

    def _load(self) -> None:
        if self._pyte is None:
            try:
                import pyte  # type: ignore
            except Exception as exc:
                raise TerminalSessionError(
                    "pyte is required for persistent terminals."
                ) from exc
            self._pyte = pyte

    def create(
        self,
        *,
        cwd: Path,
        command: str | None = None,
        rows: int = 24,
        cols: int = 80,
        env: Mapping[str, str] | None = None,
    ) -> str:
        self._load()
        shell, scratch = "", None
        if self._shell_integration:
            # A bare shell is started with its commands marked, so the UI can
            # show who ran what; anything else starts exactly as asked.
            prepared = prepare_shell(command or default_shell(), env)
            if prepared.shell:
                command, env = prepared.command, prepared.env
                shell, scratch = prepared.shell, prepared.scratch
        try:
            child = self._session_factory(
                cwd=cwd, command=command, rows=rows, cols=cols, env=env
            )
        except Exception:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
            raise
        screen = self._pyte.Screen(cols, rows)
        stream = self._pyte.Stream(screen)
        session_id = str(uuid4())
        session = TerminalSession(session_id, child, screen, stream, cwd, rows, cols)
        session.shell = shell
        session.scratch = scratch
        self._sessions[session_id] = session
        self._drain(session_id)
        return session_id

    def send_text(self, session_id: str, text: str, *, author: str = "agent") -> None:
        """Type ``text``. ``author`` is who the command belongs to: the agent's
        tools by default, "user" for what the user typed in a UI."""
        session = self._get(session_id)
        # Whatever the shell said before this keystroke comes first: a prompt
        # still in the buffer decides whether this line opens a command block.
        self._drain(session_id)
        session.integration.typed(text, author)
        session.child.send(text)
        self._drain(session_id)

    def send_enter(self, session_id: str, *, author: str = "agent") -> None:
        session = self._get(session_id)
        self._drain(session_id)
        session.integration.submitted(author)
        session.child.send_line("")
        self._drain(session_id)

    def send_control(self, session_id: str, key: str) -> None:
        session = self._get(session_id)
        session.child.send_control(key)
        self._drain(session_id)

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        session = self._get(session_id)
        session.child.resize(rows, cols)
        session.screen.resize(cols, rows)
        session.rows = rows
        session.cols = cols
        self._drain(session_id)

    def interrupt(self, session_id: str) -> None:
        self.send_control(session_id, "c")

    def terminate(self, session_id: str) -> None:
        """End a session. Ending one that already ended is not a failure."""
        session = self._sessions.get(session_id)
        if session is None:
            raise TerminalSessionError(f"Unknown terminal session: {session_id}")
        if session.closed:
            return
        session.child.terminate()
        session.closed = True
        session.integration.closed()
        if session.scratch is not None:
            shutil.rmtree(session.scratch, ignore_errors=True)
            session.scratch = None

    def read_screen(self, session_id: str, *, include_cursor: bool = True) -> dict[str, object]:
        # Readable after the session ends: the payload carries `closed`, which
        # would be a field that can only ever say False otherwise - and the one
        # read that matters most is the one right after terminating, which is
        # how the UI learns to stop drawing the session as live.
        session = self._get(session_id, allow_closed=True)
        self._drain(session_id)
        display = "\n".join(session.screen.display)
        closed = session.closed or not session.child.is_alive()
        if closed and session.integration.running is not None:
            session.integration.closed()
        payload: dict[str, object] = {
            "session_id": session_id,
            "rows": session.rows,
            "columns": session.cols,
            "screen": display.rstrip(),
            "closed": closed,
            "shell": session.shell,
            **session.integration.payload(),
        }
        if include_cursor:
            payload["cursor"] = {"x": session.screen.cursor.x, "y": session.screen.cursor.y}
        return payload

    def poll(self) -> list[dict[str, object]]:
        """Drain every live session; return the screens that changed since last poll.

        Interactive sessions keep producing output on their own (dev servers,
        long builds), so the application polls this periodically and emits a
        ``terminal.screen.updated`` for each changed screen — the UI shows the
        terminal live instead of only at tool-call boundaries.
        """
        changed: list[dict[str, object]] = []
        for session_id, session in list(self._sessions.items()):
            if session.closed:
                continue
            snapshot = self.read_screen(session_id)
            fingerprint = (
                f"{snapshot['screen']}\x00{snapshot['closed']}\x00{session.integration.revision}"
            )
            if fingerprint != session.last_display:
                session.last_display = fingerprint
                changed.append(snapshot)
        return changed

    def latest_session_id(self) -> str | None:
        """Id of the most recently created session still open, if any."""
        for session_id in reversed(list(self._sessions)):
            if not self._sessions[session_id].closed:
                return session_id
        return None

    def close_all(self) -> None:
        for session_id in list(self._sessions):
            try:
                self.terminate(session_id)
            except TerminalSessionError:
                pass

    def _get(self, session_id: str, *, allow_closed: bool = False) -> TerminalSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise TerminalSessionError(f"Unknown terminal session: {session_id}")
        if session.closed and not allow_closed:
            raise TerminalSessionError(f"Terminal session is closed: {session_id}")
        return session

    def _drain(self, session_id: str) -> None:
        session = self._sessions[session_id]
        while True:
            try:
                data = session.child.read_nonblocking(4096)
            except Exception:
                return
            if not data:
                return
            session.stream.feed(session.integration.feed(data))
