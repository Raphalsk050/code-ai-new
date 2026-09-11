"""Getting Playwright's browser onto this machine, or saying exactly how.

Up to three things stand between a user and a working browser tool, and each
is installed on first need, at most once per session (see
``BrowserSession._install``): the Playwright package when running from
source - the Code-AI binary carries it, see packaging/code-ai.spec - the
Chromium build it drives, and on Linux the system libraries that build links
against. Chromium never ships inside the binary: it is a few hundred MB,
pinned per Playwright release, and kept in the user's cache
(``%LOCALAPPDATA%\\ms-playwright``, ``~/.cache/ms-playwright``).

The fetching goes through Playwright's own driver - the Node executable and
CLI it bundles - and not through ``python -m playwright`` or a ``playwright``
command on PATH. In a frozen binary there is no Python to run a module with,
``sys.executable`` is Code-AI itself, and nothing was put on PATH. The driver
is there in every case, because it is what Playwright runs to drive a browser.

Every download skips certificate checks unless ``ssl_verification`` is on,
like every other connection Code-AI makes: a network that re-signs TLS with
its own certificate would otherwise fail all of them.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Long enough for a slow link to bring down Chromium and its headless shell (a
# few hundred MB together); short enough that a stalled download does not
# hold the turn forever.
INSTALL_TIMEOUT_S = 900.0

# A failed install says why - proxy, disk, permissions - in its last lines.
_OUTPUT_TAIL_LINES = 12

# A launch error is quoted to the user, and Playwright appends the browser's
# whole log to it. The cause is at the top; the rest is bounded.
_MAX_DETAIL_CHARS = 1_500

_BROWSERS_PATH_ENV = "PLAYWRIGHT_BROWSERS_PATH"

# apt's own switches for a network that re-signs TLS. Most Ubuntu mirrors are
# plain HTTP and never read them; an HTTPS mirror behind the proxy needs them.
_INSECURE_APT = 'Acquire::https::Verify-Peer "false";\nAcquire::https::Verify-Host "false";\n'

# Browsers already on the machine that Playwright can drive when its own build
# cannot be had: offline, behind a proxy, or under a policy that blocks the
# download. Edge first on Windows, where every install ships it.
FALLBACK_CHANNELS: tuple[str, ...] = (
    ("msedge", "chrome") if sys.platform == "win32" else ("chrome", "msedge")
)

MISSING_BROWSER = "missing_browser"
MISSING_DEPS = "missing_deps"
NO_DISPLAY = "no_display"
PROFILE_IN_USE = "profile_in_use"
OTHER = "other"

# Checked in order, and the order matters: the missing-libraries message names
# "playwright install-deps", which would otherwise read as a missing browser
# and send the user off to download one they already have.
_FAILURE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        MISSING_DEPS,
        (
            "missing dependencies",
            "install-deps",
            # What Linux itself says when Playwright did not check first: the
            # browser starts, cannot load a library, and exits at once.
            "error while loading shared libraries",
            "cannot open shared object file",
        ),
    ),
    (NO_DISPLAY, ("xserver", "missing x server", "$display")),
    (
        PROFILE_IN_USE,
        (
            "existing browser session",
            "profile appears to be in use",
            "processsingleton",
            "user data directory is already in use",
        ),
    ),
    (
        MISSING_BROWSER,
        (
            "executable doesn't exist",
            "executable doesn’t exist",
            "is not found at",
            "playwright install",
        ),
    ),
)


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _on_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def _sudo_available() -> bool:
    return shutil.which("sudo") is not None


def python_command(*args: str) -> list[str]:
    """A ``python -m`` command for the interpreter running Code-AI."""

    return [sys.executable or "python", "-m", *args]


# pypi.org serves the index and files.pythonhosted.org the wheels; trusting
# both is what gets pip through a proxy that re-signs TLS with its own
# certificate.
_PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")


def install_package_command(*, verify_ssl: bool) -> list[str]:
    trusted = [] if verify_ssl else [f for host in _PYPI_HOSTS for f in ("--trusted-host", host)]
    return python_command("pip", "install", *trusted, "playwright")


def code_ai_command(*args: str) -> list[str]:
    """How to run this same Code-AI: the binary itself, or its module from source."""

    if _frozen():
        return [sys.executable, *args]
    return python_command("code_ai", *args)


def driver_command(
    *args: str, verify_ssl: bool = True
) -> tuple[list[str], dict[str, str]] | None:
    """Playwright's CLI as its bundled Node runs it, with the environment it needs.

    None when Playwright cannot be imported: that is a missing package, and is
    reported as one.
    """

    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
    except Exception:  # noqa: BLE001 - optional, and private: any failure is "not here"
        return None
    driver = compute_driver_executable()
    # A (node, cli.js) pair in current releases; a single launcher script in
    # older ones.
    base = [str(part) for part in driver] if isinstance(driver, tuple) else [str(driver)]
    env = get_driver_env()
    if not verify_ssl:
        # The download runs in Node, which checks certificates by itself and
        # knows nothing of ssl_verification - so a proxy that re-signs TLS
        # fails every download. This is Node's own switch for that, and it
        # reaches only this process: the driver that browses is started by
        # Playwright from the unchanged environment.
        env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    return [*base, *args], env


def insecure_apt_config() -> Path:
    """A private apt configuration that skips certificate checks. The caller deletes it."""

    handle, name = tempfile.mkstemp(prefix="code-ai-apt-", suffix=".conf")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(_INSECURE_APT)
    return Path(name)


def system_deps_command(
    apt_config: Path | None, *, interactive: bool
) -> tuple[list[str], dict[str, str]] | str:
    """The command that installs Chromium's Linux libraries, or why there is none.

    Playwright elevates with a plain ``sudo``, which asks for a password on a
    terminal - and an install nobody started by hand has no terminal to ask
    on, so it would hang or fail without saying why. The driver is started
    already elevated instead, and as root it skips its own sudo; ``-n`` makes
    sudo fail at once, with a line saying so, rather than ask. ``interactive``
    is for a person at a terminal, who can answer.

    APT_CONFIG crosses sudo, which clears the environment, through ``env``:
    that is the command sudo runs, so no permission to keep variables is
    needed.
    """

    if not _on_linux():
        return "only Linux installs Chromium's system libraries separately"
    found = driver_command("install-deps", "chromium")
    if found is None:
        return "Playwright is not installed, so there is no installer"
    command, env = found
    settings = []
    if apt_config is not None:
        env["APT_CONFIG"] = str(apt_config)
        settings = [f"APT_CONFIG={apt_config}"]
    if _is_root():
        return command, env
    if not _sudo_available():
        return "installing them needs root, and sudo is not installed"
    sudo = ["sudo"] if interactive else ["sudo", "-n"]
    return [*sudo, "env", *settings, *command], env


def prepare_driver() -> None:
    """Make the bundled driver look for browsers where they last, and able to run.

    Call before Playwright starts or installs anything: both read the
    environment when they spawn the driver.
    """

    _pin_browsers_path()
    _restore_execute_bit()


def _pin_browsers_path() -> None:
    """Keep a frozen build's browsers in the user's cache, not its unpack directory.

    Frozen, Playwright defaults PLAYWRIGHT_BROWSERS_PATH to "0": browsers
    inside its own package. A one-file binary unpacks that package to a fresh
    temporary directory on every start and deletes it on exit, so a browser
    downloaded there is gone by the next run - and its installer, which does
    not apply that default, puts the download in the user's cache where the
    launcher then never looks. The default yields to a value already in the
    environment, so setting the ordinary cache path sends both to one place.
    A value the user set is theirs, and stays.
    """

    if not _frozen() or _BROWSERS_PATH_ENV in os.environ:
        return
    os.environ[_BROWSERS_PATH_ENV] = str(default_browsers_path())


def _restore_execute_bit() -> None:
    """Put the execute bit back on the bundled Node if unpacking dropped it.

    A Node that comes out of a one-file binary without the bit fails every
    browser call with a bare "permission denied" naming nothing Code-AI
    controls.
    """

    if sys.platform == "win32":
        return
    found = driver_command()
    if found is None:
        return
    node = Path(found[0][0])
    try:
        mode = node.stat().st_mode
        if not mode & stat.S_IXUSR:
            node.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        return


def default_browsers_path() -> Path:
    """Where Playwright keeps browsers when nothing says otherwise.

    The same rule its driver applies, so a binary and a ``pip install`` on the
    same machine share one download.
    """

    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(home / "Library" / "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(home / ".cache")
    return Path(base) / "ms-playwright"


def display_command(argv: list[str]) -> str:
    """A command the way the user would paste it into their shell."""

    if sys.platform != "win32":
        return shlex.join(argv)
    line = subprocess.list2cmdline(argv)
    # PowerShell, the default Windows shell, reads a quoted path as a string
    # and prints it back; the call operator is what runs it.
    return f"& {line}" if line.startswith('"') else line


def launch_failure_kind(exc: BaseException) -> str:
    """Which of the known ways a browser fails to start this one is."""

    text = str(exc).lower()
    for kind, markers in _FAILURE_MARKERS:
        if any(marker in text for marker in markers):
            return kind
    return OTHER


def package_missing_message(install_output: str = "") -> str:
    if _frozen():
        # No pip inside a binary: the only fix is a build that has it.
        return (
            "This Code-AI binary was built without Playwright, so it cannot drive "
            "a browser. Get a newer build: current ones include it."
        )
    failed = f" Installing it failed:\n{install_output}\n" if install_output else " "
    return (
        "Browser control needs Playwright, which is not installed in the Python "
        f"running Code-AI ({sys.executable}).{failed}Install it and its browser with: "
        f"{display_command(code_ai_command('doctor', 'browser', '--install'))}"
    )


def describe_launch_failure(
    exc: BaseException,
    *,
    channel: str = "",
    profile_dir: Path | None = None,
    install_output: str = "",
) -> str:
    """What stopped the browser from starting, and the one thing that fixes it."""

    kind = launch_failure_kind(exc)
    failed = f" The automatic install failed:\n{install_output}\n" if install_output else " "
    if kind == MISSING_BROWSER and channel:
        return (
            f"The browser set in browser.channel ({channel}) is not installed on "
            "this machine. Install it, or clear browser.channel to use "
            "Playwright's own Chromium."
        )
    if kind == MISSING_BROWSER:
        return (
            "Playwright's Chromium is not installed, and no Chrome or Edge on this "
            f"machine could stand in for it.{failed}Install it with: "
            f"{display_command(code_ai_command('doctor', 'browser', '--install'))}"
        )
    if kind == MISSING_DEPS:
        command = code_ai_command("doctor", "browser", "--install", "--with-deps")
        return (
            "The browser is installed, but this system lacks libraries it needs to "
            f"run.{failed}Install them with: {display_command(command)} - it asks "
            "for your password."
        )
    if kind == PROFILE_IN_USE:
        where = f" ({profile_dir})" if profile_dir is not None else ""
        return (
            f"The browser profile{where} is already open in another browser - "
            "usually another Code-AI session on this workspace, or a browser a "
            "crashed one left running. Close that window and try again."
        )
    return f"Could not start the browser: {_launch_detail(str(exc))}"


def _launch_detail(text: str) -> str:
    """The first line of a launch error, and what the browser itself printed.

    Playwright appends its whole call log, and the browser's own complaint -
    its "[err]" lines - comes after a command line thousands of characters
    long, so cutting from the top loses exactly the cause.
    """

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    said = [line for line in lines[1:] if "[err]" in line][-8:]
    detail = "\n".join([lines[0], *(said or lines[1:][-8:])])
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = detail[:_MAX_DETAIL_CHARS] + "\n...[truncated]"
    return detail


def bundled_browser_present(chromium: Any) -> bool:
    """Whether Playwright's own Chromium build is on disk.

    An answer that cannot be had counts as present: the launch that follows
    reports a missing build in its own words, and the session acts on that.
    """

    try:
        return Path(chromium.executable_path).is_file()
    except Exception:  # noqa: BLE001 - see above
        return True


@dataclass(frozen=True, slots=True)
class InstallResult:
    ok: bool
    # The end of what the installer printed, kept when it failed: that is
    # where it names the cause.
    output: str = ""


async def install_package(
    *, verify_ssl: bool = False, timeout_s: float = INSTALL_TIMEOUT_S
) -> InstallResult:
    """pip-install Playwright into the Python running Code-AI. From source only."""

    return await run_installer(install_package_command(verify_ssl=verify_ssl), timeout_s=timeout_s)


async def install_browser(
    *, verify_ssl: bool = False, timeout_s: float = INSTALL_TIMEOUT_S
) -> InstallResult:
    """Download the Chromium build the installed Playwright pins."""

    prepare_driver()
    found = driver_command("install", "chromium", verify_ssl=verify_ssl)
    if found is None:
        return InstallResult(False, "Playwright is not installed, so there is no installer.")
    command, env = found
    return await run_installer(command, env=env, timeout_s=timeout_s)


async def install_system_deps(
    *, verify_ssl: bool = False, timeout_s: float = INSTALL_TIMEOUT_S
) -> InstallResult:
    """Install Chromium's Linux libraries, when that needs no password."""

    if not _on_linux():
        return InstallResult(False, "only Linux installs Chromium's system libraries separately")
    apt_config = None if verify_ssl else insecure_apt_config()
    try:
        built = system_deps_command(apt_config, interactive=False)
        if isinstance(built, str):
            return InstallResult(False, built)
        command, env = built
        return await run_installer(command, env=env, timeout_s=timeout_s)
    finally:
        if apt_config is not None:
            apt_config.unlink(missing_ok=True)


async def run_installer(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_s: float = INSTALL_TIMEOUT_S,
) -> InstallResult:
    """Run one install command to the end, keeping what it said if it failed."""

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return InstallResult(False, str(exc))
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError:
        await _kill(process)
        return InstallResult(False, f"gave up after {timeout_s:g}s without it finishing")
    except BaseException:
        # Cancelled with the turn: an install left running would finish, or
        # hang, with nobody waiting for it.
        await _kill(process)
        raise
    if process.returncode == 0:
        return InstallResult(True)
    tail = _tail(output.decode("utf-8", "replace"))
    return InstallResult(False, tail or f"exit code {process.returncode}")


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        return
    await process.wait()


# What surrounds the cause in an installer's output: progress bars
# ("|■■■■   |  40% of 172.8 MiB"), Node stack frames ("at TLSSocket...") and
# the notice Node prints when certificate checks are off. Kept, they push the
# one line that says why - a refused certificate, a 404 - out of the tail.
_NOISE_PREFIXES = ("|", "at ", "(node:", "(use `node --trace-warnings")


def _tail(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    kept = [
        line
        for line in lines
        if line and line not in "{}" and not line.lower().startswith(_NOISE_PREFIXES)
    ]
    return "\n".join(kept[-_OUTPUT_TAIL_LINES:])
