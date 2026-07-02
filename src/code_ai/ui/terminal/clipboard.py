from __future__ import annotations

import shutil
import subprocess
import sys

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
