"""Getting a browser to start: the installs, the stand-ins, and what is said when neither works."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from code_ai.cli.main import build_parser
from code_ai.config.models import BrowserConfig
from code_ai.core.errors import ConfigurationError, ToolExecutionError
from code_ai.tools.browser import install as install_module
from code_ai.tools.browser import session as session_module
from code_ai.tools.browser.install import (
    FALLBACK_CHANNELS,
    MISSING_BROWSER,
    MISSING_DEPS,
    NO_DISPLAY,
    OTHER,
    PROFILE_IN_USE,
    InstallResult,
    code_ai_command,
    default_browsers_path,
    driver_command,
    insecure_apt_config,
    install_package_command,
    launch_failure_kind,
    package_missing_message,
    prepare_driver,
    run_installer,
    system_deps_command,
)
from code_ai.tools.browser.session import BrowserSession

MISSING_EXECUTABLE = (
    "BrowserType.launch_persistent_context: Executable doesn't exist at "
    "C:\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe\n"
    "Looks like Playwright was just installed or updated.\n"
    "Please run the following command to download new browsers:\n\n    playwright install"
)

MISSING_LIBRARIES = (
    "Host system is missing dependencies to run browsers.\n"
    "Please install them with the following command:\n\n    sudo playwright install-deps"
)

BROWSERS_PATH = "PLAYWRIGHT_BROWSERS_PATH"


async def _noop(*args, **kwargs):
    return None


class FakeChromium:
    """Playwright's Chromium launcher, on a machine that has what the test gives it."""

    def __init__(self, root: Path) -> None:
        self.executable_path = str(root / "ms-playwright" / "chrome.exe")
        self.channels: set[str] = set()
        self.launches: list[dict] = []
        # Raised by the next launches, in order, before anything else is checked.
        self.errors: list[Exception] = []

    def put_bundled(self) -> None:
        path = Path(self.executable_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    async def launch_persistent_context(self, user_data_dir, **options):
        self.launches.append({"profile": Path(user_data_dir), **options})
        if self.errors:
            raise self.errors.pop(0)
        channel = options.get("channel", "")
        if channel and channel not in self.channels:
            raise RuntimeError(
                f"Chromium distribution '{channel}' is not found at C:\\{channel}.exe\n"
                f'Run "npx playwright install {channel}"'
            )
        if not channel and not Path(self.executable_path).is_file():
            raise RuntimeError(MISSING_EXECUTABLE)
        return SimpleNamespace(pages=[], close=_noop)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine with Playwright importable and every installer faked and counted."""

    chromium = FakeChromium(tmp_path)
    module = ModuleType("playwright.async_api")
    state = SimpleNamespace(
        chromium=chromium,
        module=module,
        installs=0,
        install_ok=True,
        install_output="",
        deps_installs=0,
        deps_ok=True,
        deps_output="",
        package_installs=0,
    )

    async def install(**kwargs):
        state.installs += 1
        if state.install_ok:
            chromium.put_bundled()
            return InstallResult(True)
        return InstallResult(False, state.install_output)

    async def install_deps(**kwargs):
        state.deps_installs += 1
        return InstallResult(state.deps_ok, "" if state.deps_ok else state.deps_output)

    async def install_package(**kwargs):
        state.package_installs += 1
        monkeypatch.setitem(sys.modules, "playwright.async_api", module)
        return InstallResult(True)

    async def start():
        return SimpleNamespace(chromium=chromium, stop=_noop)

    module.async_playwright = lambda: SimpleNamespace(start=start)
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    monkeypatch.setattr(session_module, "install_browser", install)
    monkeypatch.setattr(session_module, "install_system_deps", install_deps)
    monkeypatch.setattr(session_module, "install_package", install_package)
    monkeypatch.setattr(session_module, "prepare_driver", lambda: None)
    return state


@pytest.fixture
def unset_browsers_path(monkeypatch):
    # Set before it is removed, so teardown puts back what was there -
    # including nothing - after prepare_driver writes to it.
    monkeypatch.setenv(BROWSERS_PATH, "")
    monkeypatch.delenv(BROWSERS_PATH)


def make(tmp_path: Path, **options) -> BrowserSession:
    return BrowserSession(profile_dir=tmp_path / "profile", **options)


# ------------------------------------------------------------------ download


async def test_a_missing_chromium_is_downloaded_before_the_first_launch(tmp_path, machine) -> None:
    """A fresh machine, or any Playwright upgrade: the pinned build is not there yet."""

    session = make(tmp_path)
    await session._start()

    assert machine.installs == 1
    assert session.browser_name == "chromium"
    assert [launch.get("channel") for launch in machine.chromium.launches] == [None]


async def test_a_chromium_already_there_is_not_downloaded_again(tmp_path, machine) -> None:
    machine.chromium.put_bundled()
    await make(tmp_path)._start()
    assert machine.installs == 0


async def test_the_download_is_tried_once_per_session_not_on_every_call(tmp_path, machine) -> None:
    """Offline stays offline: paying for the attempt on every call helps nobody."""

    machine.install_ok = False
    session = make(tmp_path)
    for _ in range(2):
        with pytest.raises(ToolExecutionError):
            await session._start()
    assert machine.installs == 1


async def test_with_auto_install_off_nothing_is_installed(tmp_path, machine) -> None:
    session = make(tmp_path, auto_install=False)
    with pytest.raises(ToolExecutionError) as caught:
        await session._start()
    assert (machine.installs, machine.deps_installs, machine.package_installs) == (0, 0, 0)
    assert "doctor browser --install" in str(caught.value)


# ------------------------------------------------------------------ system libraries


async def test_missing_system_libraries_are_installed_and_the_launch_tried_again(
    tmp_path, machine
) -> None:
    """A fresh Linux machine has the build but not the libraries it links against."""

    machine.chromium.put_bundled()
    machine.chromium.errors.append(RuntimeError(MISSING_LIBRARIES))
    session = make(tmp_path)
    await session._start()

    assert machine.deps_installs == 1
    assert len(machine.chromium.launches) == 2
    assert session.browser_name == "chromium"


async def test_a_fresh_machine_gets_the_build_and_then_its_libraries(tmp_path, machine) -> None:
    """Both missing at once: each is installed as the launch reveals it."""

    machine.chromium.errors += [RuntimeError(MISSING_EXECUTABLE), RuntimeError(MISSING_LIBRARIES)]
    machine.chromium.put_bundled()
    await make(tmp_path)._start()

    assert (machine.installs, machine.deps_installs) == (1, 1)
    assert len(machine.chromium.launches) == 3


async def test_libraries_that_need_a_password_are_left_to_the_user_with_the_command(
    tmp_path, machine
) -> None:
    """Nobody is at a prompt to type it, and waiting for one would hang the turn."""

    machine.chromium.put_bundled()
    machine.chromium.errors.append(RuntimeError(MISSING_LIBRARIES))
    machine.deps_ok = False
    machine.deps_output = "sudo: a password is required"
    with pytest.raises(ToolExecutionError) as caught:
        await make(tmp_path)._start()

    message = str(caught.value)
    assert "a password is required" in message
    assert "--with-deps" in message


def test_libraries_are_installed_without_ever_waiting_on_a_password(
    monkeypatch, tmp_path
) -> None:
    pytest.importorskip("playwright")
    monkeypatch.setattr(install_module, "_on_linux", lambda: True)
    monkeypatch.setattr(install_module, "_is_root", lambda: False)
    monkeypatch.setattr(install_module, "_sudo_available", lambda: True)
    apt = tmp_path / "apt.conf"

    command, env = system_deps_command(apt, interactive=False)
    # -n: fail at once instead of asking; env: APT_CONFIG survives sudo.
    assert command[:4] == ["sudo", "-n", "env", f"APT_CONFIG={apt}"]
    assert command[-2:] == ["install-deps", "chromium"]

    by_hand, _ = system_deps_command(apt, interactive=True)
    assert by_hand[:2] == ["sudo", "env"]


def test_as_root_the_libraries_are_installed_without_sudo(monkeypatch, tmp_path) -> None:
    pytest.importorskip("playwright")
    monkeypatch.setattr(install_module, "_on_linux", lambda: True)
    monkeypatch.setattr(install_module, "_is_root", lambda: True)
    apt = tmp_path / "apt.conf"

    command, env = system_deps_command(apt, interactive=False)
    assert command[0] != "sudo"
    assert env["APT_CONFIG"] == str(apt)


def test_without_root_or_sudo_there_is_no_command_only_a_reason(monkeypatch) -> None:
    pytest.importorskip("playwright")
    monkeypatch.setattr(install_module, "_on_linux", lambda: True)
    monkeypatch.setattr(install_module, "_is_root", lambda: False)
    monkeypatch.setattr(install_module, "_sudo_available", lambda: False)
    assert "sudo is not installed" in system_deps_command(None, interactive=False)


def test_apt_is_told_to_skip_certificate_checks() -> None:
    path = insecure_apt_config()
    try:
        text = path.read_text(encoding="utf-8")
    finally:
        path.unlink()
    assert 'Acquire::https::Verify-Peer "false";' in text


# ------------------------------------------------------------------ the package


async def test_from_source_a_missing_playwright_is_installed_first(
    tmp_path, machine, monkeypatch
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    machine.chromium.put_bundled()
    session = make(tmp_path)
    await session._start()

    assert machine.package_installs == 1
    assert session.browser_name == "chromium"


async def test_a_binary_never_reaches_for_pip(tmp_path, machine, monkeypatch) -> None:
    """A frozen build carries Playwright or cannot get it: pip is not there to run."""

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(ToolExecutionError) as caught:
        await make(tmp_path)._start()

    assert machine.package_installs == 0
    assert "built without Playwright" in str(caught.value)


# ------------------------------------------------------------------ stand-ins


async def test_when_chromium_cannot_be_had_an_installed_browser_stands_in(
    tmp_path, machine
) -> None:
    """Behind a proxy that blocks the download, Edge or Chrome is already there."""

    machine.install_ok = False
    machine.chromium.channels = set(FALLBACK_CHANNELS)
    session = make(tmp_path)
    await session._start()

    assert session.browser_name == FALLBACK_CHANNELS[0]
    # A profile of its own: the two browsers cannot read each other's.
    assert machine.chromium.launches[-1]["profile"] == tmp_path / f"profile-{FALLBACK_CHANNELS[0]}"


async def test_with_nothing_to_drive_the_error_says_why_and_what_to_run(tmp_path, machine) -> None:
    machine.install_ok = False
    machine.install_output = "Error: getaddrinfo ENOTFOUND cdn.playwright.dev"
    with pytest.raises(ToolExecutionError) as caught:
        await make(tmp_path)._start()

    message = str(caught.value)
    assert "ENOTFOUND" in message
    assert "doctor browser --install" in message


async def test_a_configured_channel_is_used_as_is_and_never_substituted(tmp_path, machine) -> None:
    """The user named that browser; a quiet stand-in would hide that it is missing."""

    machine.chromium.put_bundled()
    session = make(tmp_path, channel="chrome")
    with pytest.raises(ToolExecutionError) as caught:
        await session._start()

    assert "browser.channel (chrome)" in str(caught.value)
    assert [launch.get("channel") for launch in machine.chromium.launches] == ["chrome"]
    assert machine.installs == 0


async def test_with_no_display_the_browser_starts_headless(tmp_path, machine) -> None:
    """A server or SSH session has no screen; reading and clicking still work."""

    machine.chromium.put_bundled()
    machine.chromium.errors.append(
        RuntimeError("Looks like you launched a headed browser without having a XServer running.")
    )
    session = make(tmp_path)
    await session._start()

    assert session.headless is True
    assert [launch["headless"] for launch in machine.chromium.launches] == [False, True]


async def test_a_profile_held_by_another_browser_is_named_as_such(tmp_path, machine) -> None:
    machine.chromium.put_bundled()
    held = "Target page, context or browser has been closed\nOpening in existing browser session."
    machine.chromium.errors += [RuntimeError(held), RuntimeError(held)]
    with pytest.raises(ToolExecutionError) as caught:
        await make(tmp_path).read()
    assert "already open in another browser" in str(caught.value)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (MISSING_EXECUTABLE, MISSING_BROWSER),
        # Names "playwright install-deps": must not read as a missing browser.
        (MISSING_LIBRARIES, MISSING_DEPS),
        # Linux's own words, when Playwright did not check first (seen on Ubuntu 24.04).
        (
            "Target page, context or browser has been closed\n[pid=397][err] "
            "chrome-headless-shell: error while loading shared libraries: libnspr4.so: "
            "cannot open shared object file: No such file or directory",
            MISSING_DEPS,
        ),
        ("Looks like you launched a headed browser without having a XServer running.", NO_DISPLAY),
        ("Browser logs:\nOpening in existing browser session.", PROFILE_IN_USE),
        ("net::ERR_PROXY_CONNECTION_FAILED", OTHER),
    ],
)
def test_launch_failures_are_told_apart(message, kind) -> None:
    assert launch_failure_kind(RuntimeError(message)) == kind


def test_a_launch_error_keeps_what_the_browser_said_not_its_command_line() -> None:
    """The cause comes after a command line long enough to fill the whole budget."""

    error = RuntimeError(
        "BrowserType.launch_persistent_context: boom\nCall log:\n"
        "  - <launching> /x/chrome " + "--flag " * 500 + "\n"
        "  - [pid=1][err] chrome: crashed in the GPU process\n"
        "  - [pid=1] <process did exit: exitCode=139>"
    )
    message = install_module.describe_launch_failure(error)
    assert "crashed in the GPU process" in message
    assert "--flag --flag" not in message


# ------------------------------------------------------------------ the binary


def test_a_frozen_build_keeps_its_browsers_in_the_users_cache(
    monkeypatch, unset_browsers_path
) -> None:
    """Playwright's frozen default is the unpack directory, deleted on every exit.

    A Chromium downloaded there was gone by the next run, and one installed
    the ordinary way was never found.
    """

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    prepare_driver()
    assert os.environ[BROWSERS_PATH] == str(default_browsers_path())
    assert default_browsers_path().name == "ms-playwright"


def test_a_browsers_path_the_user_set_is_left_alone(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(BROWSERS_PATH, str(tmp_path))
    prepare_driver()
    assert os.environ[BROWSERS_PATH] == str(tmp_path)


def test_from_source_playwrights_own_default_stands(monkeypatch, unset_browsers_path) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    prepare_driver()
    assert BROWSERS_PATH not in os.environ


def test_in_the_binary_the_way_back_in_is_the_binary_itself(monkeypatch) -> None:
    """A frozen build has no ``python -m`` and no pip to point anyone at."""

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert code_ai_command("doctor", "browser") == [sys.executable, "doctor", "browser"]
    assert "built without Playwright" in package_missing_message()


def test_from_source_the_way_back_in_goes_through_this_python(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert code_ai_command("doctor")[:3] == [sys.executable, "-m", "code_ai"]


def test_the_installer_is_playwrights_own_node_not_a_python_module() -> None:
    """The one installer a frozen binary still has: the driver it runs browsers with."""

    pytest.importorskip("playwright")
    command, env = driver_command("install", "chromium")
    assert Path(command[0]).name.startswith("node")
    assert command[-2:] == ["install", "chromium"]
    assert env["PW_LANG_NAME"] == "python"


async def test_a_failed_install_keeps_the_lines_that_say_why() -> None:
    script = (
        "print('|####      |  40% of 170 MiB'); "
        "print('(node:7) Warning: Setting NODE_TLS_REJECT_UNAUTHORIZED to 0 is insecure'); "
        "print('proxy refused the download'); "
        "print('    at TLSSocket.onConnectSecure (node:internal/tls/wrap:1748:34)'); "
        "print('}'); raise SystemExit(3)"
    )
    result = await run_installer([sys.executable, "-c", script])
    assert not result.ok
    assert result.output == "proxy refused the download"


async def test_a_stalled_install_is_given_up_on() -> None:
    result = await run_installer(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5
    )
    assert not result.ok
    assert "gave up" in result.output


# ------------------------------------------------------------------ config and CLI


def test_an_unknown_browser_channel_is_refused_when_the_config_loads() -> None:
    with pytest.raises(ConfigurationError):
        BrowserConfig.from_mapping({"channel": "firefox"}).validate()
    BrowserConfig.from_mapping({"channel": "MSEdge"}).validate()


def test_the_doctor_can_install_and_can_skip_the_launch() -> None:
    args = build_parser().parse_args(
        ["doctor", "browser", "--install", "--with-deps", "--no-launch"]
    )
    assert (args.doctor_command, args.install, args.with_deps, args.no_launch) == (
        "browser",
        True,
        True,
        True,
    )


# ------------------------------------------------------------------ company TLS


def test_with_ssl_verification_off_the_download_skips_the_certificate_check(monkeypatch) -> None:
    """A network that re-signs TLS hands Node a certificate it refuses unless told not to."""

    pytest.importorskip("playwright")
    monkeypatch.delenv("NODE_TLS_REJECT_UNAUTHORIZED", raising=False)
    _, lenient = driver_command("install", "chromium", verify_ssl=False)
    _, strict = driver_command("install", "chromium", verify_ssl=True)

    assert lenient["NODE_TLS_REJECT_UNAUTHORIZED"] == "0"
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in strict
    # Only the install's own environment: the one Playwright browses with
    # starts from this process's, which stays untouched.
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in os.environ


def test_with_ssl_verification_off_pip_trusts_the_pypi_hosts() -> None:
    assert install_package_command(verify_ssl=False).count("--trusted-host") == 2
    assert "--trusted-host" not in install_package_command(verify_ssl=True)


async def test_every_automatic_install_gets_the_configured_ssl_setting(
    tmp_path, machine, monkeypatch
) -> None:
    seen: list[bool] = []

    async def install(**kwargs):
        seen.append(kwargs["verify_ssl"])
        machine.chromium.put_bundled()
        return InstallResult(True)

    monkeypatch.setattr(session_module, "install_browser", install)
    await make(tmp_path)._start()
    Path(machine.chromium.executable_path).unlink()
    await make(tmp_path, ssl_verification=True)._start()

    # Off unless the config turns it on, like every other connection Code-AI makes.
    assert seen == [False, True]
