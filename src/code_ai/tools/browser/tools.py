"""The browser as the agent sees it: read a page, click a number, type into one."""

from __future__ import annotations

import base64
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.browser.devtools import (
    DEFAULT_DOM_DEPTH,
    DEFAULT_LOG_LIMIT,
    INSPECT_ASPECTS,
    LOG_LEVELS,
    MAX_DOM_DEPTH,
    MAX_LOG_ENTRIES,
)
from code_ai.tools.schema import tool_schema

# Reading a page is the one browser action worth describing once and reusing:
# every tool here answers with the page it left behind, so the model never has
# to ask what happened after acting.
_RESULT_NOTE = (
    "Answers with the page as it now stands: its url and title, its readable "
    "text, and the elements that can be acted on - each with a number, what it "
    "is, what it says and its state (checked, expanded, the options of a "
    "dropdown). Use those numbers with browser_click, browser_type and "
    "browser_act. Also reports where the page is scrolled, any other tab that "
    "is open, and any dialog that was answered on the way."
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


# What every acting tool takes: a number from the last read, or - for what a
# read did not list - a CSS selector or a piece of visible text.
_TARGET_FIELDS: dict[str, Any] = {
    "element": {
        "type": "integer",
        "description": "The number shown beside the element in the last page reading.",
    },
    "selector": {
        "type": "string",
        "description": (
            "A CSS selector, for something the reading did not list. Prefer the "
            "number when there is one: it came from the page as it actually is."
        ),
    },
    "text": {
        "type": "string",
        "description": "Visible text to find the element by, when there is no number for it.",
    },
}


def _target(arguments: dict[str, Any], *, required: bool = True) -> dict[str, Any] | None:
    """Which element the model named, in whichever of the ways it used."""

    picked: dict[str, Any] = {}
    if arguments.get("x") is not None and arguments.get("y") is not None:
        picked["x"] = _bounded_int(arguments, "x", 0, -20_000, 20_000)
        picked["y"] = _bounded_int(arguments, "y", 0, -20_000, 20_000)
        return picked
    if arguments.get("element") is not None:
        picked["element"] = _index(arguments)
    for key in ("selector", "text"):
        value = str(arguments.get(key) or "").strip()
        if value:
            picked[key] = value
    if picked:
        return picked
    if required:
        raise ToolArgumentError("Name what to act on: element, selector or text.")
    return None


async def _answer(
    session: Any, result: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """What the action reported, with a picture when one was asked for.

    The action already comes back with the page it left behind - and with why
    it did not go through, when it did not. Reading again here would drop that.
    """

    if arguments.get("screenshot"):
        result["screenshot_png"] = (await session.screenshot()).get("screenshot_png")
    return _payload(result)


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
        "Click an element. Name it by the number from the last page reading "
        "where there is one - the numbers come from the page as it actually is, "
        "and a selector written from memory can miss or hit the wrong thing "
        "without saying so. The click scrolls the element into view and waits "
        "for it to be clickable, so a button below the fold or behind a banner "
        "that is about to close does not need handling first. " + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            **_TARGET_FIELDS,
            "button": {
                "type": "string",
                "description": (
                    "Which button: left, right or middle. Right opens the context "
                    "menu. Defaults to left."
                ),
            },
            "double": {
                "type": "boolean",
                "description": "Double-click - what selects a word or opens an item.",
            },
            "modifiers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Keys held while clicking, for multi-select and the like: "
                    "Alt, Control, Meta, Shift."
                ),
            },
            "x": {
                "type": "integer",
                "description": (
                    "Click this point on the page instead of an element - for a "
                    "shape on a slide or anything else drawn on a canvas, which "
                    "a reading cannot list. Page pixels, the same ones each "
                    "element's x and y are given in, not screen pixels."
                ),
            },
            "y": {"type": "integer", "description": "The point's vertical position."},
            "screenshot": _SCREENSHOT_FIELD,
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        button = str(arguments.get("button") or "left").strip().lower()
        if button not in {"left", "right", "middle"}:
            raise ToolArgumentError(f"Unknown button {button!r}: left, right or middle.")
        result = await session.click(
            _target(arguments),
            button=button,
            count=2 if arguments.get("double") else 1,
            modifiers=[str(key) for key in arguments.get("modifiers") or []],
        )
        return await _answer(session, result, arguments)


class BrowserTypeTool:
    name = "browser_type"
    description = (
        "Type into a field, or into a rich editor - a document, a slide, a "
        "comment box - by naming the element the text should go into. Set "
        "replace to put the text in place of what is there; leave it off to add "
        "to it. Never type a password or any other credential with this: hand "
        "the login to the user instead (see browser_request_login), so the "
        "secret stays between them and the site. " + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            **_TARGET_FIELDS,
            "text": {"type": "string", "description": "What to type."},
            "replace": {
                "type": "boolean",
                "description": "Clear what is there first. Defaults to false, which appends.",
            },
            "submit": {
                "type": "boolean",
                "description": "Press Enter after typing. Defaults to false.",
            },
            "screenshot": _SCREENSHOT_FIELD,
        },
        required=("text",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        result = await session.type_text(
            _target(arguments),
            str(arguments.get("text") or ""),
            replace=bool(arguments.get("replace")),
            submit=bool(arguments.get("submit")),
        )
        return await _answer(session, result, arguments)


class BrowserActTool:
    """Everything else a pointer and a keyboard do, one action per call.

    One tool rather than nine: they share the way an element is named and they
    all answer with the page afterwards. The tool list is read every turn, and
    spelling out hover, drag and check separately costs more than it explains.
    """

    name = "browser_act"
    description = (
        "Act on the page in a way clicking and typing cannot. action picks what: "
        "'hover' - move the pointer onto something, for a menu that opens on "
        "hover; 'press' - send keys or a chord ('Enter', 'Control+b', 'Escape', "
        "'Control+Shift+ArrowRight'), which is how an editor is driven where no "
        "button exists - bold, undo, next placeholder; 'select' - choose values "
        "in a dropdown; 'check' / 'uncheck' - set a checkbox, radio or switch; "
        "'drag' - press the mouse on one thing and release it on another, or "
        "move it by an offset, for reordering and for moving a shape on a "
        "slide; 'scroll' - bring something into view, or move the page; "
        "'upload' - hand files to a file input; 'focus' - put the caret "
        "somewhere without clicking. " + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            # Named in the description rather than as an enum, like every other
            # schema here: they stay atomic for weak local models (see
            # test_tool_schemas), and _run refuses anything else with the list.
            "action": {
                "type": "string",
                "description": (
                    "What to do: hover, press, select, check, uncheck, drag, "
                    "scroll, upload or focus."
                ),
            },
            **_TARGET_FIELDS,
            "keys": {
                "type": "string",
                "description": (
                    "For 'press': the key or chord, or several separated by commas "
                    "to send in order ('Control+a, Delete')."
                ),
            },
            "values": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For 'select': the option values or labels to choose.",
            },
            "to_element": {
                "type": "integer",
                "description": "For 'drag': the number of the element to drop on.",
            },
            "to_selector": {
                "type": "string",
                "description": "For 'drag': a CSS selector for where to drop.",
            },
            "dx": {"type": "integer", "description": "For 'drag' and 'scroll': horizontal amount."},
            "dy": {"type": "integer", "description": "For 'drag' and 'scroll': vertical amount."},
            "to": {
                "type": "string",
                "description": "For 'scroll': 'top' or 'bottom', to jump to one end of the page.",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For 'upload': the files to hand over.",
            },
            "screenshot": _SCREENSHOT_FIELD,
        },
        required=("action",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        action = str(arguments.get("action") or "").strip()
        result = await self._run(session, action, arguments)
        return await _answer(session, result, arguments)

    async def _run(
        self, session: Any, action: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if action == "hover":
            return await session.hover(_target(arguments))
        if action == "press":
            keys = str(arguments.get("keys") or "").strip()
            if not keys:
                raise ToolArgumentError("press needs keys, like 'Enter' or 'Control+b'.")
            return await session.press(keys, _target(arguments, required=False))
        if action == "select":
            values = [str(value) for value in arguments.get("values") or []]
            if not values:
                raise ToolArgumentError("select needs values: what to choose in the dropdown.")
            return await session.select(_target(arguments), values)
        if action in {"check", "uncheck"}:
            return await session.set_checked(_target(arguments), action == "check")
        if action == "drag":
            return await self._drag(session, arguments)
        if action == "scroll":
            return await session.scroll(
                target=_target(arguments, required=False),
                dx=_bounded_int(arguments, "dx", 0, -20_000, 20_000),
                dy=_bounded_int(arguments, "dy", 0, -20_000, 20_000),
                to=str(arguments.get("to") or ""),
            )
        if action == "upload":
            paths = [str(path) for path in arguments.get("paths") or []]
            if not paths:
                raise ToolArgumentError("upload needs paths: the files to hand over.")
            return await session.upload(_target(arguments), paths)
        if action == "focus":
            return await session.focus(_target(arguments))
        raise ToolArgumentError(
            f"Unknown action {action!r}. One of: hover, press, select, check, "
            "uncheck, drag, scroll, upload, focus."
        )

    @staticmethod
    async def _drag(session: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        destination: dict[str, Any] = {}
        if arguments.get("to_element") is not None:
            destination["element"] = _bounded_int(arguments, "to_element", 0, 0, 10_000)
        selector = str(arguments.get("to_selector") or "").strip()
        if selector:
            destination["selector"] = selector
        offset = (
            _bounded_int(arguments, "dx", 0, -20_000, 20_000),
            _bounded_int(arguments, "dy", 0, -20_000, 20_000),
        )
        if not destination and offset == (0, 0):
            raise ToolArgumentError("drag needs somewhere to go: to_element, to_selector or dx/dy.")
        return await session.drag(
            _target(arguments),
            destination or None,
            offset=None if destination else offset,
        )


class BrowserWaitTool:
    name = "browser_wait"
    description = (
        "Wait for the page to get somewhere before reading it again: text to "
        "appear, an element to exist, the address to change, loading to finish. "
        "Use this rather than reading in a loop - a slow save, a dialog that "
        "animates open, a document that renders after its data arrives. "
        + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            "text": {"type": "string", "description": "Wait until this text is on the page."},
            "selector": {
                "type": "string",
                "description": "Wait until this CSS selector matches something.",
            },
            "url": {
                "type": "string",
                "description": "Wait until the address matches this, glob allowed.",
            },
            "state": {
                "type": "string",
                "description": (
                    "Wait for a loading state: load, domcontentloaded or "
                    "networkidle. The last suits a page that fetches its data."
                ),
            },
            "seconds": {
                "type": "number",
                "description": "Wait a fixed time. The last resort - prefer the others.",
            },
            "screenshot": _SCREENSHOT_FIELD,
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        try:
            seconds = float(arguments.get("seconds") or 0)
        except (TypeError, ValueError):
            raise ToolArgumentError("seconds must be a number.") from None
        return _payload(
            await session.wait_for(
                text=str(arguments.get("text") or ""),
                selector=str(arguments.get("selector") or ""),
                url=str(arguments.get("url") or ""),
                state=str(arguments.get("state") or ""),
                seconds=max(0.0, min(seconds, 60.0)),
                screenshot=bool(arguments.get("screenshot")),
            )
        )


class BrowserPageTool:
    name = "browser_page"
    description = (
        "Move around the browser itself rather than the page: 'back', 'forward' "
        "and 'reload' for history, and 'tabs', 'switch', 'new_tab', 'close_tab' "
        "for windows. A link that opens a tab is followed automatically, so this "
        "is for going back to one the agent left. " + _RESULT_NOTE
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            "action": {
                "type": "string",
                "description": (
                    "What to do: back, forward, reload, tabs, switch, new_tab "
                    "or close_tab."
                ),
            },
            "tab": {"type": "integer", "description": "Which tab, for switch and close_tab."},
            "screenshot": _SCREENSHOT_FIELD,
        },
        required=("action",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        action = str(arguments.get("action") or "").strip()
        tab = _bounded_int(arguments, "tab", 0, 0, 100)
        if action in {"back", "forward", "reload"}:
            return _payload(await session.navigate(action))
        mapped = {
            "tabs": "list",
            "switch": "switch",
            "new_tab": "new",
            "close_tab": "close",
        }.get(action)
        if mapped is None:
            raise ToolArgumentError(
                f"Unknown action {action!r}. One of: back, forward, reload, tabs, "
                "switch, new_tab, close_tab."
            )
        return _payload(await session.tabs(mapped, tab))


class BrowserScreenshotTool:
    name = "browser_screenshot"
    description = (
        "Take a picture, of the whole page or of one element. This is the visual "
        "check: whether a layout is right, whether a chart drew, whether a slide "
        "looks the way it should - things the text of a page cannot answer. "
        "full_page captures past the bottom of the window."
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            **_TARGET_FIELDS,
            "full_page": {
                "type": "boolean",
                "description": "Capture the whole document, not just what fits on screen.",
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        session = _session(context)
        return _payload(
            await session.screenshot(
                target=_target(arguments, required=False),
                full_page=bool(arguments.get("full_page")),
            )
        )


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


def _bounded_int(arguments: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    raw = arguments.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ToolArgumentError(f"{name} must be a whole number.") from None
    return max(low, min(high, value))


class BrowserInspectTool:
    """The browser's developer tools, one panel per call.

    One tool with an ``aspect`` rather than eight tools: they share the page,
    the selector and the bounds, and a tool list the model has to read every
    turn is not the place to spell out each panel separately.
    """

    name = "browser_inspect"
    description = (
        "Look at the current page the way the browser's developer tools do, one "
        "panel per call. Read-only: it changes nothing on the page. aspect picks "
        "the panel: 'dom' - the element tree (Elements panel), one element per "
        "line with its id, classes and key attributes; 'html' - the live HTML of "
        "the page or of one element, as it is now after scripts ran; 'styles' - "
        "the computed styles, box and matching CSS rules of one element (needs "
        "selector); 'console' - what the page logged, uncaught errors included; "
        "'network' - the requests it made, with method, status, type and time; "
        "'storage' - cookies (names and flags, never values), localStorage, "
        "sessionStorage, IndexedDB, service workers and caches; 'accessibility' "
        "- roles and names as a screen reader gets them; 'performance' - load "
        "timing, paint, the slowest resources and memory. For the readable text "
        "and the clickable elements, use browser_read."
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {
            # Named in the description rather than as an enum: the schemas stay
            # atomic for weak local models (see test_tool_schemas), and execute
            # refuses anything else with the list.
            "aspect": {
                "type": "string",
                "description": "Which panel to read: " + ", ".join(INSPECT_ASPECTS) + ".",
            },
            "selector": {
                "type": "string",
                "description": (
                    "CSS selector for one element. Narrows dom, html and "
                    "accessibility to it (default: the whole page); required "
                    "for styles. The first match is used."
                ),
            },
            "depth": {
                "type": "integer",
                "description": (
                    f"dom only: levels below the root to show "
                    f"(default {DEFAULT_DOM_DEPTH}, max {MAX_DOM_DEPTH})."
                ),
            },
            "offset": {
                "type": "integer",
                "description": "html only: where to continue a long page, from its 'next' hint.",
            },
            "include_scripts": {
                "type": "boolean",
                "description": (
                    "html only: keep inline <script> and <style> bodies. Left out "
                    "by default: they are usually minified bundles."
                ),
            },
            "css_properties": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "styles only: the computed properties to report, instead of "
                    "the default layout/box/type set."
                ),
            },
            "level": {
                "type": "string",
                "description": (
                    "console only: the least severe level to show, one of "
                    + ", ".join(LOG_LEVELS)
                    + " (default log)."
                ),
            },
            "filter": {
                "type": "string",
                "description": "network only: keep requests whose URL contains this text.",
            },
            "failed_only": {
                "type": "boolean",
                "description": "network only: keep failed requests and 4xx/5xx responses.",
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"console/network: how many of the newest entries "
                    f"(default {DEFAULT_LOG_LIMIT}, max {MAX_LOG_ENTRIES})."
                ),
            },
            "clear": {
                "type": "boolean",
                "description": (
                    "console/network: empty the log after reading, so the next "
                    "read shows only what happened since."
                ),
            },
        },
        required=("aspect",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        aspect = str(arguments.get("aspect") or "").strip().lower()
        if aspect not in INSPECT_ASPECTS:
            raise ToolArgumentError(f"aspect must be one of: {', '.join(INSPECT_ASPECTS)}.")
        session = _session(context)
        if aspect in {"console", "network"}:
            level = str(arguments.get("level") or "log").strip().lower()
            if level not in LOG_LEVELS:
                raise ToolArgumentError(f"level must be one of: {', '.join(LOG_LEVELS)}.")
            return await session.inspect(
                aspect,
                level=level,
                contains=str(arguments.get("filter") or "").strip(),
                failed_only=bool(arguments.get("failed_only")),
                limit=_bounded_int(arguments, "limit", DEFAULT_LOG_LIMIT, 1, MAX_LOG_ENTRIES),
                clear=bool(arguments.get("clear")),
            )
        selector = str(arguments.get("selector") or "").strip()
        if aspect == "styles" and not selector:
            raise ToolArgumentError("styles needs a selector: the element whose styles to show.")
        properties = arguments.get("css_properties") or []
        if not isinstance(properties, list):
            raise ToolArgumentError("css_properties must be a list of CSS property names.")
        return await session.inspect(
            aspect,
            selector=selector,
            depth=_bounded_int(arguments, "depth", DEFAULT_DOM_DEPTH, 0, MAX_DOM_DEPTH),
            css_properties=tuple(str(name).strip() for name in properties if str(name).strip()),
            include_scripts=bool(arguments.get("include_scripts")),
            offset=_bounded_int(arguments, "offset", 0, 0, 1 << 31),
        )


class BrowserEvaluateTool:
    """The developer-tools console: run JavaScript in the page, see what it gives back."""

    name = "browser_evaluate"
    description = (
        "Run JavaScript in the current page, as if typed into the developer "
        "tools console, and get back its value and anything it logged. Pass an "
        "expression (document.title, document.querySelectorAll('a').length) or a "
        "function for several statements (() => { ...; return result; }); an "
        "async function is awaited. The value comes back as JSON, so return "
        "data, not DOM nodes. It runs with the page's full power and can change "
        "the page: prefer browser_inspect to look and browser_click / "
        "browser_type to act. Never use it to read or copy cookies, tokens or "
        "passwords."
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = tool_schema(
        {"expression": {"type": "string", "description": "The JavaScript to run."}},
        required=("expression",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        expression = str(arguments.get("expression") or "").strip()
        if not expression:
            raise ToolArgumentError("expression is required.")
        return await _session(context).evaluate(expression)
