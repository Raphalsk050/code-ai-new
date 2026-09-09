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

    async def page(self) -> Any:
        """The current page, starting the browser the first time it is asked for."""

        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page
            if self._context is None:
                await self._start()
            # A persistent context opens with one blank page; reuse it rather
            # than leaving an empty window beside the one being driven.
            pages = [page for page in self._context.pages if not page.is_closed()]
            self._page = pages[0] if pages else await self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            return self._page

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

    async def read(self, *, screenshot: bool = False) -> dict[str, Any]:
        """What the page says, what can be clicked on it, and optionally a picture."""

        page = await self.page()
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
        page = await self.page()
        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        return await self.read()

    async def click_index(self, index: int) -> dict[str, Any]:
        """Click the numbered element from the last read.

        By number rather than by selector because the model is choosing from a
        list it was just given: a selector it invents describes a DOM it cannot
        see, and a wrong one either misses or hits something else silently.
        """

        element = self._element_at(index)
        page = await self.page()
        # Located again by position rather than held as a handle: anything the
        # page did since the read may have replaced the node, and a stale
        # handle throws where a fresh coordinate still lands correctly.
        await page.mouse.click(element["x"], element["y"])
        await self._settle(page)
        return await self.read()

    async def type_into(self, index: int, text: str, *, submit: bool = False) -> dict[str, Any]:
        element = self._element_at(index)
        page = await self.page()
        await page.mouse.click(element["x"], element["y"])
        await page.keyboard.type(text)
        if submit:
            await page.keyboard.press("Enter")
            await self._settle(page)
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
