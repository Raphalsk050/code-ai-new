"""``code-ai doctor browser``: does the agent's browser start on this machine?

Checked in the order things break: Playwright present in this build, its
bundled Node driver able to run (the part a one-file binary carries as data
and can get wrong), Chromium on disk, and last a real start - the same
session code, launch options and automatic installs the agent uses, so the
answer is the one the agent would get. That start happens in a throwaway
profile: the workspace's own may be held by a running session, and the
question is about this machine, not about the logins kept there.

``--install`` fetches what is missing first, with its progress on screen.
``--with-deps`` also installs Chromium's Linux libraries, asking for the
password the agent's own automatic install cannot ask for. ``--no-launch``
stops before a browser starts, for a build machine with no display and no
Chromium, where what is worth checking is that the binary carries a driver
that runs.
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.tools.browser.install import (
    display_command,
    driver_command,
    insecure_apt_config,
    install_package_command,
    package_missing_message,
    prepare_driver,
    system_deps_command,
)
from code_ai.tools.browser.session import BrowserSession


def run_browser_doctor(
    config: AppConfig,
    *,
    install: bool = False,
    with_deps: bool = False,
    launch: bool = True,
) -> int:
    frozen = bool(getattr(sys, "frozen", False))
    print(f"code-ai:     {sys.executable}{' (binary)' if frozen else ''}")
    tls = "checked" if config.ssl_verification else "not checked (ssl_verification is off)"
    print(f"tls:         certificates {tls}")
    if install and not _install(config):
        return 1
    if with_deps and not _install_system_deps(config):
        return 1
    version = _playwright_version()
    print(f"playwright:  {version or 'not installed'}")
    if version is None:
        print(package_missing_message())
        return 1
    driver_ok, chromium = asyncio.run(_driver_check(config))
    if not driver_ok:
        print(f"driver:      FAILED\n{chromium}")
        return 1
    print("driver:      ok")
    print(f"chromium:    {chromium}")
    if not launch:
        return 0
    settings = config.browser
    print(f"channel:     {settings.channel or 'bundled Chromium, Edge/Chrome as fallback'}")
    automatic = " (installs whatever is missing first)" if settings.auto_install else ""
    print(f"starting the browser{automatic}...", flush=True)
    ok, detail, headless = asyncio.run(_probe(config))
    if not ok:
        print(f"browser:     FAILED\n{detail}")
        return 1
    print(f"browser:     ok - {detail}, {'headless' if headless else 'with a window'}")
    return 0


def _install(config: AppConfig) -> bool:
    verify = config.ssl_verification
    if _playwright_version() is None:
        if getattr(sys, "frozen", False):
            print(package_missing_message())
            return False
        if not _run(install_package_command(verify_ssl=verify)):
            return False
        # A package pip just added is importable in this process only once the
        # import system forgets it looked before.
        importlib.invalidate_caches()
    # Before the command is built: it copies the environment prepare_driver sets.
    prepare_driver()
    found = driver_command("install", "chromium", verify_ssl=verify)
    if found is None:
        print(package_missing_message())
        return False
    command, env = found
    return _run(command, env=env)


def _install_system_deps(config: AppConfig) -> bool:
    if not sys.platform.startswith("linux"):
        print("libraries:   nothing to install on this system")
        return True
    apt_config = None if config.ssl_verification else insecure_apt_config()
    try:
        built = system_deps_command(apt_config, interactive=True)
        if isinstance(built, str):
            print(f"libraries:   {built}")
            return False
        command, env = built
        return _run(command, env=env)
    finally:
        if apt_config is not None:
            apt_config.unlink(missing_ok=True)


def _run(command: list[str], *, env: dict[str, str] | None = None) -> bool:
    print(f"$ {display_command(command)}", flush=True)
    # Inherits the terminal, so the download shows its own progress and sudo
    # can ask for the password.
    if subprocess.run(command, env=env, check=False).returncode == 0:
        return True
    print("install failed; the cause is in the output above.")
    return False


def _playwright_version() -> str | None:
    try:
        from playwright._repo_version import version
    except Exception:  # noqa: BLE001 - not in this build, or not installed
        return None
    return str(version)


async def _driver_check(config: AppConfig) -> tuple[bool, str]:
    """Start the driver and ask it where Chromium belongs - no browser needed."""

    prepare_driver()
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            path = Path(playwright.chromium.executable_path)
    except Exception as exc:  # noqa: BLE001 - reported, whatever it is
        return False, str(exc).strip()
    if path.is_file():
        return True, f"{path} (present)"
    later = "installed on first use" if config.browser.auto_install else "run with --install"
    return True, f"{path} (missing - {later})"


async def _probe(config: AppConfig) -> tuple[bool, str, bool]:
    settings = config.browser
    with tempfile.TemporaryDirectory(
        prefix="code-ai-browser-", ignore_cleanup_errors=True
    ) as scratch:
        session = BrowserSession(
            profile_dir=Path(scratch) / "profile",
            headless=settings.headless,
            timeout_ms=settings.timeout_ms,
            channel=settings.channel,
            auto_install=settings.auto_install,
            ssl_verification=config.ssl_verification,
        )
        try:
            await session.page()
        except ToolExecutionError as exc:
            return False, str(exc), session.headless
        finally:
            await session.close()
        return True, session.browser_name, session.headless
