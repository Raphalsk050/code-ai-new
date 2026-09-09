"""The browser as the agent sees it: read a page, click a number, type into one."""

from __future__ import annotations

import base64
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema

# Reading a page is the one browser action worth describing once and reusing:
# every tool here answers with the page it left behind, so the model never has
# to ask what happened after acting.
_RESULT_NOTE = (
    "Answers with the page as it now stands: its url and title, its readable "
    "text, and the elements that can be clicked, each with a number. Use those "
    "numbers with browser_click and browser_type."
)


def _session(context: ToolContext) -> Any:
    session = getattr(context, "browser", None)
    if session is None:
        raise ToolExecutionError(
            "The browser is not configured for this session (config: browser.enabled)."
        )
    return session


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    """Move a screenshot out of the JSON body and onto the result as an image."""

    shot = result.pop("screenshot_png", None)
    if shot:
        result[TOOL_IMAGES_KEY] = [
            {"data": base64.b64encode(shot).decode("ascii"), "media_type": "image/png"}
        ]
    return result


def _index(arguments: dict[str, Any]) -> int:
    raw = arguments.get("element")
    if raw is None:
        raise ToolArgumentError("element is required: the number from the page listing.")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ToolArgumentError("element must be the integer shown next to the element.") from None


_SCREENSHOT_FIELD = {
    "type": "boolean",
    "description": (
        "Attach a picture of the page as well as its text. Worth it when the "
        "layout matters or the text alone is ambiguous; the text is usually "
        "enough and costs far less."
    ),
}


class BrowserOpenTool:
    name = "browser_open"
    description = (
        "Open a URL in the agent's own browser and read the page. The browser is "
        "a real one, kept in a profile separate from the user's, and it stays "
        "logged in between sessions - so a site logged into once does not have "
        "to be logged into again. " + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            "url": {"type": "string", "description": "The address to open."},
            "screenshot": _SCREENSHOT_FIELD,
        },
        required=("url",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        url = str(arguments.get("url") or "").strip()
        if not url:
            raise ToolArgumentError("url is required.")
        if "://" not in url:
            url = f"https://{url}"
        session = _session(context)
        await session.goto(url)
        result = await session.read(screenshot=bool(arguments.get("screenshot")))
        await context.event_bus.emit(
            "browser.navigated", {"url": result.get("url")}, source="tool.browser_open"
        )
        return _payload(result)


class BrowserReadTool:
    name = "browser_read"
    description = (
        "Read the page the browser is on right now, without touching it. Use it "
        "after the user has done something themselves (logging in, dismissing a "
        "dialog) to see where that left things. " + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema({"screenshot": _SCREENSHOT_FIELD})

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        return _payload(await session.read(screenshot=bool(arguments.get("screenshot"))))


class BrowserClickTool:
    name = "browser_click"
    description = (
        "Click one of the numbered elements from the last page reading. Identify "
        "it by that number rather than by a CSS selector: the numbers come from "
        "the page as it actually is, and a selector written from memory can miss "
        "or hit the wrong thing without saying so. " + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            "element": {
                "type": "integer",
                "description": "The number shown beside the element to click.",
            },
            "screenshot": _SCREENSHOT_FIELD,
        },
        required=("element",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        await session.click_index(_index(arguments))
        return _payload(await session.read(screenshot=bool(arguments.get("screenshot"))))


class BrowserTypeTool:
    name = "browser_type"
    description = (
        "Type into one of the numbered fields from the last page reading, "
        "optionally pressing Enter afterwards. Never type a password or any "
        "other credential with this: hand the login to the user instead (see "
        "browser_request_login), so the secret stays between them and the site. "
        + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            "element": {
                "type": "integer",
                "description": "The number shown beside the field to type into.",
            },
            "text": {"type": "string", "description": "What to type."},
            "submit": {
                "type": "boolean",
                "description": "Press Enter after typing. Defaults to false.",
            },
            "screenshot": _SCREENSHOT_FIELD,
        },
        required=("element", "text"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        await session.type_into(
            _index(arguments),
            str(arguments.get("text") or ""),
            submit=bool(arguments.get("submit")),
        )
        return _payload(await session.read(screenshot=bool(arguments.get("screenshot"))))


class BrowserRequestLoginTool:
    """Bring the window forward and stop, so the user can sign in themselves.

    The agent must not be the thing that handles a password. It can navigate to
    the login page and it can read the result, but the typing in between is the
    user's - which also means the credential never enters the conversation, the
    transcript, or the model's context, where it would outlive the moment.

    Ending the turn is the mechanism. There is nothing to poll and nothing to
    time out: the user logs in while the agent is not running, and says so when
    they are done, which is the next turn.
    """

    name = "browser_request_login"
    description = (
        "Ask the user to sign in themselves, in the browser window the agent is "
        "driving. Call this the moment a page needs credentials - a login form, "
        "an SSO redirect, a two-factor prompt - instead of trying to fill them "
        "in. Navigate to the login page first so the user arrives at the right "
        "screen. This ends your turn: the user signs in, tells you when they "
        "are done, and the session persists in the browser profile afterwards, "
        "so this is asked once per site rather than once per task."
    )
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = tool_schema(
        {
            "site": {
                "type": "string",
                "description": "The site being signed in to, named for the user.",
            },
            "reason": {
                "type": "string",
                "description": "What you are trying to reach that needs the login.",
            },
        },
        required=("site",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        site = str(arguments.get("site") or "").strip()
        if not site:
            raise ToolArgumentError("site is required.")
        reason = str(arguments.get("reason") or "").strip()
        session = _session(context)
        # Read rather than assume: the user is about to be sent to a window,
        # and the answer should say which page it is actually showing.
        try:
            page = await session.read()
            url = page.get("url", "")
        except ToolExecutionError:
            url = ""
        message = (
            f"Sign in to {site} in the browser window I opened"
            + (f" ({url})" if url else "")
            + ". I will not type your credentials; tell me when you are done."
        )
        await context.event_bus.emit(
            "browser.login.requested",
            {"site": site, "reason": reason, "url": url},
            source="tool.browser_request_login",
        )
        return {
            "awaiting_user_login": True,
            "site": site,
            "url": url,
            "message": message,
            "next": (
                "Stop here and say the above to the user. When they confirm, call "
                "browser_read to see where the login left the page."
            ),
        }
