"""A real browser the agent drives, in a profile that is not the user's.

Two things shape this. The browser is *persistent* - one profile directory per
workspace, kept between sessions - because the whole point of letting the user
log in by hand is that they should only have to do it once. And it is *visible*
by default, because a headless browser cannot be logged into: the handover for
anything behind a password is the user looking at the same window the agent is
driving and typing their credentials into it themselves.

The agent never types the password. It navigates to the login page, hands over,
and the session that results is stored in the profile - so the secret is only
ever between the user and the site, and the agent inherits the cookie.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.browser.devtools import DevtoolsRecorder, inspect_page, mask, render_value
from code_ai.tools.browser.dom import (
    COLLECT_JS,
    INTERACTIVE_SELECTOR,
    MAX_ELEMENTS,
    MAX_LABEL_CHARS,
    MAX_OPTIONS,
    VIEWPORT_JS,
    describe,
    stamp_selector,
)
from code_ai.tools.browser.install import (
    FALLBACK_CHANNELS,
    MISSING_BROWSER,
    MISSING_DEPS,
    NO_DISPLAY,
    bundled_browser_present,
    describe_launch_failure,
    install_browser,
    install_package,
    install_system_deps,
    launch_failure_kind,
    package_missing_message,
    prepare_driver,
)

# How long a navigation or an action may take before it is called a failure.
# A page that has not settled in this long is either very slow or waiting on
# something the agent cannot provide, and the turn must not hang on it.
DEFAULT_TIMEOUT_MS = 30_000

# The least a browser gets to start. The first launch after a download is the
# slow one - an antivirus scans the new executable before letting it run - and
# failing it would report a working install as broken.
_LAUNCH_TIMEOUT_MS = 60_000

# Text pulled off a page is bounded like any other tool output: a long article
# would otherwise cost the whole turn's context to answer "what is on screen".
MAX_PAGE_TEXT_CHARS = 6_000

# Dialogs and downloads kept between reads.
_MAX_DIALOGS = 10
_MAX_DOWNLOADS = 20

# Playwright reports a vanished browser through several exception types and
# spellings depending on which call noticed it first. Matching the text is what
# covers them all - and being wrong here only costs one extra restart attempt,
# where being too narrow costs the whole feature the moment a user closes a
# window.
_CLOSED_MARKERS = (
    "has been closed",
    "target closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser closed",
    "connection closed",
    "playwright is not running",
    "event loop is closed",
)


def _is_closed_error(exc: BaseException) -> bool:
    return any(marker in str(exc).lower() for marker in _CLOSED_MARKERS)


@dataclass
class BrowserSession:
    """One browser, started on first use and reused for the rest of the session."""

    profile_dir: Path
    headless: bool = False
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    # A browser already on the machine ("msedge", "chrome") to drive instead of
    # Playwright's own build. Empty means the bundled Chromium, with Edge or
    # Chrome standing in by themselves when that build cannot be had.
    channel: str = ""
    # Fetch the bundled Chromium on first use when it is missing - which is
    # also after every Playwright upgrade, since each release pins its own.
    auto_install: bool = True
    # Check TLS certificates while downloading Chromium. Off by default, like
    # the rest of Code-AI (config ssl_verification): a proxy that re-signs TLS
    # with a company certificate would otherwise fail every download.
    ssl_verification: bool = False
    # The browser actually being driven: "chromium", or the channel standing in.
    browser_name: str = ""
    # What was already tried installing this session - "package", "browser",
    # "system" - and what the last failed install said.
    _installs_attempted: set[str] = field(default_factory=set, repr=False)
    _install_output: str = field(default="", repr=False)
    _playwright: Any = field(default=None, repr=False)
    _context: Any = field(default=None, repr=False)
    _page: Any = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # What to do with an alert(), confirm() or prompt(): "accept" or "dismiss".
    # Something must, or the page stops dead behind a modal nobody can see.
    dialog_policy: str = "accept"
    # Where a file the page downloads is kept. Without it Playwright deletes it
    # when the context closes, and an export the agent just asked for is gone
    # before anything can look at it.
    download_dir: Path | None = None
    # The elements offered by the last read, so a click can name one by number.
    last_elements: list[dict[str, Any]] = field(default_factory=list)
    # The frames those elements were found in, in the same order as their
    # "_frame" field. Held rather than re-derived: frame order can change.
    last_frames: list[Any] = field(default_factory=list, repr=False)
    # Dialogs answered since the last read, reported once so the model knows a
    # confirm() was in the way rather than wondering why the click did nothing.
    _dialogs: list[dict[str, Any]] = field(default_factory=list, repr=False)
    # Files the page downloaded since the last read, with where they were kept.
    _downloads: list[dict[str, Any]] = field(default_factory=list, repr=False)
    # Pages already listened to, by id, so handlers are attached once each.
    _hooked: set[int] = field(default_factory=set, repr=False)
    # Where the browser was, so a window the user closed can be reopened there
    # rather than on a blank page that answers "nothing here" to a question
    # about a site that was open a second ago.
    last_url: str = ""
    # The console and network logs. They are the two things DevTools shows that
    # cannot be read after the fact, so every page is listened to from the
    # moment the session first touches it.
    devtools: DevtoolsRecorder = field(default_factory=DevtoolsRecorder)

    async def page(self) -> Any:
        """The current page, starting the browser the first time it is asked for."""

        async with self._lock:
            return await self._page_unlocked()

    async def _page_unlocked(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            self._watch(self._page)
            return self._page
        if self._context is None:
            await self._start()
        try:
            # A persistent context opens with one blank page; reuse it rather
            # than leaving an empty window beside the one being driven.
            pages = [page for page in self._context.pages if not page.is_closed()]
            self._page = pages[0] if pages else await self._context.new_page()
        except Exception as exc:  # noqa: BLE001
            if not _is_closed_error(exc):
                raise
            # The context object outlives the browser it spoke to, so a window
            # the user closed leaves a handle that looks alive and answers
            # every call with "target closed". Throw it away and start again.
            await self._rebuild()
        self._page.set_default_timeout(self.timeout_ms)
        self._watch(self._page)
        return self._page

    def _watch(self, page: Any) -> None:
        """Listen to one page: its logs, its dialogs, and the tabs it opens.

        A native dialog blocks the page until something answers it, and nothing
        will - the agent is not looking at the screen. Unanswered, every later
        action times out with no hint as to why, so they are answered here and
        reported in the next read.
        """

        self.devtools.watch(page)
        key = id(page)
        if key in self._hooked:
            return
        self._hooked.add(key)

        def on_dialog(dialog: Any) -> None:
            # Bounded: a page in a loop can raise them faster than they are read,
            # and the report is meant to explain a click, not to be the output.
            if len(self._dialogs) < _MAX_DIALOGS:
                self._dialogs.append(
                    {
                        "kind": getattr(dialog, "type", ""),
                        "message": mask(str(getattr(dialog, "message", ""))[:400]),
                        "answered": self.dialog_policy,
                    }
                )
            handler = dialog.dismiss() if self.dialog_policy == "dismiss" else dialog.accept()
            asyncio.ensure_future(handler)

        def on_download(download: Any) -> None:
            asyncio.ensure_future(self._keep(download))

        def on_popup(popup: Any) -> None:
            # A link that opens a tab is still the thing the agent just did, so
            # the new tab becomes the current one rather than being lost.
            self._page = popup
            self.last_elements = []
            self._watch(popup)

        try:
            page.on("dialog", on_dialog)
            page.on("popup", on_popup)
            page.on("download", on_download)
        except Exception:  # noqa: BLE001 - a page that died between the two
            return

    async def _keep(self, download: Any) -> None:
        """Put a downloaded file where it can be opened after the browser closes."""

        if self.download_dir is None or len(self._downloads) >= _MAX_DOWNLOADS:
            return
        try:
            name = Path(str(download.suggested_filename or "download")).name
            self.download_dir.mkdir(parents=True, exist_ok=True)
            target = self.download_dir / name
            await download.save_as(str(target))
        except Exception as exc:  # noqa: BLE001 - a cancelled or failed download
            self._downloads.append({"failed": mask(str(exc).splitlines()[0])})
            return
        self._downloads.append({"file": str(target), "url": getattr(download, "url", "")})

    async def _rebuild(self) -> Any:
        """Start a fresh browser and put it back where the old one was.

        Closing the window is a normal thing for a user to do - it is their
        screen - so it must cost the agent a moment rather than the rest of the
        task. Returning to the last address is what makes the recovery
        invisible: the alternative is a blank page that answers "nothing here"
        about a site that was open a second ago.

        Every path back from a dead browser goes through here, so none of them
        can restore the window while forgetting where it was pointed.
        """

        await self._discard()
        await self._start()
        pages = [page for page in self._context.pages if not page.is_closed()]
        self._page = pages[0] if pages else await self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        # Before the reload below, so the requests it makes are in the log.
        self.devtools.watch(self._page)
        # The element numbers were measured on a page that no longer exists.
        # Dropping them makes a stale click an error instead of a click at
        # whatever coordinates now happen to be there.
        self.last_elements = []
        if self.last_url and self.last_url != "about:blank":
            try:
                await self._page.goto(
                    self.last_url, wait_until="domcontentloaded", timeout=self.timeout_ms
                )
            except Exception:  # noqa: BLE001 - a page that will not reload is
                # still better answered by a live browser on a blank tab than by
                # a dead handle, so the restart stands and the caller reads it.
                pass
        return self._page

    async def _recover(self) -> Any:
        return await self._rebuild()

    async def _discard(self) -> None:
        """Drop the dead handles without trying to talk to them."""

        for closer in (self._context, self._playwright):
            if closer is None:
                continue
            try:
                await (closer.close() if closer is self._context else closer.stop())
            except Exception:  # noqa: BLE001 - it is already gone; that is the point
                pass
        self._context = None
        self._page = None
        self._playwright = None

    async def _start(self) -> None:
        async_playwright = await self._import_playwright()
        prepare_driver()
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._launch(self._playwright.chromium)
        except BaseException:
            await self._stop_playwright()
            raise

    async def _import_playwright(self) -> Any:
        """Playwright's entry point, pip-installing it first when running from source.

        A binary carries Playwright or cannot get it - there is no pip inside
        one - so frozen, a missing package is only ever reported.
        """

        try:
            module = importlib.import_module("playwright.async_api")
        except Exception as exc:  # noqa: BLE001 - playwright is optional
            if getattr(sys, "frozen", False) or not await self._install("package"):
                raise ToolExecutionError(package_missing_message(self._install_output)) from exc
            # A package pip just added is importable in this process only once
            # the import system forgets it looked before.
            importlib.invalidate_caches()
            try:
                module = importlib.import_module("playwright.async_api")
            except Exception as retry:  # noqa: BLE001
                raise ToolExecutionError(package_missing_message()) from retry
        return module.async_playwright

    async def _launch(self, chromium: Any) -> Any:
        """Start a browser, installing what it turns out to be missing on the way.

        In order: the bundled Chromium, downloaded first if it is missing;
        then whatever the launch itself reports missing - the build (the
        headless shell is a separate one the check above cannot see) or, on
        Linux, the system libraries it links against - each installed once
        and the launch tried again; then a Chrome or Edge already on the
        machine. Each step is there because the one before it fails on real
        machines: a fresh install, a Playwright upgrade, a proxy that blocks
        the download.
        """

        if self.channel:
            # Chosen in the config: that browser or an error saying so, never
            # a quiet substitute the user did not ask for.
            try:
                return await self._open(chromium, self.channel, self.profile_dir)
            except Exception as exc:  # noqa: BLE001
                raise ToolExecutionError(self._failure(exc)) from exc
        if not bundled_browser_present(chromium):
            await self._install("browser")
        try:
            return await self._open(chromium, "", self.profile_dir)
        except Exception as exc:  # noqa: BLE001
            failure = exc
        # Twice at most: a fresh Linux machine can lack both, the build first
        # and then the libraries it links against.
        for _ in range(2):
            missing = {MISSING_BROWSER: "browser", MISSING_DEPS: "system"}.get(
                launch_failure_kind(failure)
            )
            if missing is None or not await self._install(missing):
                break
            try:
                return await self._open(chromium, "", self.profile_dir)
            except Exception as exc:  # noqa: BLE001
                failure = exc
        if launch_failure_kind(failure) != MISSING_BROWSER:
            raise ToolExecutionError(self._failure(failure)) from failure
        for channel in FALLBACK_CHANNELS:
            # A profile of its own: Chrome and Edge encrypt what they store
            # with keys the other cannot read, and a browser handed a profile
            # written by a newer build refuses it.
            profile = self.profile_dir.with_name(f"{self.profile_dir.name}-{channel}")
            try:
                return await self._open(chromium, channel, profile)
            except Exception:  # noqa: BLE001 - not on this machine either
                continue
        raise ToolExecutionError(self._failure(failure)) from failure

    async def _open(self, chromium: Any, channel: str, profile: Path) -> Any:
        profile.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "headless": self.headless,
            "args": ["--no-first-run", "--no-default-browser-check"],
            "timeout": max(self.timeout_ms, _LAUNCH_TIMEOUT_MS),
        }
        if channel:
            options["channel"] = channel
        try:
            context = await chromium.launch_persistent_context(str(profile), **options)
        except Exception as exc:  # noqa: BLE001
            if self.headless or launch_failure_kind(exc) != NO_DISPLAY:
                raise
            # Nowhere to show a window: a server, SSH, WSL without a display.
            # Headless still reads, clicks and types; only the login handover
            # needs a window, and this machine has none to hand it over in.
            self.headless = True
            options["headless"] = True
            context = await chromium.launch_persistent_context(str(profile), **options)
        self.browser_name = channel or "chromium"
        return context

    async def _install(self, what: str) -> bool:
        """Install one missing piece - "package", "browser" or "system" - once a session.

        Once, because an install that failed - offline, a proxy, a password
        nobody is there to type - fails the same way on the next call, and
        paying for it on every browser call would turn one missing piece into
        a session of slow errors. True when an install ran now and succeeded.
        """

        if not self.auto_install or what in self._installs_attempted:
            return False
        self._installs_attempted.add(what)
        installer = {
            "package": install_package,
            "browser": install_browser,
            "system": install_system_deps,
        }[what]
        result = await installer(verify_ssl=self.ssl_verification)
        self._install_output = "" if result.ok else result.output
        return result.ok

    def _failure(self, exc: BaseException) -> str:
        return describe_launch_failure(
            exc,
            channel=self.channel,
            profile_dir=self.profile_dir,
            install_output=self._install_output,
        )

    async def _attempt(self, action: Any) -> Any:
        """Run one browser action, rebuilding the browser once if it has gone.

        Exactly once: a second failure is not a closed window, it is something
        that will keep failing, and retrying it forever would turn a broken
        page into a hung turn.
        """

        async with self._lock:
            try:
                return await action()
            except Exception as exc:  # noqa: BLE001
                if not _is_closed_error(exc):
                    raise
                await self._recover()
                return await action()

    async def read(
        self, *, screenshot: bool = False, full_page: bool = False
    ) -> dict[str, Any]:
        """What the page says, what can be acted on, and optionally a picture."""

        return await self._attempt(
            lambda: self._read_unlocked(screenshot=screenshot, full_page=full_page)
        )

    async def _read_unlocked(
        self, *, screenshot: bool = False, full_page: bool = False
    ) -> dict[str, Any]:
        page = await self._page_unlocked()
        self.last_url = page.url
        title = await page.title()
        try:
            text = await page.inner_text("body")
        except Exception:  # noqa: BLE001 - a page with no body yet (about:blank)
            text = ""
        elements = await self._elements(page)
        self.last_elements = elements
        truncated = len(text) > MAX_PAGE_TEXT_CHARS
        payload: dict[str, Any] = {
            "url": page.url,
            "title": title,
            "text": text[:MAX_PAGE_TEXT_CHARS] + ("\n...[truncated]" if truncated else ""),
            "elements": elements,
        }
        if truncated:
            payload["text_truncated"] = True
        viewport = await self._viewport(page)
        if viewport:
            payload["viewport"] = viewport
        tabs = self._open_pages()
        if len(tabs) > 1:
            payload["tabs"] = [{"index": i, "url": tab.url} for i, tab in enumerate(tabs)]
        if self._dialogs:
            payload["dialogs"] = list(self._dialogs)
            self._dialogs.clear()
        if self._downloads:
            payload["downloads"] = list(self._downloads)
            self._downloads.clear()
        if screenshot:
            payload["screenshot_png"] = await page.screenshot(type="png", full_page=full_page)
        return payload

    async def _elements(self, page: Any) -> list[dict[str, Any]]:
        """Every interactive element on the page, iframes included.

        Numbered across frames in one sequence, because the model sees one page
        and should not have to know which document a button happens to live in.
        The frame is kept beside the number so the action can go back to it.
        """

        frames = self._frames(page)
        self.last_frames = frames
        collected: list[dict[str, Any]] = []
        for position, frame in enumerate(frames):
            if len(collected) >= MAX_ELEMENTS:
                break
            try:
                found = await frame.evaluate(
                    COLLECT_JS,
                    [
                        INTERACTIVE_SELECTOR,
                        MAX_ELEMENTS - len(collected),
                        MAX_LABEL_CHARS,
                        MAX_OPTIONS,
                    ],
                )
            except Exception:  # noqa: BLE001 - mid-navigation, or cross-origin
                continue
            for item in found:
                if not isinstance(item, dict):
                    continue
                item["index"] = len(collected)
                if position:
                    # Only worth saying when it is not the main document.
                    item["frame"] = position
                item["_frame"] = position
                collected.append(item)
        return collected

    @staticmethod
    def _frames(page: Any) -> list[Any]:
        try:
            return [frame for frame in page.frames if not frame.is_detached()]
        except Exception:  # noqa: BLE001 - no frame tree yet
            return [page.main_frame] if getattr(page, "main_frame", None) else []

    @staticmethod
    async def _viewport(page: Any) -> dict[str, Any]:
        try:
            found = await page.evaluate(VIEWPORT_JS)
        except Exception:  # noqa: BLE001 - no DOM yet
            return {}
        return found if isinstance(found, dict) else {}

    def _open_pages(self) -> list[Any]:
        if self._context is None:
            return []
        try:
            return [page for page in self._context.pages if not page.is_closed()]
        except Exception:  # noqa: BLE001 - context already gone
            return []

    async def inspect(self, aspect: str, **options: Any) -> dict[str, Any]:
        """What one developer-tools panel shows about the page the browser is on."""

        if aspect in {"console", "network"}:
            # Logs rather than page state: reading them must not start a
            # browser only to report that nothing has happened yet.
            return {"url": self.last_url, "aspect": aspect, **self.devtools.read(aspect, **options)}

        async def read_panel() -> dict[str, Any]:
            page = await self._page_unlocked()
            self.last_url = page.url
            panel = await inspect_page(page, self._context, aspect, **options)
            return {"url": page.url, "aspect": aspect, **panel}

        return await self._attempt(read_panel)

    async def evaluate(self, expression: str) -> dict[str, Any]:
        """Run JavaScript in the page, the way the developer-tools console does.

        What the script logged comes back with its value, because that is what
        the console shows too - and a script run to debug something usually
        answers through console.log as much as through its return value.
        """

        async def run() -> dict[str, Any]:
            page = await self._page_unlocked()
            self.last_url = page.url
            mark = self.devtools.seq
            try:
                value = await page.evaluate(expression)
            except Exception as exc:  # noqa: BLE001
                if _is_closed_error(exc):
                    raise
                raise ToolExecutionError(f"The script threw: {self._script_error(exc)}") from exc
            return {"url": page.url, **render_value(value), "console": self.devtools.since(mark)}

        return await self._attempt(run)

    @staticmethod
    def _script_error(exc: BaseException) -> str:
        """The JavaScript error itself, without Playwright's call-site prefix and stack."""

        lines = str(exc).strip().splitlines()
        first = lines[0] if lines else type(exc).__name__
        return mask(first.removeprefix("Page.evaluate: ").strip())

    async def goto(self, url: str) -> dict[str, Any]:
        async def navigate() -> None:
            page = await self._page_unlocked()
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self.last_url = url

        await self._attempt(navigate)
        return await self.read()

    # ------------------------------------------------------------------ acting
    #
    # Every action goes through a locator on the attribute the read stamped,
    # never through the coordinates it also reported. Playwright then scrolls
    # the element into view, waits until it is actually clickable, and says so
    # when something covers it - three ways a coordinate click fails silently,
    # landing on a cookie banner or on nothing at all.

    async def click(
        self,
        target: dict[str, Any],
        *,
        button: str = "left",
        count: int = 1,
        modifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            locator, _ = await self._locate(page, target)
            await locator.click(
                button=button,
                click_count=max(1, count),
                modifiers=list(modifiers or []),
                timeout=self.timeout_ms,
            )
            await self._settle(page)

        return await self._act(run)

    async def hover(self, target: dict[str, Any]) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            locator, _ = await self._locate(page, target)
            await locator.hover(timeout=self.timeout_ms)
            # Menus open on a timer after the pointer lands.
            await self._pause(page, 300)

        return await self._act(run)

    async def type_text(
        self,
        target: dict[str, Any],
        text: str,
        *,
        replace: bool = False,
        submit: bool = False,
    ) -> dict[str, Any]:
        """Type into a field, or into whatever a rich editor puts the caret in.

        `fill` replaces the value in one step and is what a form wants;
        `press_sequentially` sends real keystrokes, which is what an editor
        that only listens to keydown - a document, a slide - needs.
        """

        async def run() -> None:
            page = await self._page_unlocked()
            locator, record = await self._locate(page, target)
            editable = bool(record.get("editable")) or record.get("tag") in {"", None}
            if replace and not editable:
                await locator.fill(text, timeout=self.timeout_ms)
            else:
                await locator.click(timeout=self.timeout_ms)
                if replace:
                    await self._select_all(page)
                await locator.press_sequentially(text, delay=15, timeout=self.timeout_ms)
            if submit:
                await locator.press("Enter", timeout=self.timeout_ms)
                await self._settle(page)

        return await self._act(run)

    async def press(self, keys: str, target: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a key or a chord - "Enter", "Control+b", "Escape" - as a user would.

        This is how an application is driven where no button exists for what is
        wanted: bold in a document, a slide's next-placeholder, undo.
        """

        async def run() -> None:
            page = await self._page_unlocked()
            sequence = [part.strip() for part in keys.split(",") if part.strip()]
            if not sequence:
                raise ToolExecutionError("No keys given to press.")
            if target:
                locator, _ = await self._locate(page, target)
                for key in sequence:
                    await locator.press(key, timeout=self.timeout_ms)
            else:
                for key in sequence:
                    await page.keyboard.press(key)
            await self._settle(page)

        return await self._act(run)

    async def focus(self, target: dict[str, Any]) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            locator, _ = await self._locate(page, target)
            await locator.focus(timeout=self.timeout_ms)

        return await self._act(run)

    async def select(self, target: dict[str, Any], values: list[str]) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            locator, _ = await self._locate(page, target)
            try:
                await locator.select_option(values, timeout=self.timeout_ms)
            except Exception as exc:  # noqa: BLE001
                if _is_closed_error(exc):
                    raise
                # A <select> takes values; a listbox built out of divs takes a
                # click on the option that reads like what was asked for.
                raise ToolExecutionError(
                    f"Could not select {values!r}: {mask(str(exc).splitlines()[0])}. "
                    "If this is not a real <select>, click the option instead."
                ) from exc

        return await self._act(run)

    async def set_checked(self, target: dict[str, Any], checked: bool) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            locator, _ = await self._locate(page, target)
            await locator.set_checked(checked, timeout=self.timeout_ms)
            await self._settle(page)

        return await self._act(run)

    async def upload(self, target: dict[str, Any], paths: list[str]) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            locator, _ = await self._locate(page, target)
            await locator.set_input_files(paths, timeout=self.timeout_ms)
            await self._settle(page)

        return await self._act(run)

    async def drag(
        self,
        source: dict[str, Any],
        destination: dict[str, Any] | None = None,
        *,
        offset: tuple[int, int] | None = None,
        steps: int = 20,
    ) -> dict[str, Any]:
        """Drag with the mouse, from an element to another or by an offset.

        Stepped by hand rather than through drag_to: an editor that moves a
        shape watches mousemove, and a drag that jumps straight to the end
        looks like no movement at all. The steps are what make it land.
        """

        async def run() -> None:
            page = await self._page_unlocked()
            start = await self._point(page, source)
            if destination is not None:
                end = await self._point(page, destination)
            elif offset is not None:
                end = (start[0] + offset[0], start[1] + offset[1])
            else:
                raise ToolExecutionError("A drag needs somewhere to go: a target or an offset.")
            await page.mouse.move(start[0], start[1])
            await page.mouse.down()
            # One move before the stepped run: some libraries only start
            # dragging after the first move that follows the press.
            await page.mouse.move(start[0] + 1, start[1] + 1)
            await page.mouse.move(end[0], end[1], steps=max(2, steps))
            await page.mouse.up()
            await self._settle(page)

        return await self._act(run)

    async def scroll(
        self,
        *,
        target: dict[str, Any] | None = None,
        dx: int = 0,
        dy: int = 0,
        to: str = "",
    ) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            if target is not None:
                locator, _ = await self._locate(page, target)
                await locator.scroll_into_view_if_needed(timeout=self.timeout_ms)
            elif to in {"top", "bottom"}:
                where = "0" if to == "top" else "document.body.scrollHeight"
                await page.evaluate(f"() => window.scrollTo(0, {where})")
            else:
                await page.mouse.wheel(dx, dy)
            await self._pause(page, 250)

        return await self._act(run)

    async def navigate(self, action: str) -> dict[str, Any]:
        async def run() -> None:
            page = await self._page_unlocked()
            mover = {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}.get(
                action
            )
            if mover is None:
                raise ToolExecutionError(f"Unknown navigation {action!r}: back, forward, reload.")
            await mover(wait_until="domcontentloaded", timeout=self.timeout_ms)
            self.last_url = page.url

        return await self._act(run)

    async def wait_for(
        self,
        *,
        text: str = "",
        selector: str = "",
        url: str = "",
        state: str = "",
        seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Wait for the page to reach a state, rather than reading it repeatedly."""

        async def run() -> None:
            page = await self._page_unlocked()
            timeout = self.timeout_ms
            if selector:
                await page.wait_for_selector(selector, timeout=timeout)
            elif text:
                await page.wait_for_selector(f"text={text}", timeout=timeout)
            elif url:
                await page.wait_for_url(url, timeout=timeout)
            elif state:
                await page.wait_for_load_state(state, timeout=timeout)
            elif seconds > 0:
                await asyncio.sleep(min(seconds, timeout / 1000))
            else:
                raise ToolExecutionError("Nothing to wait for: text, selector, url, state or time.")

        return await self._act(run)

    async def screenshot(
        self, *, target: dict[str, Any] | None = None, full_page: bool = False
    ) -> dict[str, Any]:
        """A picture of the page, or of one element - the visual check itself."""

        async def run() -> dict[str, Any]:
            page = await self._page_unlocked()
            if target is not None:
                locator, record = await self._locate(page, target)
                await locator.scroll_into_view_if_needed(timeout=self.timeout_ms)
                shot = await locator.screenshot(type="png", timeout=self.timeout_ms)
                return {"url": page.url, "of": describe(record), "screenshot_png": shot}
            shot = await page.screenshot(type="png", full_page=full_page)
            return {"url": page.url, "of": "page", "screenshot_png": shot}

        return await self._attempt(run)

    async def tabs(self, action: str = "list", index: int = 0) -> dict[str, Any]:
        async def run() -> dict[str, Any]:
            await self._page_unlocked()
            pages = self._open_pages()
            if action == "list":
                return {
                    "tabs": [
                        {"index": i, "url": tab.url, "current": tab is self._page}
                        for i, tab in enumerate(pages)
                    ]
                }
            if action == "new":
                self._page = await self._context.new_page()
                self._page.set_default_timeout(self.timeout_ms)
                self._watch(self._page)
                return {"opened": True}
            if index < 0 or index >= len(pages):
                raise ToolExecutionError(
                    f"No tab {index}: there {'is' if len(pages) == 1 else 'are'} {len(pages)}."
                )
            chosen = pages[index]
            if action == "close":
                await chosen.close()
                remaining = self._open_pages()
                self._page = remaining[0] if remaining else None
                self.last_elements = []
                return {"closed": index}
            if action == "switch":
                self._page = chosen
                await chosen.bring_to_front()
                self.last_elements = []
                return {"switched": index}
            raise ToolExecutionError(f"Unknown tab action {action!r}: list, switch, new, close.")

        result = await self._attempt(run)
        if action in {"list"}:
            return result
        return {**result, **await self.read()}

    # ------------------------------------------------------------- finding one

    async def _locate(self, page: Any, target: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """The locator for what the model named, and what the read knew about it.

        A number is the reliable way in - it came from this page, a moment ago.
        A CSS selector or a piece of text is for what a read did not list:
        something inside a canvas-drawn widget, or a node the model found by
        running its own JavaScript.
        """

        if "element" in target and target["element"] is not None:
            record = self._element_at(int(target["element"]))
            frame = self._frame_of(record)
            return frame.locator(stamp_selector(int(record["ref"]))).first, record
        selector = str(target.get("selector") or "").strip()
        text = str(target.get("text") or "").strip()
        if not selector and not text:
            raise ToolExecutionError("Name what to act on: element, selector or text.")
        query = selector or f"text={text}"
        for frame in self._frames(page):
            locator = frame.locator(query).first
            try:
                if await locator.count():
                    return locator, {"tag": "", "text": selector or text}
            except Exception:  # noqa: BLE001 - detached frame, cross-origin
                continue
        raise ToolExecutionError(
            f"Nothing on this page matches {query!r}. Read the page and use one of "
            "the numbered elements, or check the selector."
        )

    def _frame_of(self, record: dict[str, Any]) -> Any:
        position = int(record.get("_frame", 0))
        if position < len(self.last_frames):
            frame = self.last_frames[position]
            try:
                if not frame.is_detached():
                    return frame
            except Exception:  # noqa: BLE001 - gone, fall through to the page
                pass
        raise ToolExecutionError(
            "The frame that element was in is gone. Read the page again for fresh numbers."
        )

    async def _point(self, page: Any, target: dict[str, Any]) -> tuple[float, float]:
        """The middle of an element in page coordinates, for the mouse to use."""

        if "x" in target and "y" in target:
            return float(target["x"]), float(target["y"])
        locator, record = await self._locate(page, target)
        await locator.scroll_into_view_if_needed(timeout=self.timeout_ms)
        box = await locator.bounding_box()
        if not box:
            raise ToolExecutionError(f"{describe(record)} has no position on screen to drag from.")
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def _element_at(self, index: int) -> dict[str, Any]:
        for element in self.last_elements:
            if int(element.get("index", -1)) == index:
                return element
        raise ToolExecutionError(
            f"No element {index} on this page. Read the page first, then use one "
            f"of the numbers it lists ({len(self.last_elements)} available)."
        )

    async def _act(self, action: Any) -> dict[str, Any]:
        """Do something, then report the page it left behind.

        Always a fresh read: the numbers from before the action describe a page
        that no longer exists, and the next action must not use them.
        """

        await self._attempt(action)
        return await self.read()

    @staticmethod
    async def _select_all(page: Any) -> None:
        modifier = "Meta" if sys.platform == "darwin" else "Control"
        await page.keyboard.press(f"{modifier}+a")

    @staticmethod
    async def _pause(page: Any, ms: int) -> None:
        try:
            await page.wait_for_timeout(ms)
        except Exception:  # noqa: BLE001 - page went away mid-wait
            return

    async def _settle(self, page: Any) -> None:
        """Give a click that navigates a moment to land, without insisting."""

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:  # noqa: BLE001 - a click that navigates nowhere is fine
            return

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001 - already gone
                pass
            self._context = None
            self._page = None
        await self._stop_playwright()

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None
