from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolContext
from code_ai.tools.browser import (
    BrowserActTool,
    BrowserClickTool,
    BrowserEvaluateTool,
    BrowserInspectTool,
    BrowserOpenTool,
    BrowserPageTool,
    BrowserReadTool,
    BrowserRequestLoginTool,
    BrowserScreenshotTool,
    BrowserSession,
    BrowserTypeTool,
    BrowserWaitTool,
)
from code_ai.tools.browser.devtools import MAX_DOM_DEPTH, MAX_HTML_CHARS, mask
from code_ai.tools.browser.dom import COLLECT_JS, STAMP, VIEWPORT_JS
from code_ai.util.paths import WorkspacePolicy


class FakeLocator:
    """Records what was asked of it, so a test can assert on the action itself."""

    def __init__(self, page, selector) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        if self.selector == "#missing":
            return 0
        return 0 if self.selector in self.page.absent else 1

    async def aria_snapshot(self):
        return self.page.aria

    def _record(self, what, **detail):
        self.page.actions.append((what, {"selector": self.selector, **detail}))

    async def click(self, **kwargs):
        self._record("click", **kwargs)

    async def hover(self, **kwargs):
        self._record("hover")

    async def focus(self, **kwargs):
        self._record("focus")

    async def fill(self, text, **kwargs):
        self._record("fill", text=text)

    async def press(self, key, **kwargs):
        self._record("press", key=key)

    async def press_sequentially(self, text, **kwargs):
        self._record("type", text=text)

    async def select_option(self, values, **kwargs):
        if self.page.select_error is not None:
            raise self.page.select_error
        self._record("select", values=values)

    async def set_checked(self, checked, **kwargs):
        self._record("set_checked", checked=checked)

    async def set_input_files(self, paths, **kwargs):
        self._record("upload", paths=paths)

    async def scroll_into_view_if_needed(self, **kwargs):
        self._record("scroll_into_view")

    async def bounding_box(self):
        return self.page.boxes.get(self.selector, {"x": 10, "y": 20, "width": 40, "height": 10})

    async def screenshot(self, **kwargs):
        self._record("screenshot")
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


class FakePage:
    """A page that records what was done to it, and can be navigated."""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.clicks: list[tuple[int, int]] = []
        self.typed: list[str] = []
        self.keys: list[str] = []
        self.body = "Sign in to continue"
        self.elements = [
            {"ref": 0, "tag": "input", "type": "email", "text": "Email", "x": 100, "y": 200},
            {"ref": 1, "tag": "button", "type": "", "text": "Sign in", "x": 100, "y": 260},
        ]
        # What locators were asked to do, in order: (action, detail).
        self.actions: list[tuple[str, dict]] = []
        # Selectors that match nothing, so a test can make a lookup fail.
        self.absent: set[str] = set()
        self.boxes: dict[str, dict] = {}
        self.select_error: Exception | None = None
        # Selectors a wait will never see turn up, so a test can let one expire.
        self.never_arrives: set[str] = set()
        self.mouse = SimpleNamespace(
            click=self._click,
            move=self._move,
            down=self._down,
            up=self._up,
            wheel=self._wheel,
        )
        self.keyboard = SimpleNamespace(type=self._type, press=self._press)
        # A page is its own main frame here: one document, no iframes.
        self.frames = [self]
        self.main_frame = self
        # The listeners the session attached, so a test can fire page events.
        self.handlers: dict[str, list] = {}
        # What any script but the element listing gets back: a value, a
        # callable given (script, args), or an exception to raise.
        self.eval_result: object = None
        self.evaluated: list[tuple[str, object]] = []
        self.aria = '- heading "Login" [level=1]'

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def fire(self, event, payload):
        for handler in self.handlers.get(event, []):
            handler(payload)

    def locator(self, selector):
        return FakeLocator(self, selector)

    async def _click(self, x, y):
        self.clicks.append((x, y))

    async def _type(self, text):
        self.typed.append(text)

    async def _press(self, key):
        self.keys.append(key)

    async def _move(self, x, y, steps=1):
        self.actions.append(("move", {"x": x, "y": y, "steps": steps}))

    async def _down(self):
        self.actions.append(("mouse_down", {}))

    async def _up(self):
        self.actions.append(("mouse_up", {}))

    async def _wheel(self, dx, dy):
        self.actions.append(("wheel", {"dx": dx, "dy": dy}))

    def is_closed(self):
        return False

    def is_detached(self):
        return False

    def set_default_timeout(self, ms):
        return None

    async def title(self):
        return "Login"

    async def inner_text(self, selector):
        return self.body

    async def evaluate(self, script, args=None):
        if script == COLLECT_JS:
            return self.elements
        if script == VIEWPORT_JS:
            return {"scroll_y": 0, "page_height": 2000, "at_bottom": False}
        self.evaluated.append((script, args))
        if isinstance(self.eval_result, Exception):
            raise self.eval_result
        if callable(self.eval_result):
            return self.eval_result(script, args)
        return self.eval_result

    async def goto(self, url, **kwargs):
        self.url = url

    async def go_back(self, **kwargs):
        self.actions.append(("back", {}))

    async def go_forward(self, **kwargs):
        self.actions.append(("forward", {}))

    async def reload(self, **kwargs):
        self.actions.append(("reload", {}))

    async def wait_for_load_state(self, state, timeout=None):
        return None

    async def wait_for_selector(self, selector, timeout=None):
        self.actions.append(("wait_selector", {"selector": selector}))
        if selector in self.never_arrives:
            raise RuntimeError(f"Timeout {timeout}ms exceeded waiting for {selector}")
        return FakeLocator(self, selector)

    async def wait_for_url(self, url, timeout=None):
        self.actions.append(("wait_url", {"url": url}))

    async def wait_for_timeout(self, ms):
        return None

    async def bring_to_front(self):
        self.actions.append(("front", {}))

    async def close(self):
        return None

    async def screenshot(self, type="png", full_page=False):
        self.actions.append(("page_screenshot", {"full_page": full_page}))
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def make_session(tmp_path: Path) -> tuple[BrowserSession, FakePage]:
    page = FakePage()
    session = BrowserSession(profile_dir=tmp_path / "profile")
    session._page = page
    session._context = SimpleNamespace(pages=[page])
    return session, page


def make_context(tmp_path: Path, session) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        browser=session,
    )


# ------------------------------------------------------------------ reading


async def test_a_page_comes_back_with_its_text_and_what_can_be_clicked(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    result = await BrowserOpenTool().execute({"url": "example.com"}, context)

    # A bare host is not a URL; a browser would treat it as a search term.
    assert page.url == "https://example.com"
    assert result["title"] == "Login"
    assert "Sign in to continue" in result["text"]
    assert [element["text"] for element in result["elements"]] == ["Email", "Sign in"]


async def test_a_long_page_is_bounded_like_any_other_tool_output(tmp_path) -> None:
    """A long article would otherwise cost the turn to answer "what is here"."""

    session, page = make_session(tmp_path)
    page.body = "x" * 50_000
    result = await BrowserReadTool().execute({}, make_context(tmp_path, session))

    assert len(result["text"]) < 10_000
    assert result["text_truncated"] is True


async def test_a_screenshot_travels_as_an_image_not_as_json(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    context = make_context(tmp_path, session)

    plain = await BrowserReadTool().execute({}, context)
    assert TOOL_IMAGES_KEY not in plain

    shot = await BrowserReadTool().execute({"screenshot": True}, context)
    assert shot[TOOL_IMAGES_KEY][0]["media_type"] == "image/png"
    # The raw bytes are gone from the body, where they would be serialised.
    assert "screenshot_png" not in shot


# ------------------------------------------------------------------ acting


async def test_an_element_is_clicked_by_its_number_from_the_listing(tmp_path) -> None:
    """The numbers describe the page as it is; a selector would be invented."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserClickTool().execute({"element": 1}, context)
    action, detail = page.actions[-1]
    assert action == "click"
    # The stamp the read put on that element, not the coordinates beside it.
    assert detail["selector"] == f"[{STAMP}='1']"


async def test_typing_targets_the_field_and_can_submit(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserTypeTool().execute(
        {"element": 0, "text": "someone@example.com", "submit": True}, context
    )
    done = [(what, detail.get("text") or detail.get("key")) for what, detail in page.actions]
    assert ("type", "someone@example.com") in done
    assert ("press", "Enter") in done


async def test_a_number_that_is_not_on_the_page_is_refused_not_guessed(tmp_path) -> None:
    """Clicking a coordinate that came from nowhere is worse than an error."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    with pytest.raises(ToolExecutionError) as caught:
        await BrowserClickTool().execute({"element": 99}, context)
    assert "99" in str(caught.value)
    assert page.clicks == []


async def test_clicking_before_reading_a_page_is_refused(tmp_path) -> None:
    session, page = make_session(tmp_path)
    with pytest.raises(ToolExecutionError):
        await BrowserClickTool().execute({"element": 0}, make_context(tmp_path, session))
    assert page.clicks == []


async def test_a_missing_element_number_is_an_argument_error(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    with pytest.raises(ToolArgumentError):
        await BrowserClickTool().execute({}, make_context(tmp_path, session))


# ------------------------------------------------------------------ login


async def test_the_login_is_handed_to_the_user_rather_than_typed(tmp_path) -> None:
    """The agent must never be the thing that handles a password.

    It navigates to the login page and reads the result; the typing between
    those two is the user's, which is also what keeps the credential out of the
    conversation, the transcript and the model's context.
    """

    session, page = make_session(tmp_path)
    page.url = "https://example.com/login"
    context = make_context(tmp_path, session)
    seen: list[dict] = []
    context.event_bus.subscribe(
        lambda event: seen.append(event.payload)
        if event.event_type == "browser.login.requested"
        else None
    )

    result = await BrowserRequestLoginTool().execute(
        {"site": "Example", "reason": "read the dashboard"}, context
    )

    assert result["awaiting_user_login"] is True
    assert result["url"] == "https://example.com/login"
    assert "will not type your credentials" in result["message"]
    # Nothing was typed and nothing was clicked on the user's behalf.
    assert page.typed == [] and page.clicks == []


async def test_the_login_request_names_the_site(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    with pytest.raises(ToolArgumentError):
        await BrowserRequestLoginTool().execute({}, make_context(tmp_path, session))


# ------------------------------------------------------------------ isolation


def test_the_profile_is_the_agents_own_and_not_the_users(tmp_path) -> None:
    """Driving the profile someone reads their mail in would expose all of it."""

    from code_ai.config.defaults import project_browser_dir

    profile = project_browser_dir(tmp_path)
    assert ".code-ai" in profile.parts
    assert profile.name == "browser"
    # Per workspace, so two projects never share a logged-in session.
    assert project_browser_dir(tmp_path) != project_browser_dir(tmp_path / "other")


async def test_without_playwright_the_error_says_how_to_install_it(tmp_path, monkeypatch) -> None:
    # Automatic installs off: this is about what is said, not about pip.
    session = BrowserSession(profile_dir=tmp_path / "profile", auto_install=False)
    monkeypatch.setitem(__import__("sys").modules, "playwright.async_api", None)

    with pytest.raises(ToolExecutionError) as caught:
        await session._start()
    assert "playwright" in str(caught.value).lower()


async def test_the_browser_tools_say_so_when_it_is_disabled(tmp_path) -> None:
    context = make_context(tmp_path, None)
    with pytest.raises(ToolExecutionError) as caught:
        await BrowserReadTool().execute({}, context)
    assert "browser.enabled" in str(caught.value)


# ------------------------------------------------------------------ recovery


class ClosingPage(FakePage):
    """A page whose browser dies once, the way a closed window behaves.

    Playwright does not fail at the moment the window goes; it fails on the
    next call, with the handle still looking alive.
    """

    def __init__(self, fail_times: int = 1) -> None:
        super().__init__()
        self.fail_times = fail_times
        self.gotos: list[str] = []

    def _maybe_die(self) -> None:
        # A closed browser fails whatever it is asked, not only navigation.
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("Target page, context or browser has been closed")

    async def goto(self, url, **kwargs):
        self._maybe_die()
        self.gotos.append(url)
        self.url = url

    async def _click(self, x, y):
        self._maybe_die()
        await super()._click(x, y)

    async def inner_text(self, selector):
        self._maybe_die()
        return await super().inner_text(selector)

    def locator(self, selector):
        return ClosingLocator(self, selector)


class ClosingLocator(FakeLocator):
    """Acting on an element of a dead page fails the same way the page does."""

    async def click(self, **kwargs):
        self.page._maybe_die()
        await super().click(**kwargs)


def _recovering_session(tmp_path: Path, page: ClosingPage) -> BrowserSession:
    session = BrowserSession(profile_dir=tmp_path / "profile")
    session._page = page
    session._context = SimpleNamespace(pages=[page], close=_noop)

    async def restart():
        # A fresh browser: the same fake, no longer failing.
        page.fail_times = 0
        session._context = SimpleNamespace(pages=[page], close=_noop)

    session._start = restart
    return session


async def _noop(*args, **kwargs):
    return None


async def test_a_window_the_user_closed_is_reopened_instead_of_failing(tmp_path) -> None:
    """Closing the window is a normal thing to do; it must not end the task.

    It used to leave the session holding a dead handle that answered every
    later call with "target page, context or browser has been closed", so the
    agent could not browse again for the rest of the session.
    """

    page = ClosingPage()
    session = _recovering_session(tmp_path, page)

    result = await session.goto("https://example.com")

    assert result["url"] == "https://example.com"
    assert page.gotos == ["https://example.com"]


async def test_the_rebuilt_browser_goes_back_to_where_it_was(tmp_path) -> None:
    """A blank tab would answer "nothing here" about a site open a second ago."""

    page = ClosingPage(fail_times=0)
    session = _recovering_session(tmp_path, page)
    await session.goto("https://example.com/dashboard")

    page.fail_times = 1  # the user closes the window
    result = await session.read()

    assert result["url"] == "https://example.com/dashboard"
    assert page.gotos[-1] == "https://example.com/dashboard"


async def test_element_numbers_do_not_survive_a_rebuild(tmp_path) -> None:
    """The stamps went with the DOM that carried them.

    The restored page is the same address but not the same render, so number 0
    means nothing on it. Refusing costs one read; acting blind costs trust.
    """

    page = ClosingPage(fail_times=0)
    session = _recovering_session(tmp_path, page)
    await session.goto("https://example.com")
    assert session.last_elements  # numbered from the live page

    page.fail_times = 1
    with pytest.raises(ToolExecutionError) as caught:
        await session.click({"element": 0})
    assert "Read the page first" in str(caught.value)

    # After a fresh read the numbers describe the page that is really there.
    await session.read()
    await session.click({"element": 0})
    assert page.actions[-1][0] == "click"


async def test_a_failure_that_is_not_a_closed_browser_is_not_retried(tmp_path) -> None:
    """Retrying a real error forever would turn a broken page into a hung turn."""

    page = ClosingPage(fail_times=0)
    attempts: list[str] = []

    async def refuse(url, **kwargs):
        attempts.append(url)
        raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

    page.goto = refuse
    session = _recovering_session(tmp_path, page)

    with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
        await session.goto("https://nope.invalid")
    assert len(attempts) == 1


def test_the_closed_browser_signatures_are_recognised() -> None:
    from code_ai.tools.browser.session import _is_closed_error

    for message in (
        "Target page, context or browser has been closed",
        "Browser has been closed",
        "Target closed",
        "Connection closed while reading from the driver",
    ):
        assert _is_closed_error(RuntimeError(message)), message
    assert not _is_closed_error(RuntimeError("net::ERR_CONNECTION_REFUSED"))


# ------------------------------------------------------------------ devtools


def _console(text, level="log", url="https://example.com/app.js", line=9):
    return SimpleNamespace(type=level, text=text, location={"url": url, "lineNumber": line})


def _request(url, *, kind="fetch", failure=None):
    return SimpleNamespace(
        method="GET", url=url, resource_type=kind, timing={"responseEnd": 42.4}, failure=failure
    )


async def test_the_console_keeps_what_the_page_logged_and_its_uncaught_errors(tmp_path) -> None:
    """Console output cannot be read after the fact; it is captured as it happens."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    page.fire("console", _console("booting"))
    page.fire("console", _console("slow image", level="warning"))
    page.fire("pageerror", RuntimeError("TypeError: x is undefined"))

    result = await BrowserInspectTool().execute({"aspect": "console"}, context)
    assert [entry["text"] for entry in result["entries"]] == [
        "booting",
        "slow image",
        "Uncaught TypeError: x is undefined",
    ]
    # Numbered the way an editor shows the line, not Playwright's from-zero count.
    assert result["entries"][0]["source"] == "https://example.com/app.js:10"

    errors = await BrowserInspectTool().execute({"aspect": "console", "level": "error"}, context)
    assert [entry["level"] for entry in errors["entries"]] == ["error"]


async def test_listeners_are_attached_once_however_often_the_page_is_read(tmp_path) -> None:
    """Attached per read, every message would be logged once per read so far."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    for _ in range(3):
        await BrowserReadTool().execute({}, context)
    assert len(page.handlers["console"]) == 1


async def test_the_network_log_shows_status_type_and_time_and_filters(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    ok = _request("https://api.example.com/items")
    headers = {"content-type": "application/json; charset=utf-8"}
    page.fire("response", SimpleNamespace(request=ok, status=200, headers=headers))
    page.fire("requestfinished", ok)
    missing = _request("https://api.example.com/users/7")
    page.fire("response", SimpleNamespace(request=missing, status=404, headers={}))
    font = _request("https://cdn.example.com/a.woff2", kind="font", failure="net::ERR_FAILED")
    page.fire("requestfailed", font)

    everything = await BrowserInspectTool().execute({"aspect": "network"}, context)
    assert everything["entries"][0] == {
        "method": "GET",
        "url": "https://api.example.com/items",
        "type": "fetch",
        "status": 200,
        "mime": "application/json",
        "ms": 42,
    }

    failed = await BrowserInspectTool().execute(
        {"aspect": "network", "failed_only": True}, context
    )
    outcomes = [entry.get("status") or entry.get("failed") for entry in failed["entries"]]
    assert outcomes == [404, "net::ERR_FAILED"]

    api = await BrowserInspectTool().execute(
        {"aspect": "network", "filter": "api.", "clear": True}, context
    )
    assert len(api["entries"]) == 2
    # Cleared, so the next read shows only what happens from here on.
    assert (await BrowserInspectTool().execute({"aspect": "network"}, context))["entries"] == []


async def test_reading_the_logs_does_not_start_a_browser(tmp_path) -> None:
    session = BrowserSession(profile_dir=tmp_path / "profile")

    async def refuse():
        raise AssertionError("the browser was started just to read an empty log")

    session._start = refuse
    result = await session.inspect("console")
    assert result["entries"] == []
    assert "Nothing captured yet" in result["note"]


def test_tokens_are_masked_in_text_bound_for_the_model() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"
    assert "<hidden token>" in mask(f"auth failed for {jwt}")
    assert mask("Authorization: Bearer abcdefghijklmnop123") == "Authorization: Bearer <hidden>"
    assert mask("https://x.com/cb?code=abc123&state=ok") == "https://x.com/cb?code=<hidden>&state=ok"


async def test_the_dom_tree_comes_back_compact_and_depth_is_bounded(tmp_path) -> None:
    session, page = make_session(tmp_path)
    page.eval_result = {
        "found": True,
        "matches": 1,
        "tree": 'body\n  div#app.shell\n    button "Save"',
        "truncated": False,
    }
    result = await BrowserInspectTool().execute(
        {"aspect": "dom", "depth": 999}, make_context(tmp_path, session)
    )
    assert "div#app.shell" in result["tree"]
    _, args = page.evaluated[-1]
    assert args[1] == MAX_DOM_DEPTH


async def test_a_bad_selector_is_an_argument_error_and_no_match_is_an_answer(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)

    page.eval_result = {"error": "invalid selector"}
    with pytest.raises(ToolArgumentError):
        await BrowserInspectTool().execute({"aspect": "html", "selector": "div[[["}, context)

    page.eval_result = {"found": False}
    result = await BrowserInspectTool().execute({"aspect": "html", "selector": "#nope"}, context)
    assert result["found"] is False
    assert "#nope" in result["note"]


async def test_long_html_is_paged_rather_than_cut_off(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    page.eval_result = {"found": True, "matches": 1, "html": "<html>" + "a" * 20_000 + "</html>"}

    first = await BrowserInspectTool().execute({"aspect": "html"}, context)
    assert len(first["html"]) == MAX_HTML_CHARS
    assert f"offset={MAX_HTML_CHARS}" in first["next"]

    second = await BrowserInspectTool().execute(
        {"aspect": "html", "offset": MAX_HTML_CHARS}, context
    )
    assert second["range"][0] == MAX_HTML_CHARS


async def test_styles_show_computed_values_and_the_rules_that_match(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    with pytest.raises(ToolArgumentError):
        await BrowserInspectTool().execute({"aspect": "styles"}, context)

    page.eval_result = {
        "found": True,
        "matches": 2,
        "tag": "button",
        "attributes": {"class": "primary"},
        "box": {"x": 10, "y": 20, "width": 80, "height": 32},
        "computed": {"display": "flex"},
        "rules": [{"selector": ".primary", "css": "color: red;", "source": "https://x/app.css"}],
        "unreadable": 1,
    }
    result = await BrowserInspectTool().execute(
        {"aspect": "styles", "selector": ".primary"}, context
    )
    assert result["computed"] == {"display": "flex"}
    assert result["rules"][0]["selector"] == ".primary"
    assert result["shown"] == "the first match"
    # A cross-origin sheet cannot be read from the page, and the answer says so.
    assert "other origins" in result["note"]


async def test_storage_never_reads_out_a_cookie_or_a_token(tmp_path) -> None:
    """The login lives in them; the model only needs to know they are there."""

    import json

    session, page = make_session(tmp_path)
    page.url = "https://app.example.com/"

    async def cookies(urls):
        return [
            {
                "name": "sid",
                "value": "s3cr3t-session-value",
                "domain": "app.example.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ]

    session._context = SimpleNamespace(pages=[page], cookies=cookies)
    page.eval_result = {
        "local_storage": [["theme", "dark"], ["auth_token", "abc"], ["blob", "A" * 40]],
        "session_storage": [],
        "indexeddb": ["app (v3)"],
        "service_workers": [],
        "cache_storage": [],
    }
    result = await BrowserInspectTool().execute(
        {"aspect": "storage"}, make_context(tmp_path, session)
    )

    cookie = result["cookies"][0]
    assert cookie["name"] == "sid"
    assert cookie["expires"] == "session"
    assert "s3cr3t" not in json.dumps(result)
    items = result["local_storage"]["items"]
    assert items["theme"] == "dark"
    assert items["auth_token"].startswith("<hidden")
    assert items["blob"].startswith("<hidden")
    assert result["indexeddb"] == ["app (v3)"]


async def test_the_accessibility_tree_comes_from_the_page(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    context = make_context(tmp_path, session)

    result = await BrowserInspectTool().execute({"aspect": "accessibility"}, context)
    assert 'heading "Login"' in result["tree"]

    missing = await BrowserInspectTool().execute(
        {"aspect": "accessibility", "selector": "#missing"}, context
    )
    assert missing["found"] is False


async def test_the_accessibility_tree_never_reads_out_a_password(tmp_path) -> None:
    """A textbox's value is in the snapshot, and for a password field it is the password."""

    session, page = make_session(tmp_path)
    page.aria = '- textbox "Email": someone@example.com\n- textbox "Password": hunter2'
    page.eval_result = ["hunter2"]
    result = await BrowserInspectTool().execute(
        {"aspect": "accessibility"}, make_context(tmp_path, session)
    )
    assert "hunter2" not in result["tree"]
    assert 'textbox "Password": <hidden>' in result["tree"]
    # Only the password: the rest of the form is what the model came to read.
    assert "someone@example.com" in result["tree"]


async def test_evaluate_returns_the_value_and_what_the_script_logged(tmp_path) -> None:
    import json

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)
    page.fire("console", _console("already there"))

    def run(script, args):
        page.fire("console", _console("counting links"))
        return {"links": 3}

    page.eval_result = run
    result = await BrowserEvaluateTool().execute({"expression": "countLinks()"}, context)

    assert result["type"] == "object"
    assert json.loads(result["value"]) == {"links": 3}
    # Only what this script printed, not what was already in the log.
    assert [entry["text"] for entry in result["console"]] == ["counting links"]


async def test_a_script_that_throws_says_what_it_threw(tmp_path) -> None:
    session, page = make_session(tmp_path)
    page.eval_result = RuntimeError(
        "Page.evaluate: ReferenceError: nope is not defined\n    at eval (<anonymous>)"
    )
    with pytest.raises(ToolExecutionError) as caught:
        await BrowserEvaluateTool().execute({"expression": "nope"}, make_context(tmp_path, session))
    assert "The script threw: ReferenceError: nope is not defined" in str(caught.value)
    assert "at eval" not in str(caught.value)


# ------------------------------------------------- pointing, typing, dragging


async def test_a_click_carries_the_button_and_the_keys_held_with_it(tmp_path) -> None:
    """Right-click opens a context menu; a held key is what multi-select is."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserClickTool().execute(
        {"element": 1, "button": "right", "modifiers": ["Shift"]}, context
    )
    what, detail = page.actions[-1]
    assert what == "click"
    assert detail["button"] == "right"
    assert detail["modifiers"] == ["Shift"]

    await BrowserClickTool().execute({"element": 1, "double": True}, context)
    assert page.actions[-1][1]["click_count"] == 2


async def test_replacing_a_field_fills_it_but_an_editor_gets_real_keystrokes(tmp_path) -> None:
    """A document listens to keydown; fill would change the value behind its back."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    page.elements = [
        {"ref": 0, "tag": "input", "type": "text", "text": "Title", "x": 1, "y": 1},
        {"ref": 1, "tag": "div", "text": "Slide body", "editable": True, "x": 1, "y": 1},
    ]
    await BrowserReadTool().execute({}, context)

    await BrowserTypeTool().execute({"element": 0, "text": "Q3", "replace": True}, context)
    assert ("fill", "Q3") in [(what, d.get("text")) for what, d in page.actions]

    page.actions.clear()
    await BrowserTypeTool().execute({"element": 1, "text": "Revenue", "replace": True}, context)
    assert ("type", "Revenue") in [(what, d.get("text")) for what, d in page.actions]
    # Selected what was there first, so the typing replaces rather than appends.
    assert page.keys[-1].endswith("+a")


async def test_a_chord_and_a_sequence_of_keys_both_go_through(tmp_path) -> None:
    """Bold in a document is Control+b, and there is no button to click for it."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserActTool().execute({"action": "press", "keys": "Control+b"}, context)
    assert page.keys[-1] == "Control+b"

    await BrowserActTool().execute({"action": "press", "keys": "Control+a, Delete"}, context)
    assert page.keys[-2:] == ["Control+a", "Delete"]


async def test_keys_can_be_aimed_at_one_element_rather_than_the_page(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserActTool().execute({"action": "press", "keys": "Escape", "element": 1}, context)
    assert ("press", "Escape") in [(what, d.get("key")) for what, d in page.actions]


async def test_a_dropdown_is_set_by_value_and_a_checkbox_by_state(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    page.elements = [
        {"ref": 0, "tag": "select", "text": "Theme", "x": 1, "y": 1},
        {"ref": 1, "tag": "input", "type": "checkbox", "text": "Notify", "checked": False},
    ]
    await BrowserReadTool().execute({}, context)

    await BrowserActTool().execute({"action": "select", "element": 0, "values": ["dark"]}, context)
    assert ("select", ["dark"]) in [(what, d.get("values")) for what, d in page.actions]

    await BrowserActTool().execute({"action": "check", "element": 1}, context)
    assert ("set_checked", True) in [(what, d.get("checked")) for what, d in page.actions]

    await BrowserActTool().execute({"action": "uncheck", "element": 1}, context)
    assert ("set_checked", False) in [(what, d.get("checked")) for what, d in page.actions]


async def test_a_dropdown_that_is_not_a_select_says_to_click_the_option(tmp_path) -> None:
    """Half the dropdowns on the web are divs, and select_option cannot see them."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    page.select_error = RuntimeError("Element is not a <select> element")
    await BrowserReadTool().execute({}, context)

    with pytest.raises(ToolExecutionError) as caught:
        await BrowserActTool().execute(
            {"action": "select", "element": 0, "values": ["dark"]}, context
        )
    assert "click the option instead" in str(caught.value)


async def test_a_drag_presses_moves_in_steps_and_releases(tmp_path) -> None:
    """A shape on a slide moves because of the moves in between, not the ends."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserActTool().execute({"action": "drag", "element": 0, "to_element": 1}, context)
    order = [what for what, _ in page.actions if what in {"move", "mouse_down", "mouse_up"}]
    assert order[0] == "move"
    assert order[1] == "mouse_down"
    assert order[-1] == "mouse_up"
    stepped = [detail for what, detail in page.actions if what == "move" and detail["steps"] > 1]
    assert stepped, "the drag must move in steps, or nothing watching mousemove reacts"


async def test_a_drag_can_go_by_an_offset_when_there_is_nothing_to_drop_on(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserActTool().execute({"action": "drag", "element": 0, "dx": 120, "dy": -40}, context)
    moves = [detail for what, detail in page.actions if what == "move"]
    # From the middle of the box the locator reported, by the offset asked for.
    assert (moves[-1]["x"], moves[-1]["y"]) == (30 + 120, 25 - 40)


async def test_a_drag_with_nowhere_to_go_is_refused(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    with pytest.raises(ToolArgumentError):
        await BrowserActTool().execute({"action": "drag", "element": 0}, context)


async def test_scrolling_reaches_an_element_a_wheel_or_the_bottom(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserActTool().execute({"action": "scroll", "element": 1}, context)
    assert "scroll_into_view" in [what for what, _ in page.actions]

    await BrowserActTool().execute({"action": "scroll", "dy": 600}, context)
    assert ("wheel", 600) in [(what, d.get("dy")) for what, d in page.actions]

    page.evaluated.clear()
    await BrowserActTool().execute({"action": "scroll", "to": "bottom"}, context)
    assert any("scrollHeight" in str(script) for script, _ in page.evaluated)


async def test_files_are_handed_to_the_input_not_typed_into_it(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    page.elements = [{"ref": 0, "tag": "input", "type": "file", "accepts_files": True}]
    await BrowserReadTool().execute({}, context)

    await BrowserActTool().execute(
        {"action": "upload", "element": 0, "paths": ["deck.pptx"]}, context
    )
    assert ("upload", ["deck.pptx"]) in [(what, d.get("paths")) for what, d in page.actions]


# -------------------------------------------------------------- moving around


async def test_history_and_reload_go_through_the_page_itself(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)

    for action in ("back", "forward", "reload"):
        await BrowserPageTool().execute({"action": action}, context)
        assert action in [what for what, _ in page.actions]


async def test_waiting_is_for_a_thing_happening_not_for_a_number_of_seconds(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)

    await BrowserWaitTool().execute({"text": "Saved"}, context)
    waited = [(what, d.get("selector")) for what, d in page.actions]
    assert ("wait_selector", "text=Saved") in waited

    await BrowserWaitTool().execute({"selector": ".chart"}, context)
    assert ("wait_selector", ".chart") in [(what, d.get("selector")) for what, d in page.actions]

    await BrowserWaitTool().execute({"url": "**/done"}, context)
    assert ("wait_url", "**/done") in [(what, d.get("url")) for what, d in page.actions]


async def test_waiting_for_nothing_in_particular_is_refused(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    with pytest.raises(ToolExecutionError):
        await BrowserWaitTool().execute({}, make_context(tmp_path, session))


async def test_a_picture_can_be_of_one_element_rather_than_the_page(tmp_path) -> None:
    """Checking a chart drew is a question about the chart, not about the page."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    result = await BrowserScreenshotTool().execute({"element": 1}, context)
    assert result[TOOL_IMAGES_KEY][0]["media_type"] == "image/png"
    assert "Sign in" in result["of"]
    assert "screenshot" in [what for what, _ in page.actions]

    whole = await BrowserScreenshotTool().execute({"full_page": True}, context)
    assert whole["of"] == "page"
    assert ("page_screenshot", True) in [(what, d.get("full_page")) for what, d in page.actions]


# -------------------------------------------------------- what a page throws up


async def test_a_dialog_is_answered_and_then_reported(tmp_path) -> None:
    """Nothing else would answer it, and the page stops dead until something does."""

    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    answered: list[str] = []

    async def accept():
        answered.append("accept")

    async def dismiss():
        answered.append("dismiss")

    page.fire(
        "dialog",
        SimpleNamespace(
            type="confirm", message="Delete this slide?", accept=accept, dismiss=dismiss
        ),
    )
    await asyncio.sleep(0)

    assert answered == ["accept"]
    result = await BrowserReadTool().execute({}, context)
    assert result["dialogs"][0]["message"] == "Delete this slide?"
    # Reported once: the next read is about the page, not about old news.
    assert "dialogs" not in await BrowserReadTool().execute({}, context)


async def test_a_tab_a_link_opened_becomes_the_one_being_driven(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    popup = FakePage()
    popup.url = "https://example.com/report"
    page.fire("popup", popup)
    session._context = SimpleNamespace(pages=[page, popup])

    result = await BrowserReadTool().execute({}, context)
    assert result["url"] == "https://example.com/report"
    assert [tab["url"] for tab in result["tabs"]] == [page.url, popup.url]


async def test_tabs_can_be_listed_and_switched_between(tmp_path) -> None:
    session, page = make_session(tmp_path)
    other = FakePage()
    other.url = "https://example.com/other"
    session._context = SimpleNamespace(pages=[page, other])
    context = make_context(tmp_path, session)

    listed = await BrowserPageTool().execute({"action": "tabs"}, context)
    assert [tab["index"] for tab in listed["tabs"]] == [0, 1]

    await BrowserPageTool().execute({"action": "switch", "tab": 1}, context)
    assert session._page is other
    assert "front" in [what for what, _ in other.actions]


async def test_switching_to_a_tab_that_is_not_there_says_how_many_are(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    with pytest.raises(ToolExecutionError) as caught:
        await BrowserPageTool().execute(
            {"action": "switch", "tab": 5}, make_context(tmp_path, session)
        )
    assert "there is 1" in str(caught.value)


# ------------------------------------------------------- frames and selectors


async def test_an_element_inside_an_iframe_is_numbered_and_acted_on_in_its_frame(tmp_path) -> None:
    """A slide editor puts its canvas in an iframe; the document around it has no buttons."""

    session, page = make_session(tmp_path)
    inner = FakePage()
    inner.elements = [{"ref": 0, "tag": "button", "text": "Insert shape", "x": 5, "y": 5}]
    page.frames = [page, inner]
    context = make_context(tmp_path, session)

    result = await BrowserReadTool().execute({}, context)
    numbers = {element["text"]: element["index"] for element in result["elements"]}
    assert numbers["Insert shape"] == 2
    assert result["elements"][2]["frame"] == 1

    await BrowserClickTool().execute({"element": numbers["Insert shape"]}, context)
    # Clicked in the frame that holds it, not in the document around it.
    assert inner.actions[-1][0] == "click"
    assert "click" not in [what for what, _ in page.actions]


async def test_something_the_reading_did_not_list_can_be_named_by_selector(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserClickTool().execute({"selector": ".toolbar .bold"}, context)
    assert page.actions[-1][1]["selector"] == ".toolbar .bold"


async def test_a_selector_that_matches_nothing_says_so_instead_of_acting(tmp_path) -> None:
    session, page = make_session(tmp_path)
    page.absent.add(".gone")
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    with pytest.raises(ToolExecutionError) as caught:
        await BrowserClickTool().execute({"selector": ".gone"}, context)
    assert "Nothing on this page matches" in str(caught.value)


async def test_acting_without_naming_anything_is_refused(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    with pytest.raises(ToolArgumentError):
        await BrowserClickTool().execute({}, make_context(tmp_path, session))


async def test_a_reading_says_where_the_page_is_scrolled(tmp_path) -> None:
    """Otherwise there is no telling an empty page from one not scrolled to yet."""

    session, _ = make_session(tmp_path)
    result = await BrowserReadTool().execute({}, make_context(tmp_path, session))
    assert result["viewport"]["at_bottom"] is False
    assert result["viewport"]["page_height"] == 2000


async def test_a_downloaded_file_is_kept_where_it_can_be_opened_afterwards(tmp_path) -> None:
    """Playwright deletes it with the context; an export nobody kept is gone."""

    session, page = make_session(tmp_path)
    session.download_dir = tmp_path / "downloads"
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    saved: list[str] = []

    async def save_as(path):
        saved.append(path)

    page.fire(
        "download",
        SimpleNamespace(
            suggested_filename="deck.pdf", url="https://example.com/deck.pdf", save_as=save_as
        ),
    )
    await asyncio.sleep(0)

    assert saved == [str(tmp_path / "downloads" / "deck.pdf")]
    result = await BrowserReadTool().execute({}, context)
    assert result["downloads"][0]["file"].endswith("deck.pdf")
    # Reported once, like a dialog: the next read is about the page.
    assert "downloads" not in await BrowserReadTool().execute({}, context)


async def test_a_download_that_fails_says_so_rather_than_going_quiet(tmp_path) -> None:
    session, page = make_session(tmp_path)
    session.download_dir = tmp_path / "downloads"
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    async def save_as(path):
        raise RuntimeError("download was cancelled\n  at somewhere")

    page.fire(
        "download",
        SimpleNamespace(suggested_filename="deck.pdf", url="", save_as=save_as),
    )
    await asyncio.sleep(0)

    result = await BrowserReadTool().execute({}, context)
    assert result["downloads"][0]["failed"] == "download was cancelled"


async def test_a_filename_the_page_chose_cannot_escape_the_download_directory(tmp_path) -> None:
    """The name comes from the site, so it is not something to trust with a path."""

    session, page = make_session(tmp_path)
    session.download_dir = tmp_path / "downloads"
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    saved: list[str] = []

    async def save_as(path):
        saved.append(path)

    page.fire(
        "download",
        SimpleNamespace(suggested_filename="../../evil.sh", url="", save_as=save_as),
    )
    await asyncio.sleep(0)

    assert saved == [str(tmp_path / "downloads" / "evil.sh")]


async def test_a_wait_that_runs_out_of_time_answers_with_the_page(tmp_path) -> None:
    """The page is usually fine and the wording was wrong; an error hides that."""

    session, page = make_session(tmp_path)
    page.never_arrives.add("text=Saved")
    context = make_context(tmp_path, session)

    result = await BrowserWaitTool().execute({"text": "Saved"}, context)

    assert result["arrived"] is False
    assert result["waited_for"] == "text 'Saved'"
    assert "another wording" in result["note"]
    # The page it gave up on comes back, numbers and all, ready to act on.
    assert result["title"] == "Login"
    assert [element["text"] for element in result["elements"]] == ["Email", "Sign in"]


async def test_a_wait_that_arrives_says_so(tmp_path) -> None:
    session, _ = make_session(tmp_path)
    result = await BrowserWaitTool().execute({"selector": ".chart"}, make_context(tmp_path, session))
    assert result["arrived"] is True
    assert "note" not in result


async def test_a_wait_looks_in_every_frame_not_only_the_main_document(tmp_path) -> None:
    """A slide editor renders in an iframe, and nothing waited for is outside it."""

    session, page = make_session(tmp_path)
    inner = FakePage()
    page.frames = [page, inner]
    # The document around the editor never shows it; the frame does.
    page.never_arrives.add("text=Slide 2")
    context = make_context(tmp_path, session)

    result = await BrowserWaitTool().execute({"text": "Slide 2"}, context)
    assert result["arrived"] is True
    assert ("wait_selector", "text=Slide 2") in [(w, d.get("selector")) for w, d in inner.actions]


async def test_a_frame_that_fails_does_not_end_the_wait_for_the_others(tmp_path) -> None:
    session, page = make_session(tmp_path)
    detached = FakePage()
    detached.never_arrives.add("text=Ready")
    holder = FakePage()
    page.never_arrives.add("text=Ready")
    page.frames = [page, detached, holder]
    context = make_context(tmp_path, session)

    result = await BrowserWaitTool().execute({"text": "Ready"}, context)
    assert result["arrived"] is True


async def test_a_plain_pause_reports_no_verdict_to_read_into(tmp_path) -> None:
    """Time passing says nothing about the page, so there is nothing to claim."""

    session, _ = make_session(tmp_path)
    result = await BrowserWaitTool().execute({"seconds": 0.01}, make_context(tmp_path, session))
    assert "arrived" not in result
    assert result["title"] == "Login"


# --------------------------------------------------------- running out of time


class SlowLocator(FakeLocator):
    """An element that is there but never becomes actionable."""

    async def click(self, **kwargs):
        raise RuntimeError(
            'Timeout 30000ms exceeded.\nCall log:\n  - waiting for Locator("canvas") to be visible'
        )


async def test_an_action_that_times_out_reports_it_instead_of_failing_the_turn(tmp_path) -> None:
    """A banner over the button is a thing to work around, not a broken message.

    Acting waits for the element to be visible, still and uncovered. Raising on
    that would kill the message carrying the action and throw away the page
    that says what was in the way.
    """

    session, page = make_session(tmp_path)
    page.locator = lambda selector: SlowLocator(page, selector)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    result = await BrowserClickTool().execute({"element": 1}, context)

    assert result["done"] is False
    assert "The click did not go through within 30s" in result["note"]
    assert "Nothing on the page was changed" in result["note"]
    # And the page comes back, so the next move can be chosen from it.
    assert result["title"] == "Login"
    assert [element["text"] for element in result["elements"]] == ["Email", "Sign in"]


async def test_an_action_that_works_says_nothing_about_having_worked(tmp_path) -> None:
    """Only the exception is worth reporting; success is just the page."""

    session, _ = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    result = await BrowserClickTool().execute({"element": 1}, context)
    assert "done" not in result
    assert "note" not in result


async def test_a_closed_browser_is_still_a_restart_not_a_timeout_note(tmp_path) -> None:
    """The two look alike from the outside and want opposite handling."""

    page = ClosingPage(fail_times=0)
    session = _recovering_session(tmp_path, page)
    await session.goto("https://example.com")
    page.fail_times = 1

    with pytest.raises(ToolExecutionError):
        await session.click({"element": 0})
    # It rebuilt rather than shrugging: the numbers went with the old page.
    assert session.last_elements == []
