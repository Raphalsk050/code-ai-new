"""Where other agents keep their files, relative to this machine and workspace."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Relocates every foreign location under the user's home, and switches off the
# machine-wide ones. Tests point it at a temp dir so a developer's own setup can
# never leak into a run.
HOME_ENV = "CODE_AI_INTEROP_HOME"


def home() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override).expanduser() if override else Path.home()


def redirected() -> bool:
    return bool(os.environ.get(HOME_ENV))


def config_home() -> Path:
    """``$XDG_CONFIG_HOME`` or ``~/.config``; OpenCode uses it on every platform."""

    xdg = "" if redirected() else os.environ.get("XDG_CONFIG_HOME", "")
    return Path(xdg).expanduser() if xdg else home() / ".config"


def app_data() -> Path:
    """Where desktop apps (VS Code, Claude Desktop) keep their settings."""

    if sys.platform == "win32":
        appdata = "" if redirected() else os.environ.get("APPDATA", "")
        return Path(appdata) if appdata else home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return home() / "Library" / "Application Support"
    return config_home()


def machine_file(windows: str, macos: str, linux: str) -> Path | None:
    """A file IT deploys for every user of the machine; none while redirected,
    or where the platform's entry is empty."""

    if redirected():
        return None
    if sys.platform == "win32":
        chosen = windows
    else:
        chosen = macos if sys.platform == "darwin" else linux
    # Path("") is the current directory, never what was meant.
    return Path(chosen) if chosen else None


def env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def project_directories(workspace: Path | str) -> list[Path]:
    """The repository root down to the workspace, outermost first.

    Instruction files above the workspace still govern it in a monorepo, but
    nothing above the repository belongs to it. Outside a repository it is the
    workspace alone.
    """

    resolved = Path(workspace).expanduser().resolve()
    chain = [resolved]
    current = resolved
    while not (current / ".git").exists():
        if current.parent == current:
            return [resolved]
        current = current.parent
        chain.append(current)
    return chain[::-1]


def display_dir(directory: Path, root: Path | None = None) -> str:
    """How a directory is named in the prompt: "" for the repository root,
    ``packages/web`` below it, ``~/.codex`` under the home directory."""

    if root is not None:
        try:
            relative = directory.relative_to(root)
            return "" if relative == Path(".") else relative.as_posix()
        except ValueError:
            pass
    try:
        return "~/" + directory.relative_to(home()).as_posix()
    except ValueError:
        return directory.as_posix()
