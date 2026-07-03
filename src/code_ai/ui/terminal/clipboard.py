from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# OSC 52 (used by Textual's App.copy_to_clipboard) silently does nothing on
# macOS Terminal.app and any terminal/multiplexer that doesn't pass the escape
# sequence through. Shelling out to the platform clipboard tool is the only
# way that reliably reaches the system clipboard everywhere.
_LINUX_CANDIDATES = (
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
)
# Read-side counterparts, in the same preference order, for paste.
_LINUX_PASTE_CANDIDATES = (
    ("wl-paste", "--no-newline"),
    ("xclip", "-selection", "clipboard", "-o"),
    ("xsel", "--clipboard", "--output"),
)
# Image readers ask the clipboard for a PNG rendition explicitly (xsel cannot).
_LINUX_IMAGE_CANDIDATES = (
    ("wl-paste", "--type", "image/png"),
    ("xclip", "-selection", "clipboard", "-t", "image/png", "-o"),
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

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

    command = _resolve_command()
    if command is None:
        return False
    try:
        subprocess.run(command, input=text.encode("utf-8"), check=True)
    except Exception:
        return False
    return True


def paste_from_system_clipboard() -> str | None:
    """Best-effort read of the OS clipboard. Returns the text, or None on failure.

    The counterpart to :func:`copy_to_system_clipboard`: it shells out to the
    platform's clipboard tool so a value copied anywhere on the machine can be
    pasted into a prompt/field without the terminal's own paste path (which OSC
    52 does not support for reads on most terminals).
    """

    command = _resolve_paste_command()
    if command is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, check=True)
    except Exception:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def paste_image_from_system_clipboard() -> bytes | None:
    """Best-effort read of an image from the OS clipboard, as PNG bytes.

    Terminals only deliver *text* through the regular paste path, so an image
    on the clipboard (a screenshot, a copied picture) is invisible to bracketed
    paste. Like the text reader above, this shells out to a platform tool to
    fetch the image rendition directly. Returns None when the clipboard holds
    no image or no capture tool is available.
    """

    if sys.platform == "darwin":
        data = _read_image_darwin()
    elif sys.platform == "win32":
        data = _read_image_windows()
    else:
        data = _read_image_linux()
    if data and data.startswith(_PNG_MAGIC):
        return data
    return None


def _read_image_darwin() -> bytes | None:
    if shutil.which("pngpaste"):
        try:
            result = subprocess.run(("pngpaste", "-"), capture_output=True, check=True)
        except Exception:
            return None
        return result.stdout or None
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
            subprocess.run(("osascript", "-e", script), capture_output=True, check=True)
            data = target.read_bytes()
        except Exception:
            return None
    return data or None


def _read_image_linux() -> bytes | None:
    for candidate in _LINUX_IMAGE_CANDIDATES:
        if not shutil.which(candidate[0]):
            continue
        try:
            result = subprocess.run(candidate, capture_output=True, check=True)
        except Exception:
            continue
        if result.stdout:
            return result.stdout
    return None


def _read_image_windows() -> bytes | None:
    try:
        result = subprocess.run(
            ("powershell", "-noprofile", "-sta", "-command", _WINDOWS_IMAGE_SCRIPT),
            capture_output=True,
            check=True,
        )
    except Exception:
        return None
    encoded = result.stdout.decode("ascii", errors="ignore").strip()
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded)
    except Exception:
        return None


def _resolve_command() -> tuple[str, ...] | None:
    if sys.platform == "darwin":
        return ("pbcopy",)
    if sys.platform == "win32":
        return ("clip",)
    for candidate in _LINUX_CANDIDATES:
        if shutil.which(candidate[0]):
            return candidate
    return None


def _resolve_paste_command() -> tuple[str, ...] | None:
    if sys.platform == "darwin":
        return ("pbpaste",)
    if sys.platform == "win32":
        return ("powershell", "-noprofile", "-command", "Get-Clipboard")
    for candidate in _LINUX_PASTE_CANDIDATES:
        if shutil.which(candidate[0]):
            return candidate
    return None
