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

# Interactive elements offered to the model per read. Enough to cover a real
# page's controls without the list itself becoming the expensive part.
MAX_ELEMENTS = 60

# What the model is allowed to click: things a user could click, and nothing
# else. Collected in DOM order so the numbering matches reading order.
_INTERACTIVE_SELECTOR = (
    "a[href], button, input:not([type=hidden]), select, textarea, "
    "[role=button], [role=link], [role=tab], [role=checkbox], [onclick]"
)

# Runs in the page. Returns one record per interactive element: what it is,
# what it says, and where it is - so the agent can click by number instead of
# inventing a CSS selector for a DOM it cannot see.
_COLLECT_JS = """
(args) => {
  const [selector, limit] = args;
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    const rect = el.getBoundingClientRect();
    // Anything with no box is display:none, collapsed, or off in a detached
    // subtree. A user cannot click it, so it must not be offered as clickable.
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const label = (
      el.getAttribute('aria-label') ||
      el.value ||
      el.innerText ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('name') ||
      ''
    ).trim().replace(/\\s+/g, ' ').slice(0, 120);
    out.push({
      index: out.length,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      text: label,
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
    });
    if (out.length >= limit) break;
  }
  return out;
}
"""


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
    # The elements offered by the last read, so a click can name one by number.
    last_elements: list[dict[str, Any]] = field(default_factory=list)
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
            self.devtools.watch(self._page)
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
        self.devtools.watch(self._page)
        return self._page

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

    async def read(self, *, screenshot: bool = False) -> dict[str, Any]:
        """What the page says, what can be clicked on it, and optionally a picture."""

        return await self._attempt(lambda: self._read_unlocked(screenshot=screenshot))

    async def _read_unlocked(self, *, screenshot: bool = False) -> dict[str, Any]:
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
        if screenshot:
            payload["screenshot_png"] = await page.screenshot(type="png")
        return payload

    async def _elements(self, page: Any) -> list[dict[str, Any]]:
        try:
            found = await page.evaluate(_COLLECT_JS, [_INTERACTIVE_SELECTOR, MAX_ELEMENTS])
        except Exception:  # noqa: BLE001 - a page mid-navigation has no DOM yet
            return []
        return [item for item in found if isinstance(item, dict)]

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

    async def click_index(self, index: int) -> dict[str, Any]:
        """Click the numbered element from the last read.

        By number rather than by selector because the model is choosing from a
        list it was just given: a selector it invents describes a DOM it cannot
        see, and a wrong one either misses or hits something else silently.
        """

        async def click() -> None:
            # The page is secured first and the element looked up second, so a
            # rebuild in between invalidates the lookup rather than being
            # overtaken by it. Reversed, the coordinates would come from the
            # page that just died and the click would land somewhere on its
            # replacement without anything saying so.
            page = await self._page_unlocked()
            element = self._element_at(index)
            # Located again by position rather than held as a handle: anything
            # the page did since the read may have replaced the node, and a
            # stale handle throws where a fresh coordinate still lands.
            await page.mouse.click(element["x"], element["y"])
            await self._settle(page)

        await self._attempt(click)
        return await self.read()

    async def type_into(self, index: int, text: str, *, submit: bool = False) -> dict[str, Any]:
        async def enter() -> None:
            page = await self._page_unlocked()
            element = self._element_at(index)
            await page.mouse.click(element["x"], element["y"])
            await page.keyboard.type(text)
            if submit:
                await page.keyboard.press("Enter")
                await self._settle(page)

        await self._attempt(enter)
        return await self.read()

    def _element_at(self, index: int) -> dict[str, Any]:
        for element in self.last_elements:
            if int(element.get("index", -1)) == index:
                return element
        raise ToolExecutionError(
            f"No element {index} on this page. Read the page first, then use one "
            f"of the numbers it lists ({len(self.last_elements)} available)."
        )

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
