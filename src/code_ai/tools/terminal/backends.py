"""Platform PTY backends behind one session interface (Strategy).

The manager owns sessions and screen emulation; *how* a real PTY is spawned
and driven differs per platform, so each platform gets its own adapter:

- POSIX: ``pexpect`` (a true fork/exec PTY).
- Windows: ``pywinpty`` (ConPTY), which previously did not exist here — the
  interactive terminal was POSIX-only and failed immediately on Windows.

Both adapters expose the same small surface (:class:`PtySession`), so the
manager and every terminal tool stay platform-agnostic. Imports of the
platform libraries are deferred into the adapters so importing this module
never requires either dependency.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Protocol

from code_ai.core.errors import TerminalSessionError


class PtySession(Protocol):
    """What the manager needs from a live pseudo-terminal, platform-free."""

    def send(self, text: str) -> None: ...

    def send_line(self, text: str) -> None: ...

    def send_control(self, key: str) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def read_nonblocking(self, size: int) -> str:
        """Return pending output, or "" when nothing is buffered. Never blocks."""
        ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...


def default_shell() -> str:
    """The interactive shell to spawn when no explicit command is given."""
    if platform.system() == "Windows":
        return os.environ.get("COMSPEC") or "cmd.exe"
    return os.environ.get("SHELL") or "/bin/sh"


def create_pty_session(
    *, cwd: Path, command: str | None, rows: int, cols: int
) -> PtySession:
    """Spawn a PTY running ``command`` (or the default shell) for this platform."""
    shell = command or default_shell()
    if platform.system() == "Windows":
        return WinptyPtySession.spawn(shell, cwd=cwd, rows=rows, cols=cols)
    return PexpectPtySession.spawn(shell, cwd=cwd, rows=rows, cols=cols)


class PexpectPtySession:
    """POSIX PTY adapter over ``pexpect.spawn``."""

    def __init__(self, child: object) -> None:
        self._child = child

    @classmethod
    def spawn(cls, command: str, *, cwd: Path, rows: int, cols: int) -> PexpectPtySession:
        try:
            import pexpect  # type: ignore
        except Exception as exc:
            raise TerminalSessionError(
                "pexpect is required for persistent terminals on POSIX."
            ) from exc
        child = pexpect.spawn(
            command,
            cwd=str(cwd),
            dimensions=(rows, cols),
            encoding="utf-8",
            timeout=0,
        )
        return cls(child)

    def send(self, text: str) -> None:
        self._child.send(text)

    def send_line(self, text: str) -> None:
        self._child.sendline(text)

    def send_control(self, key: str) -> None:
        self._child.sendcontrol(key)

    def resize(self, rows: int, cols: int) -> None:
        self._child.setwinsize(rows, cols)

    def read_nonblocking(self, size: int) -> str:
        try:
            return self._child.read_nonblocking(size=size, timeout=0) or ""
        except Exception:
            # pexpect raises TIMEOUT when nothing is buffered and EOF when the
            # shell exited; both mean "no data now" for a drain loop.
            return ""

    def is_alive(self) -> bool:
        try:
            return bool(self._child.isalive())
        except Exception:
            return False

    def terminate(self) -> None:
        try:
            self._child.terminate(force=True)
        except Exception:
            pass


class WinptyPtySession:
    """Windows ConPTY adapter over ``pywinpty``'s ``PtyProcess``.

    ``PtyProcess.read`` blocks on an internal socket fed by a reader thread, so
    the non-blocking drain probes that socket with a zero-timeout ``select``
    (it is an AF_INET socket precisely so ``select`` works on Windows) and only
    reads when data is already available.
    """

    def __init__(self, proc: object) -> None:
        self._proc = proc

    @classmethod
    def spawn(cls, command: str, *, cwd: Path, rows: int, cols: int) -> WinptyPtySession:
        try:
            from winpty import PtyProcess  # type: ignore
        except Exception as exc:
            raise TerminalSessionError(
                "pywinpty is required for persistent terminals on Windows."
            ) from exc
        try:
            proc = PtyProcess.spawn(command, cwd=str(cwd), dimensions=(rows, cols))
        except Exception as exc:
            raise TerminalSessionError(f"Could not spawn terminal: {exc}") from exc
        return cls(proc)

    def send(self, text: str) -> None:
        self._proc.write(text)

    def send_line(self, text: str) -> None:
        # "\r" is the Enter key as a terminal emits it; ConPTY's line discipline
        # turns it into command execution for cmd/PowerShell and any REPL.
        self._proc.write(text + "\r")

    def send_control(self, key: str) -> None:
        self._proc.sendcontrol(key)

    def resize(self, rows: int, cols: int) -> None:
        self._proc.setwinsize(rows, cols)

    def read_nonblocking(self, size: int) -> str:
        import select

        try:
            ready, _, _ = select.select([self._proc.fd], [], [], 0)
        except (OSError, ValueError):
            return ""
        if not ready:
            return ""
        try:
            return self._proc.read(size) or ""
        except EOFError:
            return ""
        except Exception:
            return ""

    def is_alive(self) -> bool:
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def terminate(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass
