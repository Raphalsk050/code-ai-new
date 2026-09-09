"""Grabbing a picture of the desktop, on whatever the session happens to be.

There is no portable way to do this. macOS has ``screencapture``, Windows has
GDI through pyautogui, and Linux has neither: the screen belongs to the display
server, and a Wayland compositor will not let an ordinary process read it at
all. So the strategy is the clipboard module's, for the same reason - being on
PATH says nothing about whether a tool works here:

  * order the candidates by what the session actually is (Wayland first under
    Wayland, X11 first under X11), because ``grim`` is routinely installed on
    X11 desktops as a dependency and exits complaining about the display,
  * try each installed one and fall through on runtime failure rather than on
    absence, and
  * when they all fail, say which ones were tried and what to install, since
    "screenshot failed" is not something the caller can act on.
"""

from __future__ import annotations

import base64
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from code_ai.core.errors import ToolExecutionError

# A screenshot that takes longer than this is a tool waiting on a portal
# permission dialog nobody is going to answer, or a compositor that stopped
# responding. Either way the turn must not hang on it.
_TIMEOUT_SECONDS = 20.0

# Longest edge the capture is reduced to before it is sent, matching the
# clipboard paste path: a 4K screenshot is millions of base64 characters on
# every later request in the conversation, and a vision model reads no more
# detail from it than from this.
MAX_IMAGE_EDGE_PX = 1568


@dataclass(frozen=True, slots=True)
class CaptureBackend:
    """One way to get a PNG onto disk, and what to say when it is missing."""

    name: str
    argv: tuple[str, ...]
    # Where the output path goes. Backends that write to stdout use STDOUT.
    output: str = "argument"
    install: str = ""

    @property
    def available(self) -> bool:
        return shutil.which(self.argv[0]) is not None


STDOUT = "stdout"

# -- Linux ----------------------------------------------------------------- #
# grim is the wlroots screenshooter (Sway, Hyprland, river). The desktop
# environments each ship their own, and on GNOME and KDE those are the only
# thing the compositor will talk to, so they are not interchangeable.
_WAYLAND_BACKENDS = (
    CaptureBackend("grim", ("grim", "-"), STDOUT, "sudo apt install grim"),
    CaptureBackend(
        "spectacle",
        ("spectacle", "-b", "-n", "-f", "-o"),
        install="KDE's screenshot tool (package: kde-spectacle)",
    ),
    CaptureBackend(
        "gnome-screenshot",
        ("gnome-screenshot", "-f"),
        install="sudo apt install gnome-screenshot",
    ),
)
# X11. maim and scrot are the small dedicated tools; ImageMagick's import is
# almost always already there because something else pulled it in.
_X11_BACKENDS = (
    CaptureBackend("maim", ("maim",), STDOUT, "sudo apt install maim"),
    CaptureBackend("scrot", ("scrot", "-o"), install="sudo apt install scrot"),
    CaptureBackend(
        "import",
        ("import", "-window", "root"),
        install="sudo apt install imagemagick",
    ),
)

_MACOS_BACKENDS = (
    # -x is "no shutter sound": the agent takes these continuously.
    CaptureBackend("screencapture", ("screencapture", "-x"), install="built in to macOS"),
)


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )


def _has_display() -> bool:
    return bool(
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("DISPLAY")
        or os.environ.get("XDG_SESSION_TYPE", "").lower() in {"wayland", "x11"}
    )


def linux_backends() -> tuple[CaptureBackend, ...]:
    """Every Linux backend, the ones matching this session type first.

    Both families are kept even when the session type is known: XWayland runs
    X11 tools under Wayland, and a mislabelled XDG_SESSION_TYPE should cost
    ordering rather than the whole capability.
    """

    if _is_wayland():
        return _WAYLAND_BACKENDS + _X11_BACKENDS
    return _X11_BACKENDS + _WAYLAND_BACKENDS


def backends_for(system: str) -> tuple[CaptureBackend, ...]:
    if system == "Darwin":
        return _MACOS_BACKENDS
    if system == "Windows":
        return ()  # pyautogui/Pillow handles Windows; no CLI tool needed.
    return linux_backends()


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    # stdin is closed so a backend that decides to prompt (spectacle can, on a
    # first run) fails immediately instead of blocking until the timeout.
    return subprocess.run(
        argv,
        capture_output=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _try_backend(backend: CaptureBackend, destination: Path) -> bytes | None:
    """Run one backend. Bytes on success, None when this one cannot do it."""

    argv = list(backend.argv)
    if backend.output == STDOUT:
        try:
            result = _run(argv)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not result.stdout:
            return None
        return result.stdout
    argv.append(str(destination))
    try:
        result = _run(argv)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = destination.read_bytes()
    except OSError:
        return None
    # A tool can exit 0 and still write nothing when the compositor refuses it.
    return data or None


def _pillow_capture() -> bytes | None:
    """Pillow's own grabber, which is what covers Windows and macOS Quartz."""

    try:
        from PIL import ImageGrab
    except Exception:  # noqa: BLE001 - Pillow is optional
        return None
    try:
        image = ImageGrab.grab()
    except Exception:  # noqa: BLE001 - no display, or an unsupported platform
        return None
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def downscale_png(data: bytes, max_edge: int = MAX_IMAGE_EDGE_PX) -> bytes:
    """Shrink a capture to ``max_edge`` on its longest side, if Pillow is here.

    Best effort on purpose. Without Pillow the full-size capture is still worth
    far more than no capture; it only costs context, and the caller is told the
    size either way.
    """

    try:
        import io

        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow is optional
        return data
    try:
        image = Image.open(io.BytesIO(data))
        if max(image.size) <= max_edge:
            return data
        scale = max_edge / max(image.size)
        resized = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS,
        )
        buffer = io.BytesIO()
        resized.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - a capture that will not decode is still a capture
        return data


def _unavailable_message(system: str, backends: tuple[CaptureBackend, ...]) -> str:
    if system not in {"Darwin", "Windows"} and not _has_display():
        return (
            "No desktop session to capture: neither DISPLAY nor WAYLAND_DISPLAY "
            "is set, so this process is not attached to a screen (a container, "
            "an SSH session without X forwarding, or a headless server)."
        )
    installed = [backend.name for backend in backends if backend.available]
    if installed:
        session = "Wayland" if _is_wayland() else "X11"
        return (
            f"Every screenshot backend failed on this {session} session "
            f"(tried: {', '.join(installed)}). On Wayland the compositor has to "
            "permit the capture: GNOME and KDE only answer their own tool "
            "(gnome-screenshot, spectacle), and a portal prompt left unanswered "
            "reads as a failure here."
        )
    hints = "; ".join(f"{backend.name} ({backend.install})" for backend in backends if backend.install)
    return f"No screenshot backend is installed. Install one of: {hints}."


def capture_screen_png(max_edge: int = MAX_IMAGE_EDGE_PX) -> bytes:
    """A PNG of the whole desktop, downscaled. Raises when nothing can do it."""

    system = platform.system()
    backends = backends_for(system)
    # Pillow first on the platforms where it is the native path, and last on
    # Linux, where it goes through X11 and would fail on a Wayland session that
    # a dedicated tool handles fine.
    if system in {"Darwin", "Windows"}:
        data = _pillow_capture()
        if data:
            return downscale_png(data, max_edge)
    with tempfile.TemporaryDirectory(prefix="code-ai-screen-") as directory:
        destination = Path(directory) / "screen.png"
        for backend in backends:
            if not backend.available:
                continue
            data = _try_backend(backend, destination)
            if data:
                return downscale_png(data, max_edge)
            # Each backend gets a clean file: a previous failed run can leave a
            # zero-byte one behind, which the next would read as its own output.
            destination.unlink(missing_ok=True)
    if system not in {"Darwin", "Windows"}:
        data = _pillow_capture()
        if data:
            return downscale_png(data, max_edge)
    raise ToolExecutionError(_unavailable_message(system, backends))


def capture_screen_base64(max_edge: int = MAX_IMAGE_EDGE_PX) -> tuple[str, int]:
    """The desktop as base64 PNG, with the byte count of the raw capture."""

    data = capture_screen_png(max_edge)
    return base64.b64encode(data).decode("ascii"), len(data)
