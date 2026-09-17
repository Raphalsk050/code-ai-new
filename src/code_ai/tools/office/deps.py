"""Third-party libraries the office-style tools need, checked when a tool runs, never at startup.

A missing or broken library must cost one tool call, not the whole app: every
module that imports one is reached through ``LazyModule`` or after ``ensure``.
Running from source, what is missing is pip-installed (trusting the PyPI hosts
unless ssl_verification is on); a frozen binary has no pip, so it says so.

"import docx" is also satisfied by the PyPI package ``docx``, a Python 2 library
that dies with "No module named 'exceptions'". It is uninstalled first.
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import threading
from typing import Any

from code_ai.core.errors import ToolExecutionError

PACKAGES = {
    "docx": "python-docx",
    "pptx": "python-pptx",
    "pypdf": "pypdf",
    "pypdfium2": "pypdfium2",
    "reportlab": "reportlab",
    "PIL": "pillow",
    "lxml": "lxml",
    "markdown_it": "markdown-it-py",
    "cryptography": "cryptography",
}
# Distributions that install the same top-level module under the wrong code.
CONFLICTS = {"docx": ("docx",), "pptx": ("pptx",)}
_PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")
INSTALL_TIMEOUT_S = 900

_lock = threading.Lock()
_attempted: set[str] = set()


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def problem(module: str) -> str | None:
    """None when ``module`` imports and looks right; otherwise why it does not."""

    try:
        loaded = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - a broken install fails in many ways
        _forget(module)
        return f"{type(exc).__name__}: {exc}"
    if module == "docx" and not hasattr(loaded, "Document"):
        return "the 'docx' module is not python-docx"
    if module == "pptx" and not hasattr(loaded, "Presentation"):
        return "the 'pptx' module is not python-pptx"
    return None


def _forget(module: str) -> None:
    # A failed import can leave half-initialised submodules behind.
    for name in [n for n in sys.modules if n == module or n.startswith(module + ".")]:
        sys.modules.pop(name, None)


def ensure_sync(*modules: str, verify_ssl: bool = False) -> None:
    broken = {module: reason for module in modules if (reason := problem(module))}
    if not broken:
        return
    packages = [PACKAGES.get(module, module) for module in broken]
    details = "; ".join(f"{module} ({reason})" for module, reason in broken.items())
    if _frozen():
        raise ToolExecutionError(
            f"This Code-AI binary is missing {', '.join(packages)} ({details}). "
            "Get a newer build: current ones include it."
        )
    with _lock:
        pending = [m for m in broken if m not in _attempted]
        install_output = ""
        if pending:
            _attempted.update(pending)
            install_output = _install(pending, verify_ssl=verify_ssl)
        importlib.invalidate_caches()
        still = {module: reason for module in broken if (reason := problem(module))}
    if still:
        raise ToolExecutionError(
            f"Could not load {', '.join(PACKAGES.get(m, m) for m in still)}: "
            + "; ".join(f"{m} ({r})" for m, r in still.items())
            + (f". pip said: {install_output}" if install_output else "")
            + f". Fix it with: {manual_command(list(still), verify_ssl=verify_ssl)}"
        )


async def ensure(*modules: str, verify_ssl: bool = False) -> None:
    await asyncio.to_thread(ensure_sync, *modules, verify_ssl=verify_ssl)


def _pip(*args: str, verify_ssl: bool) -> list[str]:
    trusted = (
        [] if verify_ssl else [flag for host in _PYPI_HOSTS for flag in ("--trusted-host", host)]
    )
    command = [sys.executable, "-m", "pip", *args]
    return command + trusted if args and args[0] == "install" else command


def manual_command(modules: list[str], *, verify_ssl: bool = False) -> str:
    conflicts = [c for m in modules for c in CONFLICTS.get(m, ())]
    install = " ".join(
        _pip("install", *(PACKAGES.get(m, m) for m in modules), verify_ssl=verify_ssl)
    )
    if conflicts:
        uninstall = " ".join(_pip("uninstall", "-y", *conflicts, verify_ssl=verify_ssl))
        clashing = [PACKAGES[m] for m in modules if m in CONFLICTS]
        reinstall = " ".join(
            _pip("install", "--force-reinstall", "--no-deps", *clashing, verify_ssl=verify_ssl)
        )
        return f"{uninstall} && {reinstall} && {install}"
    return install


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S, check=False
    )


def _install(modules: list[str], *, verify_ssl: bool) -> str:
    conflicts = [c for m in modules for c in CONFLICTS.get(m, ())]
    packages = [PACKAGES.get(m, m) for m in modules]
    try:
        if conflicts:
            _run(_pip("uninstall", "-y", *conflicts, verify_ssl=verify_ssl))
            # Both distributions write the same package directory, so removing the impostor
            # also removed files of the real one, which pip still believes is installed.
            clashing = [PACKAGES[m] for m in modules if m in CONFLICTS]
            reinstall = ("install", "--force-reinstall", "--no-deps", *clashing)
            _run(_pip(*reinstall, verify_ssl=verify_ssl))
        result = _run(_pip("install", *packages, verify_ssl=verify_ssl))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode == 0:
        return ""
    lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    return " | ".join(lines[-4:])


class LazyModule:
    """A module imported on first attribute access: importing a tool never needs its libraries."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._module: Any = None

    def __getattr__(self, attribute: str) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module(self._name)
            except Exception as exc:  # noqa: BLE001 - reported as a tool failure
                raise ToolExecutionError(
                    f"{self._name} could not be loaded ({type(exc).__name__}: {exc})."
                ) from exc
        return getattr(self._module, attribute)
