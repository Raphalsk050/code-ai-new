"""The avatar loose on the desktop, outside the editor and the terminal.

Off unless ``desktop_avatar`` is on. A front end attaches it to its
application, and from then on the session's events drive it: the VS Code
panel and the terminal UI get the same one, because neither drives it.

The window is a process of its own, showing assets/desktop_avatar/avatar.html:
code-ai-avatar (desktop-avatar/, in Rust) on Windows and Linux, cocoa.py on
macOS. It takes one JSON object a line on stdin and closes when stdin does, so
it never outlives the backend. One desktop shows one: the first Code-AI to
take the lock has it, and another takes over once that one is gone.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from code_ai.desktop_avatar.mood import Mood

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "desktop_avatar"
PAGE = ASSETS / "avatar.html"
HOST_NAME = "code-ai-avatar.exe" if sys.platform == "win32" else "code-ai-avatar"
# Where a source checkout builds it: cargo build --release in desktop-avatar/.
_BUILT_HOST = (
    Path(__file__).resolve().parents[3] / "desktop-avatar" / "target" / "release" / HOST_NAME
)
HOST_ENV = "CODE_AI_AVATAR_HOST"

# Another Code-AI has the desktop: asked again on an event, at most this often.
_LOCK_RETRY_S = 5.0
# A host gone this soon never came up: it is not started again until asked.
_STARTUP_S = 3.0
# How long switching it on waits to see it come up: a missing library ends it at once.
_SETTLE_S = 0.5
# Windows' STATUS_DLL_NOT_FOUND, which says nothing on stderr.
_DLL_NOT_FOUND = 0xC0000135


def host_command(page: Path = PAGE) -> list[str] | None:
    """How this platform shows the page, or None when it cannot."""
    if not page.is_file():
        return None
    if sys.platform == "darwin":
        return [sys.executable, "-m", "code_ai.desktop_avatar.cocoa", str(page)]
    override = os.environ.get(HOST_ENV)
    for host in (Path(override) if override else None, ASSETS / HOST_NAME, _BUILT_HOST):
        if host is not None and host.is_file():
            if sys.platform != "win32" and not os.access(host, os.X_OK):
                # A one-file build unpacks it as data, without the executable bit.
                host.chmod(host.stat().st_mode | 0o755)
            return [str(host), str(page)]
    return None


def _state_dir() -> Path:
    from code_ai.config.defaults import default_config_path

    return default_config_path().parent


class _DesktopLock:
    """Whether this process has the desktop. The OS lets go when it dies."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    def take(self) -> bool:
        if self._file is not None:
            return True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self._path, "a+b")  # noqa: SIM115 - held for as long as the avatar shows
        except OSError:
            return True  # Nowhere to keep a lock: better two avatars than none.
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._file.close()
        self._file = None


class DesktopAvatar:
    """The desktop avatar of one application: shown, told and closed from here."""

    def __init__(
        self,
        config: Callable[[], Any],
        *,
        state_dir: Path | None = None,
        command: Callable[[], list[str] | None] = host_command,
    ) -> None:
        # Read on each use: the session may be rebuilt with another config.
        self._settings = config
        self._dir = state_dir
        self._command = command
        self._mood = Mood()
        self._process: subprocess.Popen[str] | None = None
        self._lock: _DesktopLock | None = None
        self._attached = False
        self._started_at = 0.0
        self._next_try = 0.0
        # Set when the host could not come up: not tried again until switched on again.
        self.problem: str | None = None
        self._errors: deque[str] = deque(maxlen=20)
        self._write = threading.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def attach(self, application: Any) -> None:
        """Follow ``application``'s events, and show it now if it is switched on."""
        if self._attached:
            return
        self._attached = True
        application.subscribe(self._on_event)
        if self._settings().desktop_avatar:
            self.show()

    def set_enabled(self, enabled: bool) -> str | None:
        """Switched on or off from the settings. Says why it cannot show, if it cannot."""
        if not enabled:
            self.hide()
            return None
        self.problem = None
        if not self._attached:
            # Nothing to show it from yet; whether it could be shown is known already.
            return None if self._command() else self._missing()
        problem = self.show()
        if problem or self._process is None:
            return problem
        try:
            self._process.wait(timeout=_SETTLE_S)
        except subprocess.TimeoutExpired:
            return None  # up and running
        self._gone()
        return self.problem

    def _missing(self) -> str:
        self.problem = "The desktop avatar is not part of this build."
        return self.problem

    def show(self) -> str | None:
        if self.running:
            return None
        command = self._command()
        if command is None:
            return self._missing()
        if self._lock is None:
            self._lock = _DesktopLock(self._path("desktop-avatar.lock"))
        if not self._lock.take():
            # Another Code-AI has it; this one asks again later.
            self._next_try = time.monotonic() + _LOCK_RETRY_S
            return None
        # Where the web view keeps its profile: the host's own folder may not be writable.
        command = [*command, str(self._path("desktop-avatar-webview"))]
        try:
            self._process = subprocess.Popen(  # noqa: S603 - our own host, by path
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self._lock.release()
            self.problem = f"The desktop avatar did not start: {exc}"
            logger.warning(self.problem)
            return self.problem
        self._started_at = time.monotonic()
        self._errors.clear()
        process = self._process
        for reader in (self._read, self._read_errors):
            threading.Thread(target=reader, args=(process,), daemon=True).start()
        language = str(getattr(self._settings(), "language", "en"))
        self._send({"at": self._saved_place(), "lang": language})
        self._send({"pose": self._mood.pose, "waiting": self._mood.waiting})
        return None

    def hide(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()  # the host's cue to go
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        if self._lock is not None:
            self._lock.release()

    close = hide

    def _on_event(self, envelope: Any) -> None:
        messages = self._mood.feed(envelope.event_type, envelope.payload or {})
        if not self._settings().desktop_avatar or self.problem:
            return
        if not self.running:
            self._gone()
            if self.problem or time.monotonic() < self._next_try:
                return
            self.show()
            return
        for message in messages:
            self._send(message)

    def _gone(self) -> None:
        """The host went away on its own: say why, once, and give up the desktop."""
        process = self._process
        if process is None:
            return
        self._process = None
        if self._lock is not None:
            self._lock.release()
        if time.monotonic() - self._started_at < _STARTUP_S or process.returncode:
            # What it said on the way out, if the reader has it yet.
            time.sleep(0.1)
            code = process.returncode
            if code is not None and code & 0xFFFFFFFF == _DLL_NOT_FOUND:
                said = "a DLL it needs is missing"
            else:
                said = f"exit code {code}"
            detail = " ".join(self._errors).strip() or said
            self.problem = f"The desktop avatar closed: {detail}"
            logger.warning(self.problem)

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            return
        with self._write:
            try:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                pass  # Gone: the next event finds out why.

    def _read(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and isinstance(message.get("rest"), dict):
                self._save_place(message["rest"])

    def _read_errors(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            if line.strip():
                self._errors.append(line.strip())

    def _path(self, name: str) -> Path:
        return (self._dir or _state_dir()) / name

    def _saved_place(self) -> dict[str, Any] | None:
        try:
            saved = json.loads(self._path("desktop-avatar.json").read_text(encoding="utf-8"))
            place = saved.get("at")
        except (OSError, ValueError, AttributeError):
            return None
        return place if isinstance(place, dict) else None

    def _save_place(self, place: dict[str, Any]) -> None:
        try:
            text = json.dumps({"at": place})
            self._path("desktop-avatar.json").write_text(text, encoding="utf-8")
        except OSError:
            pass
