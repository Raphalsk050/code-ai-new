"""The environment the agent's commands run in: the user's own, as a new terminal has it.

Commands used to get a whitelist of ten variables. On Windows that left out
TEMP, USERPROFILE, APPDATA and PATHEXT - enough to break npm, pip and most
toolchains - and every JAVA_HOME or ANDROID_HOME a build needs, so the agent
spent its turns rediscovering a machine the user had already set up. A command
now sees what the user's terminal sees, less whatever looks like a secret.

On Windows the registry is read again for what the process never saw: it
inherited its variables when VS Code started, so a tool installed or a variable
created since then was invisible until a restart. What the session already has
keeps its value and its PATH order - a venv activated in the terminal that
launched Code-AI still comes first.
"""

from __future__ import annotations

import functools
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass

# Name parts that mark a variable as a credential. By part, not substring:
# SSH_AUTH_SOCK and KEYBOARD_LAYOUT are not secrets, OPENAI_API_KEY is.
_SECRET_PARTS = frozenset(
    {
        "KEY",
        "APIKEY",
        "SECRET",
        "SECRETS",
        "TOKEN",
        "PASSWORD",
        "PASSWD",
        "PASSPHRASE",
        "CREDENTIAL",
        "CREDENTIALS",
    }
)

# cmd.exe built-ins: there is no dir.exe to run, so these go through cmd.
_CMD_BUILTINS = frozenset(
    {
        "assoc", "call", "cd", "chdir", "cls", "copy", "date", "del", "dir", "echo",
        "erase", "ftype", "md", "mkdir", "mklink", "move", "path", "popd", "pushd",
        "rd", "ren", "rename", "rmdir", "set", "start", "time", "title", "type",
        "ver", "vol",
    }
)  # fmt: skip
# The ones whose arguments are paths.
_PATH_BUILTINS = frozenset(
    {"cd", "chdir", "copy", "del", "dir", "erase", "md", "mkdir", "move", "rd", "ren",
     "rename", "rmdir", "type"}
)  # fmt: skip

# PyInstaller points these at the libraries inside the binary and keeps the
# user's under *_ORIG; a child given the binary's libstdc++ dies on load.
_LIBRARY_PATH_VARS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")

_REGISTRY_KEYS = (
    ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ("HKEY_CURRENT_USER", "Environment"),
)

# What the prompt names when it is on PATH, in the order people reach for it.
_PROBED_TOOLS = (
    "python", "py", "pip", "node", "npm", "java", "git", "gradle", "mvn",
    "dotnet", "go", "cargo", "cmake", "docker", "adb",
)  # fmt: skip
_HOME_SUFFIXES = ("_HOME", "_ROOT", "_SDK")
_HOME_NAMES = frozenset({"VIRTUAL_ENV", "CONDA_PREFIX", "CUDA_PATH", "GOPATH", "NVM_DIR"})
_PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")


def is_secret_name(name: str) -> bool:
    return any(part in _SECRET_PARTS for part in re.split(r"[^A-Za-z0-9]+", name.upper()))


@dataclass(frozen=True, slots=True)
class CommandEnvironment:
    variables: dict[str, str]
    # Present in the user's environment but kept from commands (secret-looking).
    withheld: tuple[str, ...] = ()


def command_environment(
    *, pass_secrets: bool = False, base: Mapping[str, str] | None = None
) -> CommandEnvironment:
    """The user's environment for one command, refreshed and with secrets held back."""

    env = dict(os.environ if base is None else base)
    if os.name == "nt" and base is None:
        env = _with_registry(env, _registry_variables())
    if getattr(sys, "frozen", False):
        for name in _LIBRARY_PATH_VARS:
            original = env.pop(f"{name}_ORIG", None)
            if original is None:
                env.pop(name, None)
            else:
                env[name] = original
    withheld: list[str] = []
    if not pass_secrets:
        for name in list(env):
            if is_secret_name(name):
                withheld.append(name)
                del env[name]
    return CommandEnvironment(env, tuple(sorted(withheld)))


def resolve_argv(argv: list[str], env: Mapping[str, str]) -> list[str]:
    """The argv Windows can start: ``npm`` is ``npm.cmd``, ``dir`` lives in cmd.

    CreateProcess only adds ``.exe`` to a bare name, so every ``.cmd`` and
    ``.bat`` tool (npm, npx, gradle, mvn, yarn) failed with "file not found",
    and cmd's own commands had no file at all. Elsewhere argv is left alone.
    """

    if os.name != "nt" or not argv or os.path.dirname(argv[0]):
        return argv
    name = argv[0].lower()
    # Before PATH: Git's usr\bin carries a dir.exe and an echo.exe that do not
    # take cmd's switches, and on Windows "dir /b" means cmd's.
    if name in _CMD_BUILTINS:
        shell = _get(env, "COMSPEC") or "cmd.exe"
        return [shell, "/d", "/s", "/c", subprocess.list2cmdline(_cmd_arguments(argv))]
    found = shutil.which(argv[0], path=_get(env, "PATH"))
    if found:
        return [found, *argv[1:]]
    return argv


def _cmd_arguments(argv: list[str]) -> list[str]:
    """Unix habits cmd would take literally: ``mkdir -p`` and ``a/b`` paths.

    cmd's mkdir makes the parents anyway and would create a folder named -p;
    and in a path cmd reads "/b" as a switch, so slashes inside one become
    backslashes. A leading slash is left alone: that is how cmd spells a switch.
    """

    name = argv[0].lower()
    arguments = argv[1:]
    if name in ("mkdir", "md"):
        arguments = [arg for arg in arguments if arg not in ("-p", "--parents")]
    if name in _PATH_BUILTINS:
        arguments = [
            arg.replace("/", "\\") if "/" in arg and not arg.startswith("/") else arg
            for arg in arguments
        ]
    return [argv[0], *arguments]


def describe_environment(environment: CommandEnvironment) -> str:
    """What the system prompt says about the machine commands run on. Stable per session."""

    env = environment.variables
    windows = os.name == "nt"
    desktop = windows or sys.platform == "darwin" or bool(
        _get(env, "DISPLAY") or _get(env, "WAYLAND_DISPLAY")
    )
    lines = [
        f"Machine: {_system_name()}. "
        + (
            "You run on the user's own computer: a program that opens a window "
            "opens it on their screen."
            if desktop
            else "There is no display here: a program that needs a window cannot open one."
        )
    ]
    if windows:
        lines.append(
            "execute_command starts a program directly with the user's environment "
            "variables, as a new terminal window has them (a tool installed after this "
            "session started is found too). cmd built-ins (dir, type, copy, del, set, "
            "echo) and .cmd/.bat tools (npm, gradle, mvn) work as typed; for pipes, "
            'redirects or && run "cmd /c ..." or "powershell -NoProfile -Command ...". '
            'start "" <program> launches something and returns at once.'
        )
    else:
        lines.append(
            "execute_command starts a program directly with the user's environment "
            "variables; for pipes, redirects or && run \"bash -lc '...'\"."
        )
    lines.append(
        "A program that keeps running - a game, a window, a server - never returns: "
        "start it in the interactive terminal (start_terminal) and read its screen "
        "instead of waiting for it."
    )
    shell = _get(env, "COMSPEC") if windows else _get(env, "SHELL")
    if shell:
        lines.append(f"Interactive terminal shell: {shell}")
    tools = _tools_on_path(_get(env, "PATH"))
    if tools:
        lines.append("On PATH: " + ", ".join(tools))
    homes = sorted(
        f"{name}={value}"
        for name, value in env.items()
        if (name.upper().endswith(_HOME_SUFFIXES) or name.upper() in _HOME_NAMES)
        and not name.upper().startswith("XDG_")
        and value
        and len(value) < 200
    )
    if homes:
        lines.append("Set in the environment: " + ", ".join(homes[:20]))
    proxies = [name for name in _PROXY_VARS if _get(env, name)]
    if proxies:
        lines.append(f"A proxy is configured ({', '.join(proxies)}); network tools use it.")
    if environment.withheld:
        lines.append(
            "Held back from commands because they look like secrets: "
            + ", ".join(environment.withheld[:20])
            + ". If a command needs one, ask the user to allow it in the settings."
        )
    return "\n".join(lines)


@functools.lru_cache(maxsize=8)
def _tools_on_path(search: str) -> list[str]:
    # A few hundred stat calls: once per PATH value, not once per session built.
    return [
        f"{name} ({path})" for name in _PROBED_TOOLS if (path := shutil.which(name, path=search))
    ]


def _get(env: Mapping[str, str], name: str) -> str:
    """A variable by name, case-insensitively where Windows treats it so."""

    value = env.get(name)
    if value is None and os.name == "nt":
        wanted = name.upper()
        value = next((v for k, v in env.items() if k.upper() == wanted), None)
    return value or ""


def _system_name() -> str:
    if os.name == "nt":
        version = sys.getwindowsversion()
        name = "Windows 11" if version.build >= 22000 else f"Windows {platform.release()}"
        return f"{name} ({platform.machine() or 'x64'})"
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    try:
        release = platform.freedesktop_os_release()
        name = release.get("PRETTY_NAME") or release.get("NAME") or "Linux"
    except OSError:
        name = "Linux"
    return f"{name} ({platform.machine()})"


def _registry_variables() -> tuple[dict[str, str], str, str]:
    """The machine and user variables as the registry holds them now.

    Returns the merged variables (the user's win over the machine's) and the
    two PATHs, which Windows joins rather than overrides.
    """

    import winreg

    merged: dict[str, str] = {}
    paths = {"HKEY_LOCAL_MACHINE": "", "HKEY_CURRENT_USER": ""}
    for hive_name, subkey in _REGISTRY_KEYS:
        try:
            key = winreg.OpenKey(getattr(winreg, hive_name), subkey)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
                    continue
                if name.upper() == "PATH":
                    paths[hive_name] = value
                else:
                    merged[name.upper()] = value
    return merged, paths["HKEY_LOCAL_MACHINE"], paths["HKEY_CURRENT_USER"]


def _with_registry(
    env: dict[str, str], registry: tuple[dict[str, str], str, str]
) -> dict[str, str]:
    """``env`` plus what the registry gained since the process started.

    Only additions: a variable the session has keeps the session's value, and
    PATH keeps the session's order with the registry's new entries after it.
    """

    variables, machine_path, user_path = registry
    fresh = {name.upper(): value for name, value in env.items()}
    for name, value in variables.items():
        if name not in fresh:
            fresh[name] = _expand(value, {**variables, **fresh})
    current = [entry for entry in fresh.get("PATH", "").split(";") if entry.strip()]
    seen = {entry.rstrip("\\").lower() for entry in current}
    for entry in f"{machine_path};{user_path}".split(";"):
        expanded = _expand(entry.strip(), fresh)
        if expanded and expanded.rstrip("\\").lower() not in seen:
            current.append(expanded)
            seen.add(expanded.rstrip("\\").lower())
    fresh["PATH"] = ";".join(current)
    return fresh


def _expand(value: str, env: Mapping[str, str]) -> str:
    """%NAME% references, expanded against ``env`` (unknown ones are left as typed)."""

    if "%" not in value:
        return value
    return re.sub(
        r"%([^%;]+)%",
        lambda match: env.get(match.group(1).upper(), match.group(0)),
        value,
    )
