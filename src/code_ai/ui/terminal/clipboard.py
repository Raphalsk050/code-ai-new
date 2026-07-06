from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# OSC 52 (used by Textual's App.copy_to_clipboard) silently does nothing on
# macOS Terminal.app and any terminal/multiplexer that doesn't pass the escape
# sequence through. Shelling out to the platform clipboard tool is the only
# way that reliably reaches the system clipboard everywhere.

# Clipboard tools answer instantly; a stuck one (xclip waiting on a selection
# owner that stopped responding mid INCR transfer) would freeze the whole UI,
# because paste runs synchronously on the event loop.
_TIMEOUT_SECONDS = 5.0

# On Linux the clipboard belongs to the display server, so being on PATH does
# not mean a tool works: wl-clipboard is often installed on X11 desktops as a
# package dependency and fails with "could not connect to a Wayland display".
# Callers therefore try every installed tool in session order (see
# _linux_ordered) and fall through on runtime failure.
_LINUX_COPY_CANDIDATES = (
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
)
_LINUX_PASTE_CANDIDATES = (
    ("wl-paste", "--no-newline"),
    ("xclip", "-selection", "clipboard", "-o"),
    ("xsel", "--clipboard", "--output"),
)
# Image access needs two commands per tool: one listing the MIME types the
# clipboard owner offers and one fetching a chosen type (appended as the last
# argument). Browsers put copied pictures on the clipboard as image/jpeg or
# image/webp, so asking only for image/png - the rendition macOS and Windows
# converters guarantee - comes back empty. xsel has no MIME support at all.
_LINUX_IMAGE_TOOLS = (
    (("wl-paste", "--list-types"), ("wl-paste", "--type")),
    (
        ("xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"),
        ("xclip", "-selection", "clipboard", "-o", "-t"),
    ),
)

# Formats the vision providers accept, best first.
_IMAGE_MIME_PREFERENCE = ("image/png", "image/jpeg", "image/webp", "image/gif")

_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# Renders the clipboard image (if any) as base64 PNG on stdout. -sta is required
# for Clipboard access; no output simply means "no image on the clipboard".
_WINDOWS_IMAGE_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "Add-Type -AssemblyName System.Drawing; "
    "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
    "if ($img -ne $null) { "
    "$ms = New-Object System.IO.MemoryStream; "
    "$img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); "
    "[Console]::Out.Write([Convert]::ToBase64String($ms.ToArray())) }"
)


def copy_to_system_clipboard(text: str) -> bool:
    """Best-effort copy to the OS clipboard. Returns True on success."""

    for command in _copy_commands():
        # DEVNULL, not PIPE: xclip forks a child that keeps owning the
        # selection and inherits stdio, so a pipe would make run() wait for
        # that child instead of returning once the copy is done.
        try:
            subprocess.run(
                command,
                input=text.encode("utf-8"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception:
            continue
        return True
    return False


def paste_from_system_clipboard() -> str | None:
    """Best-effort read of the OS clipboard. Returns the text, or None on failure.

    The counterpart to :func:`copy_to_system_clipboard`: it shells out to the
    platform's clipboard tool so a value copied anywhere on the machine can be
    pasted into a prompt/field without the terminal's own paste path (which OSC
    52 does not support for reads on most terminals).
    """

    for command in _paste_commands():
        data = _run_capture(command)
        if data:
            return data.decode("utf-8", errors="replace")
    return None


def paste_image_from_system_clipboard() -> tuple[bytes, str] | None:
    """Best-effort read of an image from the OS clipboard.

    Terminals only deliver *text* through the regular paste path, so an image
    on the clipboard (a screenshot, a copied picture) is invisible to bracketed
    paste. Like the text reader above, this shells out to a platform tool to
    fetch the image rendition directly. Returns the raw bytes and their MIME
    type, or None when the clipboard holds no image or no capture tool is
    available.
    """

    if sys.platform == "darwin":
        data = _read_image_darwin()
    elif sys.platform == "win32":
        data = _read_image_windows()
    else:
        data = _read_image_linux()
    if not data:
        return None
    media_type = _sniff_image_type(data)
    if media_type is None:
        return None
    return data, media_type


def linux_clipboard_packages() -> tuple[str, ...]:
    """System packages that would make the clipboard reachable.

    Empty when a clipboard tool is already installed or this isn't Linux -
    i.e. when there is nothing to tell the user to install.
    """

    if sys.platform in ("darwin", "win32"):
        return ()
    if any(shutil.which(name) for name in ("wl-paste", "xclip", "xsel")):
        return ()
    if os.environ.get("WAYLAND_DISPLAY"):
        return ("wl-clipboard",)
    if os.environ.get("DISPLAY"):
        return ("xclip",)
    return ("wl-clipboard", "xclip")


def _sniff_image_type(data: bytes) -> str | None:
    """MIME type derived from the payload's magic bytes, or None.

    Sniffing (rather than trusting the tool's claimed type) also rejects text
    accidentally served on the image path, e.g. xclip echoing a selection.
    """

    for magic, media_type in _IMAGE_SIGNATURES:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _read_image_darwin() -> bytes | None:
    if shutil.which("pngpaste"):
        return _run_capture(("pngpaste", "-"))
    # Without pngpaste, ask AppleScript to write the clipboard's PNG rendition
    # to a temp file. The coercion inside `try` fails silently when the
    # clipboard holds no image, leaving the file empty.
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "clipboard.png"
        script = (
            f'set outFile to (open for access POSIX file "{target}" with write permission)\n'
            "try\n"
            "write (the clipboard as «class PNGf») to outFile\n"
            "end try\n"
            "close access outFile"
        )
        try:
            subprocess.run(
                ("osascript", "-e", script),
                capture_output=True,
                check=True,
                timeout=_TIMEOUT_SECONDS,
            )
            data = target.read_bytes()
        except Exception:
            return None
    return data or None


def _read_image_linux() -> bytes | None:
    for list_command, fetch_command in _linux_ordered(_LINUX_IMAGE_TOOLS):
        offered = _run_capture(list_command)
        if offered is None:
            continue
        types = offered.decode("utf-8", errors="replace").split()
        for mime in _IMAGE_MIME_PREFERENCE:
            if mime not in types:
                continue
            data = _run_capture(fetch_command + (mime,))
            if data:
                return data
    return None


def _read_image_windows() -> bytes | None:
    result = _run_capture(("powershell", "-noprofile", "-sta", "-command", _WINDOWS_IMAGE_SCRIPT))
    if not result:
        return None
    encoded = result.decode("ascii", errors="ignore").strip()
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded)
    except Exception:
        return None


def _run_capture(command: tuple[str, ...]) -> bytes | None:
    """Run a read command; its stdout, or None on any failure/empty output."""

    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=_TIMEOUT_SECONDS)
    except Exception:
        return None
    return result.stdout or None


def _copy_commands() -> list[tuple[str, ...]]:
    if sys.platform == "darwin":
        return [("pbcopy",)]
    if sys.platform == "win32":
        return [("clip",)]
    return _linux_ordered(_LINUX_COPY_CANDIDATES)


def _paste_commands() -> list[tuple[str, ...]]:
    if sys.platform == "darwin":
        return [("pbpaste",)]
    if sys.platform == "win32":
        return [("powershell", "-noprofile", "-command", "Get-Clipboard")]
    return _linux_ordered(_LINUX_PASTE_CANDIDATES)


def _linux_ordered(candidates: tuple) -> list:
    """Installed candidates, reordered to match the current session.

    Under a Wayland compositor (WAYLAND_DISPLAY set) the wl-clipboard tools go
    first; on X11 the xclip/xsel family does. The other family stays as a
    fallback rather than being dropped - hybrid sessions (XWayland) exist, and
    a tool that can't connect just fails fast and yields to the next one.
    """

    wayland_first = bool(os.environ.get("WAYLAND_DISPLAY"))

    def binary(candidate: tuple) -> str:
        head = candidate[0]
        # Image candidates are (list_command, fetch_command) pairs; the rest
        # are plain argv tuples. Either way argv[0] names the executable.
        return head[0] if isinstance(head, tuple) else head

    def is_wayland(candidate: tuple) -> bool:
        return binary(candidate).startswith("wl-")

    ordered = sorted(candidates, key=lambda c: is_wayland(c) != wayland_first)
    return [c for c in ordered if shutil.which(binary(c))]
