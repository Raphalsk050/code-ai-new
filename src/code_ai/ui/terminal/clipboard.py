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


def _resolve_command() -> tuple[str, ...] | None:
    if sys.platform == "darwin":
        return ("pbcopy",)
    if sys.platform == "win32":
        return ("clip",)
    for candidate in _LINUX_CANDIDATES:
        if shutil.which(candidate[0]):
            return candidate
    return None
