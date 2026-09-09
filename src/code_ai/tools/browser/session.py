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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolExecutionError

_INSTALL_HINT = (
    "Browser control needs Playwright. Install it with: "
    "pip install 'code-ai[browser]' && playwright install chromium"
)

# How long a navigation or an action may take before it is called a failure.
# A page that has not settled in this long is either very slow or waiting on
# something the agent cannot provide, and the turn must not hang on it.
DEFAULT_TIMEOUT_MS = 30_000

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

    async def page(self) -> Any:
        """The current page, starting the browser the first time it is asked for."""

        async with self._lock:
            return await self._page_unlocked()

    async def _page_unlocked(self) -> Any:
        if self._page is not None and not self._page.is_closed():
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
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # noqa: BLE001 - playwright is optional
            raise ToolExecutionError(_INSTALL_HINT) from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=self.headless,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:  # noqa: BLE001
            await self._stop_playwright()
            message = str(exc)
            if "executable doesn" in message.lower() or "playwright install" in message.lower():
                # The package is installed but the browser binary is not, which
                # is a separate step people miss and a separate thing to say.
                raise ToolExecutionError(
                    "Playwright is installed but its browser is not. Run: "
                    "playwright install chromium"
                ) from exc
            raise ToolExecutionError(f"Could not start the browser: {message}") from exc

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
