from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from code_ai.core.errors import ToolArgumentError

# pyautogui drives mouse/keyboard/screen across platforms. It is an optional
# dependency: when it is missing the controller degrades to the native macOS
# utilities it can reach (``screencapture``/``open``/``osascript``) and reports
# a clear, actionable error for the capabilities that genuinely need it.
try:  # pragma: no cover - import guard exercised by environment, not tests
    import pyautogui as _pyautogui

    # An agent that drives the pointer should not abort the whole turn just
    # because the cursor grazed a screen corner; the user keeps a manual brake
    # via the dedicated interrupt path instead of an accidental tripwire.
    _pyautogui.FAILSAFE = False
    _pyautogui.PAUSE = 0.0
except Exception:  # noqa: BLE001 - any import failure means "no backend"
    _pyautogui = None


_INSTALL_HINT = (
    "Mouse and keyboard control require the optional 'pyautogui' backend. "
    "Install it with: pip install 'code-ai[desktop]' (or 'pip install pyautogui')."
)

_VALID_BUTTONS = frozenset({"left", "middle", "right"})


@dataclass(slots=True)
class DesktopController:
    """Resolves a desktop-automation backend and exposes blocking primitives.

    Every method here is synchronous and blocking; tools call them through
    :meth:`run` so the event loop stays responsive. State is intentionally
    minimal — the backend (pyautogui) owns the real OS handles — so a single
    instance can be shared across the whole session like the terminal manager.
    """

    @property
    def has_pointer_backend(self) -> bool:
        return _pyautogui is not None

    async def run(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking backend call off the event loop."""

        return await asyncio.to_thread(func, *args, **kwargs)

    # -- introspection ----------------------------------------------------

    def screen_size(self) -> tuple[int, int]:
        backend = self._pointer_backend()
        size = backend.size()
        return int(size[0]), int(size[1])

    def cursor_position(self) -> tuple[int, int]:
        backend = self._pointer_backend()
        point = backend.position()
        return int(point[0]), int(point[1])

    # -- mouse ------------------------------------------------------------

    def move_mouse(self, x: int, y: int, duration: float) -> tuple[int, int]:
        backend = self._pointer_backend()
        backend.moveTo(self._coord(x), self._coord(y), duration=max(0.0, duration))
        return self.cursor_position()

    def click_mouse(
        self, x: int | None, y: int | None, button: str, clicks: int, interval: float
    ) -> tuple[int, int]:
        backend = self._pointer_backend()
        button = self._button(button)
        clicks = max(1, int(clicks))
        kwargs: dict[str, Any] = {
            "button": button,
            "clicks": clicks,
            "interval": max(0.0, interval),
        }
        if x is not None and y is not None:
            kwargs["x"] = self._coord(x)
            kwargs["y"] = self._coord(y)
        backend.click(**kwargs)
        return self.cursor_position()

    def drag_mouse(
        self,
        start_x: int | None,
        start_y: int | None,
        end_x: int,
        end_y: int,
        button: str,
        duration: float,
    ) -> tuple[int, int]:
        backend = self._pointer_backend()
        button = self._button(button)
        if start_x is not None and start_y is not None:
            backend.moveTo(self._coord(start_x), self._coord(start_y))
        backend.dragTo(
            self._coord(end_x),
            self._coord(end_y),
            duration=max(0.0, duration),
            button=button,
        )
        return self.cursor_position()

    def scroll_mouse(self, amount: int, x: int | None, y: int | None) -> tuple[int, int]:
        backend = self._pointer_backend()
        if x is not None and y is not None:
            backend.moveTo(self._coord(x), self._coord(y))
        backend.scroll(int(amount))
        return self.cursor_position()

    # -- keyboard ---------------------------------------------------------

    def type_text(self, text: str, interval: float) -> None:
        backend = self._pointer_backend()
        backend.write(text, interval=max(0.0, interval))

    def press_keys(self, keys: list[str], hold: bool) -> None:
        backend = self._pointer_backend()
        normalized = [self._key(key) for key in keys]
        if hold:
            # Chord: every key held simultaneously then released in reverse.
            backend.hotkey(*normalized)
        else:
            for key in normalized:
                backend.press(key)

    # -- applications -----------------------------------------------------

    def open_application(self, name: str, *, background: bool) -> dict[str, Any]:
        system = platform.system()
        if system == "Darwin":
            argv = ["open"]
            if background:
                argv.append("-g")
            # ``-a`` resolves by app name; a path or bundle id also works via open.
            argv += (["-a", name] if not name.startswith("/") else [name])
            return self._run_open(argv, name)
        if system == "Windows":  # pragma: no cover - platform specific
            return self._run_open(["cmd", "/c", "start", "", name], name)
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:  # pragma: no cover - platform specific
            raise ToolArgumentError("No application opener available on this platform.")
        return self._run_open([opener, name], name)

    def activate_application(self, name: str) -> dict[str, Any]:
        if platform.system() != "Darwin":
            # Activation without a window server handle is macOS-specific here;
            # opening the app foregrounds it on the other platforms anyway.
            return self.open_application(name, background=False)
        script = f'tell application "{self._applescript_quote(name)}" to activate'
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise ToolArgumentError(
                f"Could not activate {name!r}: {result.stderr.strip() or 'unknown error'}"
            )
        return {"application": name, "status": "activated"}

    def list_applications(self) -> list[str]:
        if platform.system() != "Darwin":
            raise ToolArgumentError(
                "Listing visible applications is only supported on macOS."
            )
        script = (
            'tell application "System Events" to get name of '
            "(every process whose background only is false)"
        )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise ToolArgumentError(
                f"Could not list applications: {result.stderr.strip() or 'unknown error'}"
            )
        return [item.strip() for item in result.stdout.split(",") if item.strip()]

    # -- helpers ----------------------------------------------------------

    def _pointer_backend(self) -> Any:
        if _pyautogui is None:
            raise ToolArgumentError(_INSTALL_HINT)
        return _pyautogui

    @staticmethod
    def _coord(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ToolArgumentError("Coordinates must be integers.") from None

    @staticmethod
    def _button(button: Any) -> str:
        button = str(button or "left").lower()
        if button not in _VALID_BUTTONS:
            raise ToolArgumentError(
                f"button must be one of {sorted(_VALID_BUTTONS)}, got {button!r}."
            )
        return button

    @staticmethod
    def _key(key: Any) -> str:
        key = str(key or "").strip()
        if not key:
            raise ToolArgumentError("Key names must be non-empty strings.")
        return key.lower()

    @staticmethod
    def _applescript_quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _run_open(self, argv: list[str], name: str) -> dict[str, Any]:
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode != 0:
            raise ToolArgumentError(
                f"Could not open {name!r}: {result.stderr.strip() or 'unknown error'}"
            )
        # Give the launched app a beat to register so a follow-up click lands on
        # the new window rather than the previous foreground app.
        time.sleep(0.2)
        return {"application": name, "status": "launched"}
