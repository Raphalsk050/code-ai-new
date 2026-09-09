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
from typing import Any

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
    """Pillow's own grabber, which is what covers Windows and macOS Quartz.

    Every monitor, not just the primary one: a capture of the primary screen
    leaves a window the user dragged onto their second monitor invisible, and
    the agent has no way to tell that is what happened - it sees a desktop with
    nothing on it and concludes the application is not running.
    """

    try:
        from PIL import ImageGrab
    except Exception:  # noqa: BLE001 - Pillow is optional
        return None
    image = None
    try:
        image = ImageGrab.grab(all_screens=True)
    except Exception:  # noqa: BLE001 - all_screens is Windows-only
        try:
            image = ImageGrab.grab()
        except Exception:  # noqa: BLE001 - no display, or unsupported platform
            return None
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(frozen=True, slots=True)
class ScreenGeometry:
    """Where the captured image sits on the desktop, and at what scale.

    Clicking is the reason this exists. The model reads a coordinate off a
    picture that has been shrunk to fit in a request, while the mouse moves in
    real desktop pixels whose origin is not necessarily (0, 0) - a monitor
    placed to the left of the primary one has negative x. Two conversions
    therefore stand between "the button is here in the image" and a click that
    lands on it, and getting either wrong puts the pointer somewhere else
    entirely, on a desktop the agent is allowed to click.
    """

    # Bounds of the whole virtual desktop, in real screen pixels.
    left: int
    top: int
    width: int
    height: int
    # Size of the image actually sent, after downscaling.
    image_width: int
    image_height: int

    @property
    def scale(self) -> float:
        """Image pixels per screen pixel; 1.0 when it was not shrunk."""

        return (self.image_width / self.width) if self.width else 1.0

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Turn a point read off the image into one the mouse can be sent to."""

        scale = self.scale or 1.0
        return (
            int(round(self.left + x / scale)),
            int(round(self.top + y / scale)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen": {
                "left": self.left,
                "top": self.top,
                "width": self.width,
                "height": self.height,
            },
            "image": {"width": self.image_width, "height": self.image_height},
            "scale": round(self.scale, 6),
        }


def _virtual_bounds() -> tuple[int, int, int, int] | None:
    """The desktop's real bounds, origin included. None when unknowable."""

    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(all_screens=True)
    except Exception:  # noqa: BLE001 - not Windows, or no display
        image = None
    if image is not None:
        # Pillow reports the size but not the origin. On Windows the virtual
        # screen's origin comes from the metrics; elsewhere all_screens is not
        # supported and this branch is not reached.
        try:
            import ctypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        except Exception:  # noqa: BLE001 - not Windows
            left, top = 0, 0
        return left, top, image.width, image.height
    try:
        from PIL import ImageGrab

        primary = ImageGrab.grab()
    except Exception:  # noqa: BLE001
        return None
    return 0, 0, primary.width, primary.height


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a PNG, read from its header without decoding it."""

    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


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


def capture_screen_png_raw(
    max_edge: int = MAX_IMAGE_EDGE_PX,
) -> tuple[bytes, tuple[int, int] | None]:
    """The downscaled PNG, plus the size it had before being downscaled.

    The original size is what the desktop's real pixels are counted in, so it
    is carried out rather than recomputed: it is the denominator of the scale a
    click is converted through.
    """

    system = platform.system()
    backends = backends_for(system)

    def finish(data: bytes) -> tuple[bytes, tuple[int, int] | None]:
        return downscale_png(data, max_edge), png_size(data)

    # Pillow first on the platforms where it is the native path, and last on
    # Linux, where it goes through X11 and would fail on a Wayland session that
    # a dedicated tool handles fine.
    if system in {"Darwin", "Windows"}:
        data = _pillow_capture()
        if data:
            return finish(data)
    with tempfile.TemporaryDirectory(prefix="code-ai-screen-") as directory:
        destination = Path(directory) / "screen.png"
        for backend in backends:
            if not backend.available:
                continue
            data = _try_backend(backend, destination)
            if data:
                return finish(data)
            # Each backend gets a clean file: a previous failed run can leave a
            # zero-byte one behind, which the next would read as its own output.
            destination.unlink(missing_ok=True)
    if system not in {"Darwin", "Windows"}:
        data = _pillow_capture()
        if data:
            return finish(data)
    raise ToolExecutionError(_unavailable_message(system, backends))


def capture_screen_png(max_edge: int = MAX_IMAGE_EDGE_PX) -> bytes:
    """A PNG of the whole desktop, downscaled. Raises when nothing can do it."""

    return capture_screen_png_raw(max_edge)[0]


def capture_screen(max_edge: int = MAX_IMAGE_EDGE_PX) -> tuple[str, int, ScreenGeometry]:
    """The desktop as base64 PNG, its byte count, and where its pixels are.

    The geometry is measured from the capture itself rather than asked of the
    display server separately: a backend that captures one monitor and a
    metrics call that reports all of them would disagree, and the disagreement
    would only show up as clicks landing on the wrong window.
    """

    raw = capture_screen_png_raw(max_edge)
    data, full_size = raw
    image_size = png_size(data) or full_size
    bounds = _virtual_bounds()
    if bounds is not None and full_size and bounds[2:] != full_size:
        # The backend captured something other than the whole virtual desktop
        # (one monitor, typically). Trust the capture: it is what the model is
        # looking at, and its origin is the only one the coordinates share.
        bounds = (bounds[0], bounds[1], full_size[0], full_size[1])
    if bounds is None:
        width, height = full_size or image_size
        bounds = (0, 0, width, height)
    geometry = ScreenGeometry(
        left=bounds[0],
        top=bounds[1],
        width=bounds[2],
        height=bounds[3],
        image_width=image_size[0],
        image_height=image_size[1],
    )
    return base64.b64encode(data).decode("ascii"), len(data), geometry


def capture_screen_base64(max_edge: int = MAX_IMAGE_EDGE_PX) -> tuple[str, int]:
    """The desktop as base64 PNG, with the byte count of the raw capture."""

    encoded, size, _ = capture_screen(max_edge)
    return encoded, size
