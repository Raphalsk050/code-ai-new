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
    BrowserClickTool,
    BrowserOpenTool,
    BrowserReadTool,
    BrowserRequestLoginTool,
    BrowserSession,
    BrowserTypeTool,
)
from code_ai.util.paths import WorkspacePolicy


class FakePage:
    """A page that records what was done to it, and can be navigated."""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.clicks: list[tuple[int, int]] = []
        self.typed: list[str] = []
        self.keys: list[str] = []
        self.body = "Sign in to continue"
        self.elements = [
            {"index": 0, "tag": "input", "type": "email", "text": "Email", "x": 100, "y": 200},
            {"index": 1, "tag": "button", "type": "", "text": "Sign in", "x": 100, "y": 260},
        ]
        self.mouse = SimpleNamespace(click=self._click)
        self.keyboard = SimpleNamespace(type=self._type, press=self._press)

    async def _click(self, x, y):
        self.clicks.append((x, y))

    async def _type(self, text):
        self.typed.append(text)

    async def _press(self, key):
        self.keys.append(key)

    def is_closed(self):
        return False

    def set_default_timeout(self, ms):
        return None

    async def title(self):
        return "Login"

    async def inner_text(self, selector):
        return self.body

    async def evaluate(self, script, args):
        return self.elements

    async def goto(self, url, **kwargs):
        self.url = url

    async def wait_for_load_state(self, state, timeout=None):
        return None

    async def screenshot(self, type="png"):
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
    result = await BrowserOpenTool().execute({"url": "example.com"}, make_context(tmp_path, session))

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
    assert page.clicks[-1] == (100, 260)


async def test_typing_targets_the_field_and_can_submit(tmp_path) -> None:
    session, page = make_session(tmp_path)
    context = make_context(tmp_path, session)
    await BrowserReadTool().execute({}, context)

    await BrowserTypeTool().execute(
        {"element": 0, "text": "someone@example.com", "submit": True}, context
    )
    assert page.clicks[-1] == (100, 200)
    assert page.typed == ["someone@example.com"]
    assert page.keys == ["Enter"]


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
    session = BrowserSession(profile_dir=tmp_path / "profile")
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
    """They were measured on a page that no longer exists.

    The restored page is the same address but not the same render, so a click
    on the old coordinates would land somewhere on its replacement without
    anything saying so. Refusing costs one read; a blind click costs trust.
    """

    page = ClosingPage(fail_times=0)
    session = _recovering_session(tmp_path, page)
    await session.goto("https://example.com")
    assert session.last_elements  # numbered from the live page

    page.fail_times = 1
    with pytest.raises(ToolExecutionError) as caught:
        await session.click_index(0)
    assert "Read the page first" in str(caught.value)

    # After a fresh read the numbers describe the page that is really there.
    await session.read()
    await session.click_index(0)
    assert page.clicks[-1] == (100, 200)


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
