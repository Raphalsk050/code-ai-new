"""Tool groups that stay out of the request until a task asks for them.

Every tool the registry holds used to travel on every model request: 57
schemas, three times the size of the system prompt, most of them for driving
a browser or the desktop. That is the bulk of the fixed prefix and, for a
smaller model, a field of near-identical names to choose wrongly from. The
groups below are offered as one line each instead, and their tools join the
request once the model calls ``load_tools`` for the group (or simply calls one
of them, which loads the group on the spot).

Membership is by name, not capability, because the groups are about what a
task is *doing* - a browser task, a desktop task - and a capability such as
``WEB`` also covers ``web_search``, which every task should see.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolGroup:
    name: str
    # One line for the prompt: what the group is for, in the model's terms.
    summary: str
    tools: frozenset[str]


DEFERRED_TOOL_GROUPS: tuple[ToolGroup, ...] = (
    ToolGroup(
        name="browser",
        summary=(
            "drive a web page: open, read, click, type, wait, inspect, "
            "screenshot, run page scripts, ask the user to log in"
        ),
        tools=frozenset(
            {
                "browser_open",
                "browser_read",
                "browser_click",
                "browser_type",
                "browser_act",
                "browser_wait",
                "browser_page",
                "browser_screenshot",
                "browser_inspect",
                "browser_evaluate",
                "browser_request_login",
            }
        ),
    ),
    ToolGroup(
        name="desktop",
        summary=(
            "control the machine's screen, mouse and keyboard, and manage the running applications"
        ),
        tools=frozenset(
            {
                "screen_info",
                "capture_screen",
                "move_mouse",
                "click_mouse",
                "drag_mouse",
                "scroll_mouse",
                "type_text",
                "press_keys",
                "open_application",
                "activate_application",
                "list_applications",
            }
        ),
    ),
    ToolGroup(
        name="terminal",
        summary=(
            "an interactive terminal session, for REPLs, servers and programs "
            "that keep running and take input"
        ),
        tools=frozenset(
            {
                "start_terminal",
                "send_terminal_text",
                "terminal_enter",
                "interrupt_terminal",
                "terminate_terminal",
                "read_screen",
            }
        ),
    ),
    ToolGroup(
        name="android",
        summary="analyze an APK or a logcat capture",
        tools=frozenset({"analyze_apk", "analyze_logcat"}),
    ),
    ToolGroup(
        name="pdf",
        summary=(
            "read, edit and create PDFs: merge, split, watermark, forms, redact, encrypt; "
            "Markdown, HTML, images or office files to PDF; PDF to images or text"
        ),
        tools=frozenset({"pdf_inspect", "pdf_edit", "pdf_convert"}),
    ),
    ToolGroup(
        name="documents",
        summary=(
            "create, inspect, edit, format and convert Word documents (.docx): Markdown to "
            "styled docx, ABNT and other presets, find/replace, sections, tables, tracked changes"
        ),
        tools=frozenset(
            {
                "document_create",
                "document_inspect",
                "document_edit",
                "document_format",
                "document_convert",
            }
        ),
    ),
    ToolGroup(
        name="slides",
        summary=(
            "create, inspect, edit, format and render PowerPoint decks (.pptx): designed "
            "layouts and themes, native charts, text fitting, visual previews"
        ),
        tools=frozenset(
            {"slides_create", "slides_inspect", "slides_edit", "slides_format", "slides_render"}
        ),
    ),
    ToolGroup(
        name="design",
        summary=(
            "UX/UI design: design tokens and accessible colour palettes, contrast checks, "
            "screenshots of pages at several viewports, accessibility and UX audits"
        ),
        tools=frozenset({"design_system", "ui_preview", "ui_audit"}),
    ),
    ToolGroup(
        name="latex",
        summary=(
            "write and compile LaTeX articles: IEEE, ACM, Springer, Elsevier, ABNT templates, "
            "bibliography, error diagnosis, TeX Live install"
        ),
        tools=frozenset({"latex_article", "latex_compile", "latex_setup"}),
    ),
)

_BY_NAME = {group.name: group for group in DEFERRED_TOOL_GROUPS}
_BY_TOOL = {tool: group for group in DEFERRED_TOOL_GROUPS for tool in group.tools}


def group_names(enabled: Callable[[str], bool] | None = None) -> tuple[str, ...]:
    return tuple(group.name for group in available_groups(enabled))


def available_groups(enabled: Callable[[str], bool] | None = None) -> tuple[ToolGroup, ...]:
    """The groups that still hold a tool ``enabled`` accepts; all of them without it."""

    if enabled is None:
        return DEFERRED_TOOL_GROUPS
    return tuple(
        group for group in DEFERRED_TOOL_GROUPS if any(enabled(tool) for tool in group.tools)
    )


def group_named(name: str) -> ToolGroup | None:
    return _BY_NAME.get(name.strip().lower())


def group_of(tool_name: str) -> ToolGroup | None:
    """The deferred group a tool belongs to, or ``None`` for an always-on tool."""

    return _BY_TOOL.get(tool_name)


def render_catalog(enabled: Callable[[str], bool] | None = None) -> str:
    """The groups as the prompt lists them: name, then what it is for."""

    return "\n".join(f"- {group.name}: {group.summary}" for group in available_groups(enabled))
