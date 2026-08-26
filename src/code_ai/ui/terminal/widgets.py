from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

from markdown_it import MarkdownIt
from markdown_it.token import Token
from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.content import Content

try:
    from art import text2art
except ImportError:  # pragma: no cover - dependency fallback for incomplete installs.
    text2art = None

BANNER_RESOURCE = "banner.txt"
CODE_AI_LOGO_FONT = "tarty2"
CODE_AI_BANNER_FONT_OPTIONS = (
    "tarty1",
    "tarty2",
    "tarty3",
    "tarty4",
    "tarty5",
    "tarty6",
    "tarty7",
    "tarty8",
    "tarty9",
    "future_1",
    "future_2",
    "future_3",
    "future_4",
    "future_5",
    "future_6",
    "future_7",
    "future_8",
    "block",
    "block2",
    "big",
    "small",
    "smallcaps",
    "standard",
    "slant",
    "doom",
    "epic",
    "mini",
    "cybermedium",
    "cyberlarge",
    "cybersmall",
    "digital",
    "thin",
    "thin2",
    "thin3",
    "lineblocks",
    "monospace",
    "xsansi",
)
CODE_AI_LOGO_STYLES = (
    "bold rgb(255,80,100)",
    "bold rgb(255,230,90)",
)

# --- Working indicator ("the agent is busy" animation) ----------------------
# AgentState values that mean the agent is actively doing something; the
# indicator animates for these and stays static otherwise.
WORKING_STATES = frozenset(
    {"CALLING_MODEL", "EXECUTING_TOOL", "COMPRESSING_CONTEXT", "CANCELLING"}
)
_WORKING_LABELS = {
    "CALLING_MODEL": "calling model",
    "EXECUTING_TOOL": "running tools",
    "COMPRESSING_CONTEXT": "compacting context",
    "CANCELLING": "cancelling",
}

# Animated glyph color base, the dim label next to it, and the static idle tint.
WORKING_BASE_COLOR = "#ff9f1c"
WORKING_LABEL_STYLE = "#6b7280"
WORKING_IDLE_STYLE = "#3b4654"
# Seconds for one full color-pulse cycle, and the red->orange->yellow ramp it
# walks through (matches the CODE.AI banner palette).
WORKING_PULSE_PERIOD = 2.2
WORKING_PULSE_STOPS = ((255, 80, 100), (255, 138, 60), (255, 210, 80), (255, 138, 60))


@dataclass(frozen=True, slots=True)
class SpinnerStyle:
    """One selectable working-indicator animation.

    ``frames`` are cycled every ``interval`` seconds. ``pulse`` makes the glyph
    walk the color ramp continuously (independent of the frame rate), which is
    what gives single-frame styles like the plain asterisk their life.
    """

    key: str
    label: str
    frames: tuple[str, ...]
    interval: float
    pulse: bool = False


# Order here drives the command palette and `/config spinner` listing. The
# first entry is the default; the entries that read most like Claude Code's
# indicator are grouped right after it.
_SPINNER_LIST = (
    SpinnerStyle("ascii", "ASCII line (retro)", ("|", "/", "—", "\\"), 0.11),
    SpinnerStyle(
        "star-spin", "star spinning", ("✶", "✷", "✸", "✹", "✺", "✹", "✸", "✷"), 0.11, True
    ),
    SpinnerStyle("asterisk-pulse", "asterisk pulse", ("✳",), 0.6, True),
    SpinnerStyle("asterisk-color", "asterisk color only", ("✻",), 0.6, True),
    SpinnerStyle(
        "braille-full",
        "braille full (smooth)",
        ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"),
        0.07,
        True,
    ),
    SpinnerStyle("sparkle", "sparkle", ("·", "✢", "✦", "✶", "✦", "✢"), 0.13),
    SpinnerStyle("asterisk-spin", "asterisk spinning", ("✲", "✳", "✴", "✳"), 0.12, True),
    SpinnerStyle(
        "starburst", "starburst", ("·", "∗", "✳", "✺", "✹", "✺", "✳", "∗"), 0.11, True
    ),
    SpinnerStyle(
        "braille-classic",
        "braille classic",
        ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
        0.08,
    ),
    SpinnerStyle(
        "braille-wave",
        "braille wave (fill)",
        ("⡀", "⣀", "⣄", "⣆", "⣇", "⣧", "⣷", "⣿", "⣷", "⣧", "⣇", "⣆", "⣄", "⣀"),
        0.06,
    ),
    SpinnerStyle("orbit", "orbit dot", ("⠁", "⠂", "⠄", "⡀", "⢀", "⠠", "⠐", "⠈"), 0.095),
    SpinnerStyle("braille-orbit", "braille orbit", ("⠁", "⠈", "⠐", "⠠", "⢀", "⡀", "⠄", "⠂"), 0.075),
    SpinnerStyle(
        "equalizer",
        "equalizer / breathing",
        ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂"),
        0.085,
    ),
    SpinnerStyle("shade", "shaded block", ("░", "▒", "▓", "█", "▓", "▒"), 0.095),
    SpinnerStyle("dotted-circle", "dotted circle", ("◌", "◍", "◎", "●", "◉", "●", "◎", "◍"), 0.13),
    SpinnerStyle("corners", "corners", ("▖", "▘", "▝", "▗"), 0.12),
    SpinnerStyle("quad-spin", "quadrants spinning", ("▘", "▝", "▗", "▖"), 0.11),
    SpinnerStyle("arc", "arc", ("◜", "◠", "◝", "◞", "◡", "◟"), 0.11),
    SpinnerStyle("moon", "moon", ("◐", "◓", "◑", "◒"), 0.15),
    SpinnerStyle("clock", "clock", ("◴", "◷", "◶", "◵"), 0.14),
    SpinnerStyle("triangle", "triangle", ("◢", "◣", "◤", "◥"), 0.13),
    SpinnerStyle("flower", "flower", ("❉", "❊", "❋", "❊"), 0.16, True),
)
WORKING_SPINNERS = {style.key: style for style in _SPINNER_LIST}
CODE_AI_SPINNER_OPTIONS = tuple(style.key for style in _SPINNER_LIST)
DEFAULT_SPINNER = "ascii"


def working_label(status: str) -> str:
    return _WORKING_LABELS.get(status, "working")


def normalize_spinner(spinner: str) -> str:
    key = spinner.strip()
    if key in WORKING_SPINNERS:
        return key
    return DEFAULT_SPINNER


def resolve_spinner(spinner: str) -> SpinnerStyle:
    return WORKING_SPINNERS[normalize_spinner(spinner)]


def spinner_color(progress: float) -> str:
    """Hex color for a point along the pulse ramp; ``progress`` wraps at 1.0."""
    stops = WORKING_PULSE_STOPS
    count = len(stops)
    position = (progress % 1.0) * count
    index = int(position)
    fraction = position - index
    start = stops[index % count]
    end = stops[(index + 1) % count]
    red = round(start[0] + (end[0] - start[0]) * fraction)
    green = round(start[1] + (end[1] - start[1]) * fraction)
    blue = round(start[2] + (end[2] - start[2]) * fraction)
    return f"#{red:02x}{green:02x}{blue:02x}"


# --- Plan panel (planned-steps checklist beside the conversation) -----------
# Per-step marker glyphs and colors. The running step's glyph is supplied by
# the live spinner instead of a fixed marker.
_PLAN_MARKERS = {"done": "✓", "pending": "○", "failed": "✗", "paused": "◌"}
_PLAN_TITLE_STYLES = {
    "done": "#7b8493",
    "running": "bold #d7dee8",
    "pending": "#9aa4b2",
    "failed": "#e0a0a0",
    "paused": "#9aa4b2",
}
_PLAN_MARKER_STYLES = {
    "done": "#48d17a",
    "pending": "#56606e",
    "failed": "#e05252",
    "paused": "#8892a0",
}
_PLAN_SUB_STYLE = "#56606e"
_PLAN_HEADER_STYLE = "#8892a0"
_PLAN_TITLE_WIDTH = 32


def plan_is_active(payload: dict[object, object]) -> bool:
    """True when the plan snapshot describes a plan still being worked on."""
    return str(payload.get("status") or "") == "ACTIVE"


def build_plan_steps(payload: dict[object, object]) -> list[dict[str, str]]:
    """Reconstruct the ordered step list from a plan snapshot payload.

    ``completed_steps`` and ``remaining_steps`` are both emitted in plan order,
    and steps run sequentially, so the completed ones are always the prefix.
    The current step is flagged running (failed, or paused when the plan is
    WAITING on the user - nothing is executing, so it must not spin) and the
    rest pending.
    """
    completed = [str(title) for title in (payload.get("completed_steps") or [])]
    remaining = [str(title) for title in (payload.get("remaining_steps") or [])]
    current = payload.get("current_step")
    current_label = None if current is None else str(current)
    current_status = str(payload.get("current_step_status") or "")
    plan_waiting = str(payload.get("status") or "") == "WAITING"

    steps: list[dict[str, str]] = [{"title": title, "status": "done"} for title in completed]
    for title in remaining:
        if current_label is not None and title == current_label:
            if current_status == "FAILED":
                status = "failed"
            elif plan_waiting:
                status = "paused"
            else:
                status = "running"
        else:
            status = "pending"
        steps.append({"title": title, "status": status})
    return steps


def _truncate_title(title: str, width: int = _PLAN_TITLE_WIDTH) -> str:
    return title if len(title) <= width else title[: width - 1] + "…"


def render_plan(
    steps: list[dict[str, str]],
    progress: str,
    plan_status: str,
    running_glyph: str,
    running_color: str,
) -> Text:
    """Render the plan checklist as a Rich Text for the plan panel Static."""
    text = Text()
    header = f"{progress}"
    if plan_status:
        header += f" · {plan_status.lower()}"
    text.append(header + "\n", style=_PLAN_HEADER_STYLE)

    for index, step in enumerate(steps):
        status = step.get("status", "pending")
        title = _truncate_title(step.get("title", ""))
        if status == "running":
            marker, marker_style = running_glyph, f"bold {running_color}"
        else:
            marker = _PLAN_MARKERS.get(status, "○")
            marker_style = _PLAN_MARKER_STYLES.get(status, "#56606e")
        text.append(marker + " ", style=marker_style)
        text.append(title, style=_PLAN_TITLE_STYLES.get(status, "#9aa4b2"))
        if status == "running":
            text.append("\n  executando", style=_PLAN_SUB_STYLE)
        elif status == "paused":
            text.append("\n  aguardando você", style=_PLAN_SUB_STYLE)
        if index < len(steps) - 1:
            text.append("\n")
    return text


# --- Sub-agents panel (live delegated-agent activity, below the plan) --------
# Terminal-state glyphs; a running agent borrows the live spinner instead.
_AGENT_MARKERS = {"completed": "✓", "failed": "✗", "rejected": "⊘"}
_AGENT_MARKER_STYLES = {
    "completed": "#48d17a",
    "failed": "#e05252",
    "rejected": "#e0a0a0",
    "running": "#ff9f1c",
}
_AGENT_TYPE_STYLES = {
    "running": "bold #d7dee8",
    "completed": "#7b8493",
    "failed": "#e0a0a0",
    "rejected": "#9aa4b2",
}
_AGENT_DETAIL_STYLE = "#56606e"
_AGENT_TASK_WIDTH = 30


def render_subagents_summary(agents: list[dict[str, str]]) -> Text:
    """The AGENTS panel's one-line roster summary ("3 agent(s) · 1 running")."""
    running = sum(1 for agent in agents if agent.get("status") == "running")
    header = f"{len(agents)} agent(s)"
    if running:
        header += f" · {running} running"
    return Text(header, style=_PLAN_HEADER_STYLE)


def render_subagent_header(
    agent: dict[str, str],
    running_glyph: str,
    running_color: str,
) -> Text:
    """One sub-agent card's title row: status marker, name, dim type suffix.

    The marker is the live spinner while the agent runs and a terminal glyph
    (✓ / ✗ / ⊘) once it settles.
    """
    text = Text()
    status = agent.get("status", "running")
    agent_type = agent.get("agent_type", "agent")
    label = agent.get("name") or agent_type
    if status == "running":
        marker, marker_style = running_glyph, f"bold {running_color}"
    else:
        marker = _AGENT_MARKERS.get(status, "•")
        marker_style = _AGENT_MARKER_STYLES.get(status, "#56606e")
    text.append(marker + " ", style=marker_style)
    text.append(label, style=_AGENT_TYPE_STYLES.get(status, "#9aa4b2"))
    if agent.get("name"):
        # The type as a dim suffix, so the name reads as the agent's identity.
        text.append(f"  {agent_type}", style=_AGENT_DETAIL_STYLE)
    return text


def subagent_task_preview(task: str) -> str:
    """Collapsed-title preview of a delegated task: one flattened, capped line.

    Multi-line tasks are squashed to single spaces so the Collapsible title
    never wraps; the full text lives in the expanded body.
    """
    flattened = " ".join(task.split())
    return _truncate_title(flattened or "task", _AGENT_TASK_WIDTH)


def render_subagent_task(agent: dict[str, str]) -> Text:
    """Expanded card body: the full delegated task plus the latest activity."""
    text = Text()
    task = agent.get("task", "").strip()
    detail = agent.get("detail", "").strip()
    text.append(task or "(no task)", style=_AGENT_DETAIL_STYLE)
    if detail:
        text.append("\n· " + detail, style=_AGENT_DETAIL_STYLE)
    return text


# --- Terminal panel (live interactive PTY screen, below the agents panel) ----
_TERMINAL_HEADER_STYLE = "#8892a0"
_TERMINAL_SCREEN_STYLE = "#d7dee8"
_TERMINAL_CLOSED_STYLE = "#e0a0a0"
# How many rows of the emulated screen the panel shows; the newest survive.
_TERMINAL_PANEL_MAX_ROWS = 18


def render_terminal_screen(
    session_id: str,
    screen: str,
    rows: int,
    cols: int,
    closed: bool,
) -> Text:
    """Render the interactive terminal's emulated screen for the TERMINAL panel.

    A short header (session id, dimensions, state) over the newest rows of the
    ``pyte`` display. Only the tail is shown — the panel is a live viewport,
    not a scrollback; the model (and the user via /term) can always read the
    full screen through the read_screen tool.
    """
    text = Text()
    header = f"{session_id[:8] or '-'} · {cols}x{rows}"
    text.append(header, style=_TERMINAL_HEADER_STYLE)
    if closed:
        text.append("  encerrado", style=_TERMINAL_CLOSED_STYLE)
    text.append("\n")
    lines = screen.splitlines()
    if len(lines) > _TERMINAL_PANEL_MAX_ROWS:
        lines = lines[-_TERMINAL_PANEL_MAX_ROWS:]
    text.append("\n".join(line.rstrip() for line in lines), style=_TERMINAL_SCREEN_STYLE)
    return text


FALLBACK_CODE_AI_LOGO_TEXT = "code.ai"
FALLBACK_CODE_AI_LOGO_ART = "       \n█▀▀ █▀█ █▀▄ █▀▀ ░ ▄▀█ █ \n█▄▄ █▄█ █▄▀ ██▄ ▄ █▀█ █ \n       "


def normalize_banner_font(font: str) -> str:
    value = font.strip()
    if value in CODE_AI_BANNER_FONT_OPTIONS:
        return value
    return CODE_AI_LOGO_FONT


def load_banner_source() -> str:
    try:
        logo = resources.files(__package__).joinpath(BANNER_RESOURCE).read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return FALLBACK_CODE_AI_LOGO_TEXT
    if not logo.strip():
        return FALLBACK_CODE_AI_LOGO_TEXT
    return logo.strip()


def render_banner_art(source: str, *, font: str = CODE_AI_LOGO_FONT) -> str:
    font = normalize_banner_font(font)
    if text2art is None:
        return FALLBACK_CODE_AI_LOGO_ART
    try:
        return text2art(source, font=font)
    except Exception:
        return FALLBACK_CODE_AI_LOGO_ART


def style_banner_art(
    source: str,
    styles: tuple[str, ...] = CODE_AI_LOGO_STYLES,
) -> Text:
    lines = source.splitlines()
    styled = Text()
    visible_line_count = sum(1 for line in lines if line.strip())
    visible_line_index = 0

    for index, line in enumerate(lines):
        if line.strip() and styles:
            style_index = visible_line_count - visible_line_index - 1
            styled.append(line, style=styles[style_index % len(styles)])
            visible_line_index += 1
        else:
            styled.append(line)
        if index < len(lines) - 1:
            styled.append("\n")
    return styled


def load_code_ai_logo(font: str = CODE_AI_LOGO_FONT) -> Text:
    return style_banner_art(render_banner_art(load_banner_source(), font=font))


# --- Context usage meter (top-of-screen fill bar) ---------------------------
# Bar width in cells and the fill/marker glyphs.
_CONTEXT_METER_WIDTH = 28
_CONTEXT_FILLED = "█"
_CONTEXT_EMPTY = "░"
_CONTEXT_THRESHOLD_MARK = "┊"
# Color ramp by fill fraction: calm below 60%, warm as it approaches the
# auto-compaction threshold, hot once the threshold is reached.
_CONTEXT_CALM = "#48d17a"
_CONTEXT_WARN = "#ff9f1c"
_CONTEXT_HOT = "#e05252"
_CONTEXT_LABEL_STYLE = "#8892a0"
_CONTEXT_DETAIL_STYLE = "#9aa4b2"
_CONTEXT_TRACK_STYLE = "#2b3440"


def _humanize_tokens(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return str(value)


def _context_fill_color(fraction: float, threshold: float) -> str:
    if fraction >= threshold:
        return _CONTEXT_HOT
    if fraction >= 0.6:
        return _CONTEXT_WARN
    return _CONTEXT_CALM


def render_context_meter(
    used: int | None,
    budget: int | None,
    threshold: float,
    width: int = _CONTEXT_METER_WIDTH,
) -> Text:
    """Render the top-of-screen context-usage bar as Rich Text.

    The bar fills with use, recolors as it nears ``threshold`` (where the
    orchestrator auto-compacts), and marks the threshold column so the user can
    see how much headroom is left before compaction kicks in.
    """
    text = Text()
    text.append("context ", style=_CONTEXT_LABEL_STYLE)
    if not used or not budget or budget <= 0:
        text.append("tokens unavailable", style=_CONTEXT_DETAIL_STYLE)
        return text

    fraction = used / budget
    clamped = min(1.0, max(0.0, fraction))
    filled = round(clamped * width)
    color = _context_fill_color(fraction, threshold)
    threshold_index = int(threshold * width)

    text.append("[", style=_CONTEXT_TRACK_STYLE)
    for index in range(width):
        if index < filled:
            text.append(_CONTEXT_FILLED, style=color)
        elif index == threshold_index:
            text.append(_CONTEXT_THRESHOLD_MARK, style=_CONTEXT_WARN)
        else:
            text.append(_CONTEXT_EMPTY, style=_CONTEXT_TRACK_STYLE)
    text.append("] ", style=_CONTEXT_TRACK_STYLE)

    text.append(f"{round(fraction * 100)}%", style=color)
    text.append(
        f"  {_humanize_tokens(used)}/{_humanize_tokens(budget)}",
        style=_CONTEXT_DETAIL_STYLE,
    )
    return text


# Prefix the orchestrator uses for the assistant's prose answer. Streaming
# deltas concatenate onto a single ``ai> `` buffer entry, so a finished answer is
# one transcript line whose body (after the prefix) is the full Markdown source.
ASSISTANT_LINE_PREFIX = "ai> "
# Fallback render width when the conversation pane size is not known yet.
_DEFAULT_MARKDOWN_WIDTH = 80


def _parse_markdown_keeping_markup_visible(body: str) -> list[Token]:
    """Parse Markdown so raw XML/HTML in the answer stays visible.

    Rich's ``Markdown`` has no renderer for ``html_block``/``html_inline``
    tokens: it silently drops them. An answer quoting an XML/HTML document
    outside a code fence (which models do all the time when asked to produce
    XML) then simply vanishes from the terminal — the user sees the prose
    around a hole where the document should be. Rewrite block HTML into a
    fenced code block (preserving its line structure, with highlighting) and
    inline HTML into literal text, so angle-bracket content always renders.
    """
    parser = MarkdownIt().enable("strikethrough").enable("table")
    tokens = parser.parse(body)
    rewritten: list[Token] = []
    for token in tokens:
        if token.type == "html_block":
            # markdown-it splits one document into several html_block tokens
            # (the <?xml?> prolog and the root element, for instance); merge
            # runs of them so the document renders as one contiguous block.
            previous = rewritten[-1] if rewritten else None
            if previous is not None and previous.type == "fence" and previous.meta.get(
                "from_html_block"
            ):
                previous.content += token.content
                continue
            fence = Token("fence", "code", 0)
            fence.content = token.content
            fence.info = "xml"
            fence.markup = "```"
            fence.block = True
            fence.meta = {"from_html_block": True}
            rewritten.append(fence)
            continue
        if token.type == "inline" and token.children:
            for child in token.children:
                if child.type == "html_inline":
                    child.type = "text"
        rewritten.append(token)
    return rewritten


def markdown_to_content(body: str, width: int) -> Content:
    """Render Markdown to a Textual-native :class:`Content`.

    The terminal chat mounts every transcript line as a selectable ``Static``,
    which only works because plain lines become native ``Content`` visuals —
    Textual's mouse machinery (hover, scroll, drag-to-select, copy) operates on
    ``Content``/``Text`` visuals, not on wrapped Rich renderables (a
    ``RichVisual`` returns ``None`` from ``get_selection`` and cannot be copied).

    So instead of handing Textual a Rich ``Markdown`` object directly, render it
    to styled segments here and rebuild them as a ``Content``: the answer still
    shows headings, lists and highlighted code, but it stays selectable and
    copyable like the rest of the conversation.
    """
    width = max(20, width)
    console = Console(width=width)
    markdown = Markdown(body)
    # Re-parse with the HTML-preserving token rewrite; ``parsed`` is what
    # ``Markdown.__rich_console__`` walks, so this is the one override point.
    markdown.parsed = _parse_markdown_keeping_markup_visible(body)
    segments = console.render(markdown, console.options.update_width(width))
    text = Text()
    for segment in segments:
        if segment.control:
            continue
        text.append(segment.text, segment.style)
    text.rstrip()
    return Content.from_rich_text(text)


# Speaker chips: a colored "tag" (dark bold text on a colored background) marking
# the only two things that are real *messages* — the user's prompt and the
# agent's answer. Green is the statusline color, orange the permission button's
# border, so the chat reads as part of the same palette. Everything else (the
# model's thinking, tool calls, plans, evidence) is the agent's *work trace*:
# subordinate, so it is dimmed and indented via "turn-trace" rather than chipped.
_CHIP_TEXT_COLOR = "#071018"  # the screen background, for contrast on a bright chip
_USER_COLOR = "#48d17a"  # statusline green
_MODEL_COLOR = "#ff9f1c"  # permission-button / logo orange
# Tool calls are actions, not messages: a cool cyan chip carries the tool name so
# the eye can pick out *which* tool ran, distinct from the green/orange speakers.
_TOOL_COLOR = "#4fc3dc"
# Surrounding (non-chip) words on a tool line stay in the dim trace gray so the
# chip is the only thing that pops, matching the .turn-trace CSS color.
_TRACE_TEXT_STYLE = "#6b7280"

# Line prefixes that make up the agent's working trace. warning>/error> are left
# out on purpose so problems keep full prominence instead of fading into it.
_TRACE_PREFIXES = (
    "model>",
    "thinking>",
    "working>",
    "tool>",
    "subagent>",
    "evidence>",
    "plan>",
    "approval>",
    "policy>",
    "completion>",
    "permission>",
    "term>",
    # A message typed mid-turn, waiting for the model to read it.
    "queued>",
    # Background learning yielding the model to the turn (learning.cancelled).
    "memory>",
    # The live execute_command output line (see view_models._apply_command_output).
    "cmd~",
)
# Multi-line trace text (the model's reasoning) carries its own blank lines; they
# are collapsed so the dim trace stays compact instead of sprawling.
_BLANK_RUN = re.compile(r"\n[ \t]*\n+")

# "model> requested <name> tool" — the name is chipped as the tool that ran.
_TOOL_REQUEST = re.compile(r"^model> requested (?P<name>\S+) tool$")
_TOOL_LINE_PREFIX = "tool> "


def _chip(label: str, color: str) -> Content:
    """A colored speaker chip (dark bold text on a colored background)."""
    return Content.styled(f" {label} ", f"bold {_CHIP_TEXT_COLOR} on {color}")


def thinking_body(line: str) -> str | None:
    """Reasoning text for a ``thinking>`` line (blank lines collapsed), else None.

    Used to fold the model's reasoning into a collapsible block at commit time,
    mirroring the VS Code extension's hideable "Thinking" section. The live
    streaming tail still shows it inline via :func:`render_conversation_line`.
    """
    prefix = "thinking> "
    if not line.startswith(prefix):
        return None
    return _BLANK_RUN.sub("\n", line[len(prefix) :])


def conversation_line_class(line: str) -> str:
    """CSS class for a committed transcript line, driving the spacing hierarchy.

    Only two things are real messages — the user prompt and the agent's answer —
    and they get the biggest spacing. Everything else is the agent's work trace
    ("turn-trace"): dimmed, indented and packed tight so it reads as subordinate.
    """
    if line.startswith("you> "):
        return "turn-user"
    if line.startswith(ASSISTANT_LINE_PREFIX):
        return "turn-answer"
    if line.startswith(_TRACE_PREFIXES):
        return "turn-trace"
    return ""


def render_conversation_line(
    line: str, *, rich_markdown: bool = False, width: int | None = None
) -> RenderableType:
    """Render one committed transcript line.

    The user prompt and the agent's answer read as chipped messages: a green
    ``you`` chip inline, an orange ``model`` chip above the formatted answer.
    With ``rich_markdown`` the answer renders as Markdown (headings, lists,
    fenced code) to match the VS Code extension; the formatting is left off while
    the line is still streaming, since partial Markdown renders badly.

    Every other line — the model's thinking, tool calls, plans — is returned as
    plain text and dimmed/indented by its ``turn-trace`` CSS class.
    """
    if rich_markdown and line.startswith(ASSISTANT_LINE_PREFIX):
        body = line[len(ASSISTANT_LINE_PREFIX) :]
        if body.strip():
            answer = markdown_to_content(body, width or _DEFAULT_MARKDOWN_WIDTH)
            return _chip("model", _MODEL_COLOR).append("\n").append(answer)
    if line.startswith("you> "):
        rest = line[len("you> ") :]
        chip = _chip("you", _USER_COLOR)
        return chip.append(Content.styled(f" {rest}", _USER_COLOR)) if rest else chip
    request = _TOOL_REQUEST.match(line)
    if request:
        # The model asked to run a tool: keep the dim "model> requested … tool"
        # framing, only the tool name is tinted so the eye lands on which tool.
        return (
            Content.styled("model> requested ", _TRACE_TEXT_STYLE)
            .append(Content.styled(request["name"], f"bold {_TOOL_COLOR}"))
            .append(Content.styled(" tool", _TRACE_TEXT_STYLE))
        )
    if line.startswith(_TOOL_LINE_PREFIX):
        # A tool's lifecycle ("tool> <name> started/completed …"): tint only the
        # tool name, status stays in the dim trace gray.
        rest = line[len(_TOOL_LINE_PREFIX) :]
        name, sep, status = rest.partition(" ")
        result = Content.styled(_TOOL_LINE_PREFIX, _TRACE_TEXT_STYLE).append(
            Content.styled(name, f"bold {_TOOL_COLOR}")
        )
        if sep:
            result = result.append(Content.styled(f" {status}", _TRACE_TEXT_STYLE))
        return result
    if line.startswith(("thinking> ", "working> ")):
        return _BLANK_RUN.sub("\n", line)
    return line


CODE_AI_LOGO = load_code_ai_logo()
