from __future__ import annotations

import asyncio
import base64
import logging
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any

from rich.text import Text

from code_ai.bootstrap import build_application
from code_ai.config.loader import persist_config_updates
from code_ai.core.workflows import render_workflow_invocation
from code_ai.providers.model_listing import list_available_models
from code_ai.providers.models import ImageContent
from code_ai.tools.skills.common import discover_skills_from, render_skill_invocation
from code_ai.ui.terminal.clipboard import (
    copy_to_system_clipboard,
    linux_clipboard_packages,
    paste_from_system_clipboard,
    paste_image_from_system_clipboard,
)
from code_ai.ui.terminal.code_view import (
    live_code_lexer,
    live_code_title,
    render_live_code,
)
from code_ai.ui.terminal.controller import TerminalController
from code_ai.ui.terminal.slash_commands import (
    SlashCommand,
    command_completion,
    config_commands,
    handle_config_command,
    handle_debug_command,
    render_suggestions,
    skill_commands,
    workflow_commands,
)
from code_ai.ui.terminal.view_models import TerminalViewModel
from code_ai.ui.terminal.widgets import (
    CODE_AI_BANNER_FONT_OPTIONS,
    THINKING_PREFIX,
    WORKING_BASE_COLOR,
    WORKING_IDLE_STYLE,
    WORKING_LABEL_STYLE,
    WORKING_PULSE_PERIOD,
    WORKING_SPINNERS,
    WORKING_STATES,
    SpinnerStyle,
    build_plan_steps,
    conversation_line_class,
    load_code_ai_logo,
    normalize_banner_font,
    render_context_meter,
    render_conversation_line,
    render_plan,
    render_subagent_header,
    render_subagent_task,
    render_subagents_summary,
    render_terminal_screen,
    resolve_spinner,
    spinner_color,
    subagent_task_preview,
    thinking_body,
    thinking_panel_body,
    thinking_size_label,
    working_label,
)

logger = logging.getLogger(__name__)

# Labels shown in the permission-mode dropdown next to the input, mapped to the
# config values consumed by AppConfig.permission_mode.
_PERMISSION_MODE_OPTIONS = [
    ("perm: solicitar", "ask"),
    ("perm: automático", "auto"),
    ("perm: ignorar", "bypass"),
]

# How many rows of the live streaming line the (non-scrolling) tail strip shows.
_STREAM_TAIL_MAX_ROWS = 14

# Hard character budget for the live tail, independent of newlines. The strip is
# ~16 rows tall (see #stream-tail in theme.tcss); a few thousand chars is more
# than covers that on a wide pane, and it caps the per-delta word-wrap cost so a
# long single-paragraph reasoning block can no longer freeze the terminal.
_STREAM_TAIL_MAX_CHARS = 4000

# Cap on how many committed lines are kept mounted in the scrollback. Each line
# is a selectable widget, so this bounds widget count the way RichLog.max_lines
# used to bound its strips; the oldest lines scroll off once the cap is hit.
_MAX_CONVERSATION_LINES = 2000


def render_stream_tail(line: str):
    """Render the live streaming line, keeping its newest rows visible.

    The tail Static cannot scroll, so a streaming answer taller than the strip
    would otherwise clip its newest (bottom) text off-screen and look frozen.
    Show only the final rows while it streams; the complete text lands in the
    scrollable log once the turn finishes.
    """
    # Cap by characters first: reasoning text often arrives as long paragraphs
    # with few newlines, so the row cap alone never trims it and the full
    # (unbounded) string gets word-wrapped on every delta — an O(n^2) freeze.
    # The tail only shows a handful of rows and cannot scroll, so dropping the
    # leading text is safe; the complete line is still committed in full.
    if len(line) > _STREAM_TAIL_MAX_CHARS:
        line = line[-_STREAM_TAIL_MAX_CHARS:]
    rows = line.split("\n")
    if len(rows) > _STREAM_TAIL_MAX_ROWS:
        line = "\n".join(rows[-_STREAM_TAIL_MAX_ROWS:])
    return render_conversation_line(line)


def _rows_signature(rows: list[dict[str, str]]) -> tuple[tuple[tuple[str, str], ...], ...]:
    """A comparable snapshot of plan steps / sub-agent rows.

    The view model hands out the same dictionaries every time and mutates them
    in place as work progresses, so a panel cannot tell "unchanged" from
    "changed" by identity or by comparing the list it kept. Materialising the
    contents is cheap (a handful of short strings) next to the repaint it saves.
    """
    return tuple(tuple(sorted(row.items())) for row in rows)


def create_terminal_app(application, *, config_path: Path | None = None):
    from textual.app import App, ComposeResult, SystemCommand
    from textual.command import SimpleCommand
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.message import Message
    from textual.widget import Widget
    from textual.widgets import (
        Button,
        Collapsible,
        Footer,
        Header,
        Select,
        Static,
        TextArea,
    )

    from code_ai.ui.terminal.approval import TerminalApprovalGateway
    from code_ai.ui.terminal.doctor import DoctorModal
    from code_ai.ui.terminal.questions import QuestionnaireModal

    # Saved workflows and skills become slash commands. Both lists are authored by
    # the user (on disk, possibly in another agent's directory), so they are read
    # from disk rather than declared - and cached for a few seconds because the
    # completion popup asks for them on every keystroke.
    workflow_service = getattr(application, "workflows", None)
    session_skill_sources = tuple(getattr(application, "skill_sources", ()) or ())
    asset_cache: dict[str, tuple[float, list[Any]]] = {}
    ASSET_CACHE_TTL = 5.0

    def _cached(key: str, loader, *, refresh: bool) -> list[Any]:
        stamp, records = asset_cache.get(key, (0.0, []))
        now = monotonic()
        if refresh or not stamp or now - stamp > ASSET_CACHE_TTL:
            records = loader()
            asset_cache[key] = (now, records)
        return records

    def workflow_records(*, refresh: bool = False) -> list[Any]:
        if workflow_service is None:
            return []
        return _cached("workflows", workflow_service.load, refresh=refresh)

    def skill_records(*, refresh: bool = False) -> list[Any]:
        if not session_skill_sources:
            return []
        return _cached(
            "skills",
            lambda: discover_skills_from(session_skill_sources),
            refresh=refresh,
        )

    def asset_suggestions(*, refresh: bool = False) -> list[SlashCommand]:
        # Workflows before skills: running a saved procedure is the more specific
        # intent when a name exists as both.
        return [
            *workflow_commands(workflow_records(refresh=refresh)),
            *skill_commands(skill_records(refresh=refresh)),
        ]

    class MultilineInput(TextArea):
        """Multi-line prompt with shell-style history recall.

        Enter submits the prompt; Shift+Enter / Ctrl+J / Alt+Enter insert a
        newline so a single prompt can span several lines. Submitted prompts
        are pushed onto a history stack walked with Up/Down — but only when the
        cursor sits on the first/last line, so navigating a multi-line draft
        still moves between its lines like any editor.

        Ctrl+V pastes from the OS clipboard, preferring an image rendition:
        a pasted image becomes an ``[Image #N]`` placeholder in the text and
        travels with the submitted prompt so a vision model can see it.
        """

        class Submitted(Message):
            """Posted when the user presses Enter to send the prompt."""

            def __init__(
                self,
                input: MultilineInput,
                value: str,
                images: list[ImageContent] | None = None,
            ) -> None:
                super().__init__()
                self.input = input
                self.value = value
                self.images = list(images or [])

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._history: list[str] = []
            # None means "not browsing"; the live draft is whatever is typed.
            self._history_index: int | None = None
            self._draft = ""
            # Images pasted into the current draft, keyed by the placeholder
            # text standing in for them ("[Image #1]", ...).
            self._images: list[tuple[str, ImageContent]] = []

        def attach_image(self, image: ImageContent) -> str:
            placeholder = f"[Image #{len(self._images) + 1}]"
            self._images.append((placeholder, image))
            self.insert(placeholder)
            return placeholder

        def take_images(self, text: str) -> list[ImageContent]:
            """Claim this draft's images whose placeholder survived editing.

            Deleting an ``[Image #N]`` placeholder from the prompt drops that
            attachment, exactly like removing it from the text. The pending
            list is cleared either way — attachments never leak into the next
            prompt (history recall re-sends only the placeholder text).
            """
            taken = [image for placeholder, image in self._images if placeholder in text]
            self._images.clear()
            return taken

        def _attach_clipboard_image(self) -> bool:
            """Attach the clipboard's image, if it holds one. True when it did."""

            pasted_image = paste_image_from_system_clipboard()
            if pasted_image is None:
                return False
            data, media_type = pasted_image
            self.attach_image(
                ImageContent(
                    data=base64.b64encode(data).decode("ascii"),
                    media_type=media_type,
                )
            )
            return True

        def _paste_from_clipboard(self, *, image_only: bool = False) -> None:
            if self._attach_clipboard_image():
                return
            if image_only:
                self.notify(
                    "Nenhuma imagem na área de transferência.", severity="warning"
                )
                return
            text = paste_from_system_clipboard()
            if text:
                self.insert(text)
                return
            packages = linux_clipboard_packages()
            if packages:
                # Nothing was pasted because no clipboard tool exists on this
                # machine; without a hint the failure is indistinguishable
                # from an empty clipboard.
                self.notify(
                    "Área de transferência inacessível: instale "
                    + " ou ".join(packages)
                    + " para habilitar o Ctrl+V.",
                    severity="warning",
                )

        def _on_paste(self, event) -> None:
            """Catch the paste the *terminal* performed, not the key we asked for.

            Windows Terminal (and others) handle Ctrl+V themselves: they take the
            clipboard's text and send it back as a bracketed paste, so the app is
            never told a key was pressed and the image branch below could never
            run. What arrives here is only ever text - and when the clipboard
            holds a picture and nothing else, there is no text, so the terminal
            sends nothing at all and pasting appears to do nothing.

            An empty paste is therefore the signal worth acting on: it means the
            clipboard had nothing pasteable as text, which is exactly when an
            image rendition is worth going to fetch. A paste that does carry text
            is left alone - a clipboard often holds both, and hijacking an
            ordinary text paste to attach a picture would be its own bug.
            """

            if getattr(event, "text", ""):
                return
            event.stop()
            event.prevent_default()
            self._attach_clipboard_image()

        @property
        def value(self) -> str:
            """Alias for ``text`` so callers can treat it like the old Input."""
            return self.text

        @value.setter
        def value(self, new: str) -> None:
            self.text = new
            self.move_cursor(self.document.end)

        def clear_value(self) -> None:
            self.text = ""

        def remember(self, text: str) -> None:
            """Record a submitted entry and reset the browse cursor."""
            entry = text.rstrip("\n")
            # Skip blanks and consecutive duplicates, like a shell history.
            if entry.strip() and (not self._history or self._history[-1] != entry):
                self._history.append(entry)
            self._history_index = None
            self._draft = ""

        def _recall(self, value: str) -> None:
            self.text = value
            self.move_cursor(self.document.end)

        def _history_prev(self) -> bool:
            """Step to the previous (older) entry; True if anything happened."""
            if not self._history:
                return False
            if self._history_index is None:
                # Entering history: stash the unsent draft to restore later.
                self._draft = self.value
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            # else: already at the oldest entry — hold position.
            self._recall(self._history[self._history_index])
            return True

        def _history_next(self) -> bool:
            """Step to the next (newer) entry; True if anything happened."""
            if self._history_index is None:
                return False
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                self._recall(self._history[self._history_index])
            else:
                # Past the newest entry: drop back to the saved draft.
                self._history_index = None
                self._recall(self._draft)
            return True

        async def _on_key(self, event) -> None:
            key = event.key
            if key == "enter":
                # Enter sends the prompt; a newline needs an explicit modifier.
                event.stop()
                event.prevent_default()
                self.post_message(self.Submitted(self, self.text, self.take_images(self.text)))
                return
            if key == "ctrl+v":
                # Explicit paste reads the OS clipboard directly, so an image
                # (which the terminal's own paste path cannot deliver) becomes
                # an attachment. Regular terminal paste still works unchanged.
                event.stop()
                event.prevent_default()
                self._paste_from_clipboard()
                return
            if key in ("alt+v", "ctrl+alt+v"):
                # The way in when the terminal keeps Ctrl+V for itself, which
                # Windows Terminal does by default: it pastes the clipboard's
                # text and the key never reaches us, so an image-only clipboard
                # has no other route. Alt+V is not claimed by those terminals.
                event.stop()
                event.prevent_default()
                self._paste_from_clipboard(image_only=True)
                return
            if key in ("shift+enter", "ctrl+j", "alt+enter"):
                event.stop()
                event.prevent_default()
                self.insert("\n")
                return
            if key == "tab" and "\n" not in self.text:
                # Accept a slash-command completion in-place, like the old
                # single-line prompt did on cursor movement.
                completion = command_completion(self.text, extra=asset_suggestions())
                if completion:
                    event.stop()
                    event.prevent_default()
                    self._recall(completion)
                    return
            if key == "up" and self.cursor_at_first_line and self._history_prev():
                event.stop()
                event.prevent_default()
                return
            if key == "down" and self.cursor_at_last_line and self._history_next():
                event.stop()
                event.prevent_default()
                return
            await super()._on_key(event)

    class WorkingIndicator(Static):
        """Animated "the agent is busy" indicator shown below the conversation.

        A single fast timer drives everything: frames advance at the selected
        style's interval while the color pulse is derived from wall-clock time,
        so even single-glyph styles stay silky. The timer is paused whenever the
        agent is idle, so it costs nothing when nothing is happening.
        """

        TICK = 0.06

        def __init__(self, style: SpinnerStyle, **kwargs: Any) -> None:
            super().__init__("", **kwargs)
            self._style = style
            self._label = "working"
            self._active = False
            self._frame = 0
            self._start = 0.0
            self._last_frame = 0.0
            self._timer = None

        def on_mount(self) -> None:
            self._timer = self.set_interval(self.TICK, self._tick, pause=True)
            self._render_idle()

        def set_style(self, style: SpinnerStyle) -> None:
            self._style = style
            self._frame = 0
            if self._active:
                self._render_active()
            else:
                self._render_idle()

        def set_running(self, running: bool, label: str = "working") -> None:
            self._label = label
            if running and not self._active:
                self._active = True
                now = monotonic()
                self._start = now
                self._last_frame = now
                self._frame = 0
                if self._timer is not None:
                    self._timer.resume()
                self._render_active(now)
            elif not running and self._active:
                self._active = False
                if self._timer is not None:
                    self._timer.pause()
                self._render_idle()

        def _tick(self) -> None:
            now = monotonic()
            if now - self._last_frame >= self._style.interval:
                self._frame += 1
                self._last_frame = now
            self._render_active(now)

        def _render_active(self, now: float | None = None) -> None:
            now = monotonic() if now is None else now
            frames = self._style.frames
            glyph = frames[self._frame % len(frames)]
            if self._style.pulse:
                color = spinner_color((now - self._start) / WORKING_PULSE_PERIOD)
            else:
                color = WORKING_BASE_COLOR
            seconds = int(now - self._start)
            text = Text()
            text.append(glyph, style=f"bold {color}")
            text.append(
                f"  {self._label} ({seconds}s · ctrl+c to interrupt)",
                style=WORKING_LABEL_STYLE,
            )
            self.update(text)

        def _render_idle(self) -> None:
            self.update(Text(self._style.frames[0], style=WORKING_IDLE_STYLE))

    class LiveCodePanel(Static):
        """The window a write opens in, filled as the source streams into it.

        It is a window, not a stretch of chat: a titled frame naming the call
        and its target, the model's own reason for the change, and the source
        underneath. The frame is drawn as soon as the call starts, before a
        single character of source has arrived, so the user reads *why* first
        and then watches the *what* fill in - the order the approval dialog
        presents them in once the call is complete.

        Two things keep repainting cheap enough to do on every fragment. The
        paint is rate-limited by a timer instead of following the event rate, so
        a fast local model cannot drive the terminal past ~16 repaints a second.
        And the lexer is resolved once per file rather than re-guessed from a
        growing fragment, which would otherwise repaint the window in a new
        palette as the file takes shape.

        The window is replaced whole on each paint, never appended to, so it
        never tears: what the user sees is always one consistent snapshot.
        """

        TICK = 0.06

        def __init__(self, style: SpinnerStyle, **kwargs: Any) -> None:
            super().__init__("", **kwargs)
            self._style = style
            self._signature: tuple[object, ...] | None = None
            self._pending: tuple[str, str, str, str, str, bool] | None = None
            self._lexer_key: tuple[str, str] | None = None
            self._lexer = "text"
            self._frame = 0
            self._timer = None

        def on_mount(self) -> None:
            self._timer = self.set_interval(self.TICK, self._tick, pause=True)

        def set_style(self, style: SpinnerStyle) -> None:
            self._style = style

        def update_code(
            self,
            tool: str,
            path: str,
            code_key: str,
            reason: str,
            code: str,
            complete: bool,
        ) -> None:
            # Length is enough to detect growth: the buffer is append-only.
            signature = (tool, path, code_key, reason, len(code), complete)
            if signature == self._signature:
                return
            self._signature = signature
            self._pending = (tool, path, code_key, reason, code, complete)
            if self._timer is not None:
                self._timer.resume()

        def _tick(self) -> None:
            if self._pending is None:
                # Caught up: stop ticking until the next fragment arrives.
                if self._timer is not None:
                    self._timer.pause()
                return
            tool, path, code_key, reason, code, complete = self._pending
            self._pending = None
            self._frame += 1
            title, subtitle = live_code_title(tool, path)
            self.border_title = title
            self.border_subtitle = subtitle
            self.update(
                render_live_code(
                    tool=tool,
                    path=path,
                    code=code,
                    code_key=code_key,
                    reason=reason,
                    complete=complete,
                    lexer=self._resolve_lexer(tool, path, code, complete),
                    glyph=self._glyph(),
                )
            )

        def _glyph(self) -> str:
            frames = self._style.frames
            return frames[self._frame % len(frames)]

        def _resolve_lexer(self, tool: str, path: str, code: str, complete: bool) -> str:
            """Settle the language once per file, not once per fragment.

            Re-resolved only when the target changes - which includes a path
            that arrives after the source has already started, so a file that
            began plain gets its colours the moment its name is known.
            """
            key = (tool, path)
            if key != self._lexer_key or self._lexer == "text":
                self._lexer_key = key
                self._lexer = live_code_lexer(tool, path, code, complete)
            return self._lexer

    class ThinkingPanel(Static):
        """The model's reasoning while it streams, held inside its own box.

        Reasoning used to stream straight into the tail strip, which has no
        frame: a long block simply spilled down the pane, at full width and at
        column 0, reading as if it had escaped the conversation. It is the
        agent's work trace, not a message, so it gets the same quiet round frame
        a sub-agent card gets - titled ``thinking``, dim inside, sized to the
        rows it can actually show - instead of the orange accent the code window
        uses for a write.

        Repaints are rate-limited by a timer rather than following the delta
        rate (the same trick the code window uses): a reasoning model emits
        hundreds of fragments a second, and painting each one is what made the
        terminal stop responding. The panel also only ever renders its newest
        rows, so the per-paint cost stays constant however long the model
        reasons; the whole block is still committed to the transcript.
        """

        TICK = 0.06

        def __init__(self, **kwargs: Any) -> None:
            super().__init__("", markup=False, **kwargs)
            self._pending: str | None = None
            self._painted: int | None = None
            self._timer = None

        def on_mount(self) -> None:
            self.border_title = "thinking"
            self._timer = self.set_interval(self.TICK, self._tick, pause=True)

        def update_reasoning(self, line: str) -> None:
            """Queue the newest reasoning; the timer decides when to paint it."""
            # Length alone identifies the state: the line only ever grows.
            if len(line) == self._painted:
                return
            self._pending = line
            if self._timer is not None:
                self._timer.resume()

        def reset(self) -> None:
            """Drop the reasoning being shown - the turn moved on without it."""
            self._pending = None
            self._painted = None
            if self._timer is not None:
                self._timer.pause()
            self.update("")

        def flush(self) -> None:
            """Paint whatever is queued right now, without waiting for the tick.

            Used when the panel first opens, so reasoning appears the moment the
            model starts thinking instead of up to one tick later.
            """
            self._tick()

        def _tick(self) -> None:
            if self._pending is None:
                # Caught up: stop ticking until the next fragment arrives.
                if self._timer is not None:
                    self._timer.pause()
                return
            line = self._pending
            self._pending = None
            self._painted = len(line)
            self.border_subtitle = thinking_size_label(line)
            self.update(thinking_panel_body(line))

    class PlanPanel(Static):
        """Checklist of planned steps shown beside the conversation.

        Cards are driven straight from the plan snapshot in the view model
        (done / running / pending / failed). The running card's marker is the
        live spinner, so its timer only runs while a step is in progress.
        """

        TICK = 0.08

        def __init__(self, style: SpinnerStyle, **kwargs: Any) -> None:
            super().__init__("", **kwargs)
            self._style = style
            self._steps: list[dict[str, str]] = []
            self._progress = "-"
            self._status = ""
            self._signature: tuple[object, ...] | None = None
            self._frame = 0
            self._running = False
            self._timer = None

        def on_mount(self) -> None:
            self._timer = self.set_interval(self.TICK, self._tick, pause=True)
            self._paint()

        def set_style(self, style: SpinnerStyle) -> None:
            self._style = style
            self._paint()

        def update_plan(self, steps: list[dict[str, str]], progress: str, status: str) -> None:
            # Every event refreshes the status area, and most of them leave the
            # checklist exactly as it was: repaint only on a real change, so a
            # streaming turn cannot drive one repaint per fragment. The spinner
            # keeps animating from its own timer regardless.
            signature = (_rows_signature(steps), progress, status)
            if signature == self._signature:
                return
            self._signature = signature
            self._steps = steps
            self._progress = progress
            self._status = status
            has_running = any(step.get("status") == "running" for step in steps)
            if has_running and not self._running:
                self._running = True
                self._frame = 0
                if self._timer is not None:
                    self._timer.resume()
            elif not has_running and self._running:
                self._running = False
                if self._timer is not None:
                    self._timer.pause()
            self._paint()

        def _tick(self) -> None:
            self._frame += 1
            self._paint()

        def _paint(self) -> None:
            frames = self._style.frames
            glyph = frames[self._frame % len(frames)]
            if self._style.pulse:
                color = spinner_color((self._frame * self.TICK) / WORKING_PULSE_PERIOD)
            else:
                color = WORKING_BASE_COLOR
            self.update(render_plan(self._steps, self._progress, self._status, glyph, color))

    class SubagentCard(Vertical):
        """One delegated sub-agent's card: a title row over its folded task.

        The delegated task collapses into a ``Collapsible`` (like the
        transcript's "thinking" block): a one-line dim preview the user can
        click to read the full text and the agent's latest activity.
        """

        def __init__(self, agent: dict[str, str], **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._agent = dict(agent)
            self._glyph = " "
            self._color = WORKING_BASE_COLOR

        def compose(self) -> ComposeResult:
            yield Static("", classes="agent-card-header")
            yield Collapsible(
                Static("", classes="agent-task-body", markup=False),
                title=subagent_task_preview(self._agent.get("task", "")),
                collapsed=True,
                classes="agent-task",
            )

        def on_mount(self) -> None:
            self._apply()

        def paint(self, agent: dict[str, str], glyph: str, color: str) -> None:
            self._agent = dict(agent)
            self._glyph = glyph
            self._color = color
            # A freshly created card paints itself in on_mount instead; its
            # composed children do not exist yet.
            if self.is_mounted:
                self._apply()

        def _apply(self) -> None:
            self.query_one(".agent-card-header", Static).update(
                render_subagent_header(self._agent, self._glyph, self._color)
            )
            self.query_one(Collapsible).title = subagent_task_preview(self._agent.get("task", ""))
            self.query_one(".agent-task-body", Static).update(render_subagent_task(self._agent))

    class SubagentPanel(Vertical):
        """Live roster of delegated sub-agents, shown below the plan panel.

        One card per agent (see SubagentCard) under a one-line summary. Cards
        are keyed by agent id and repainted in place — never remounted while
        the roster only grows — so a task the user expanded stays expanded.
        Mirrors the PlanPanel's spinner: running cards borrow the shared
        glyph, and the timer only ticks while at least one agent is working.
        """

        TICK = 0.08

        def __init__(self, style: SpinnerStyle, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._style = style
            self._agents: list[dict[str, str]] = []
            self._cards: dict[str, SubagentCard] = {}
            self._signature: tuple[object, ...] | None = None
            self._frame = 0
            self._running = False
            self._timer = None

        def compose(self) -> ComposeResult:
            yield Static("", classes="agents-summary")

        def on_mount(self) -> None:
            self._timer = self.set_interval(self.TICK, self._tick, pause=True)
            self._paint()

        def set_style(self, style: SpinnerStyle) -> None:
            self._style = style
            self._paint()

        def update_agents(self, agents: list[dict[str, str]]) -> None:
            # The roster's rows are mutated in place as agents work, so identity
            # says nothing: compare their contents. Unchanged means no repaint,
            # for the same reason the plan panel skips one.
            signature = _rows_signature(agents)
            if signature == self._signature:
                return
            self._signature = signature
            self._agents = agents
            self._sync_cards()
            has_running = any(agent.get("status") == "running" for agent in agents)
            if has_running and not self._running:
                self._running = True
                self._frame = 0
                if self._timer is not None:
                    self._timer.resume()
            elif not has_running and self._running:
                self._running = False
                if self._timer is not None:
                    self._timer.pause()
            self._paint()

        def _sync_cards(self) -> None:
            """Mount/remove cards so one exists per roster entry.

            The roster only appends within a turn and resets between turns, so
            growth reuses the existing cards (keeping their collapsed state)
            and anything else rebuilds from scratch.
            """
            keys = [
                agent.get("agent_id") or f"agent-{index}"
                for index, agent in enumerate(self._agents)
            ]
            current = list(self._cards)
            if keys == current:
                return
            if keys[: len(current)] != current:
                for card in self._cards.values():
                    card.remove()
                self._cards = {}
                current = []
            for key, agent in zip(keys[len(current) :], self._agents[len(current) :], strict=True):
                card = SubagentCard(agent, classes="agent-card")
                self._cards[key] = card
                self.mount(card)

        def _tick(self) -> None:
            self._frame += 1
            self._paint()

        def _paint(self) -> None:
            frames = self._style.frames
            glyph = frames[self._frame % len(frames)]
            if self._style.pulse:
                color = spinner_color((self._frame * self.TICK) / WORKING_PULSE_PERIOD)
            else:
                color = WORKING_BASE_COLOR
            self.query_one(".agents-summary", Static).update(render_subagents_summary(self._agents))
            for card, agent in zip(self._cards.values(), self._agents, strict=True):
                card.paint(agent, glyph, color)

    class CodeAITerminalApp(App[None]):
        CSS_PATH = "theme.tcss"
        BINDINGS = [
            ("ctrl+c", "cancel_or_quit", "Cancel/Quit"),
            ("ctrl+q", "quit", "Quit"),
            ("ctrl+l", "clear", "Clear"),
            ("ctrl+b", "toggle_session", "Session panel"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.vm = TerminalViewModel()
            self.vm.permission_mode = application.session.config.permission_mode
            self.controller = TerminalController(application, self.vm)
            self.follow_output = True
            # Number of conversation lines already flushed to the append-only log.
            # Streaming only mutates the last line, so we keep the tail in a
            # separate Static and never re-render the whole transcript per event.
            self._committed = 0
            # Armed by a first Ctrl+Q that could not shut down cleanly (e.g. the
            # model is hung mid-request); a second Ctrl+Q while armed force-quits.
            self._force_quit_armed = False
            self._close_task: asyncio.Task[None] | None = None
            # Guards the question dialog against being pushed twice: every event
            # after the turn ends would otherwise see the same pending questions.
            self._questions_open = False
            # Background tasks this screen started, held so the loop cannot
            # collect one mid-flight (see _spawn).
            self._tasks: set[asyncio.Task[Any]] = set()
            # What each status widget is currently showing, keyed by widget.
            # Every event refreshes the whole status area, but a streaming turn
            # emits hundreds of events a second that change none of it: repainting
            # them anyway cost six widget updates - and six layout invalidations -
            # per reasoning fragment, which is what made the terminal stop
            # responding while the model was thinking.
            self._painted: dict[str, object] = {}

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="root"):
                with Container(id="topbar"):
                    yield Static(
                        load_code_ai_logo(application.session.config.terminal_banner_font),
                        id="logo",
                    )
                    yield Static("Any model. Real tools. Local control.", id="subtitle")
                    # Carries the configured model name and workspace folder,
                    # neither of which is ours to sanitise — a single bracket in
                    # either would otherwise raise MarkupError on every status
                    # refresh. Nothing in this UI styles via markup strings;
                    # colour always comes from Text/Content renderables, which
                    # markup=False leaves untouched.
                    yield Static("READY", id="statusline", markup=False)
                    yield Static("", id="context-meter")
                with Horizontal(id="main"):
                    with Vertical(id="session"):
                        with Horizontal(id="session-header"):
                            yield Static("SESSION", classes="panel-title", id="session-title")
                            yield Button("‹", id="toggle-session", classes="collapse-btn")
                        # Includes the model-authored current plan step, so the
                        # same reasoning as the status line applies.
                        yield Static("", id="session-info", markup=False)
                    with Vertical(id="conversation-pane"):
                        # One selectable Static per committed line (inside a
                        # scroller) instead of a RichLog: RichLog renders to
                        # Strips that carry no offset metadata, so Textual's
                        # screen text-selection can never highlight or copy from
                        # it. Content widgets do, so the transcript is now
                        # drag-selectable like a terminal scrollback.
                        yield VerticalScroll(id="conversation")
                        # The file being written right now, between the
                        # transcript and the live text tail: it belongs to the
                        # turn in progress, not to the scrollback behind it.
                        code_window = LiveCodePanel(
                            resolve_spinner(application.session.config.terminal_spinner),
                            id="code-window",
                        )
                        code_window.display = False
                        yield code_window
                        # ``markup=False`` for the same reason the committed
                        # lines set it: trace lines carry raw tool and model
                        # text, and a stray bracket ("evidence> FILE_READ:
                        # [1/3] ...") would otherwise be parsed as console
                        # markup and raise MarkupError inside the event
                        # subscriber. Styled Content renderables are unaffected.
                        # The model's reasoning, boxed. Sits between the code
                        # window and the tail: all three belong to the turn in
                        # progress rather than to the scrollback behind them.
                        thinking = ThinkingPanel(id="thinking-panel")
                        thinking.display = False
                        yield thinking
                        yield Static("", id="stream-tail", markup=False)
                    with Vertical(id="sidebar"):
                        # Two stacked panels, each scrolls internally when its
                        # content outgrows the space: the plan checklist on top,
                        # the live sub-agent roster below it.
                        with Vertical(id="plan"):
                            yield Static("PLAN", classes="panel-title")
                            with VerticalScroll(id="plan-scroll"):
                                yield PlanPanel(
                                    resolve_spinner(application.session.config.terminal_spinner),
                                    id="plan-body",
                                )
                        with Vertical(id="subagents"):
                            yield Static("AGENTS", classes="panel-title")
                            with VerticalScroll(id="subagents-scroll"):
                                yield SubagentPanel(
                                    resolve_spinner(application.session.config.terminal_spinner),
                                    id="subagents-body",
                                )
                        # Live viewport of the interactive PTY session (agent-
                        # or user-driven); appears with the first screen update
                        # and stays across turns while the session lives.
                        with Vertical(id="terminal"):
                            yield Static("TERMINAL", classes="panel-title")
                            with VerticalScroll(id="terminal-scroll"):
                                yield Static("", id="terminal-body", markup=False)
                yield WorkingIndicator(
                    resolve_spinner(application.session.config.terminal_spinner),
                    id="working-indicator",
                )
                # Lists skill and workflow names read off disk, so the same
                # reasoning as the status line applies.
                suggestions = Static("", id="command-suggestions", markup=False)
                suggestions.display = False
                yield suggestions
                with Horizontal(id="input-row"):
                    yield Select(
                        _PERMISSION_MODE_OPTIONS,
                        value=application.session.config.permission_mode,
                        allow_blank=False,
                        id="mode-select",
                    )
                    yield MultilineInput(
                        id="input",
                        soft_wrap=True,
                        tab_behavior="focus",
                        show_line_numbers=False,
                    )
            yield Footer()

        async def on_mount(self) -> None:
            application.subscribe(self._on_event)
            # Route gated tool calls through an interactive approve/deny modal.
            application.orchestrator.approval_gateway = TerminalApprovalGateway(
                self, application.session.config
            )
            self._apply_configured_terminal_theme()
            self._apply_session_collapsed(application.session.config.terminal_session_collapsed)
            self.theme_changed_signal.subscribe(self, self._persist_terminal_theme)
            await application.start()
            self._refresh_status()
            self.query_one("#input", MultilineInput).focus()

        async def _on_event(self, event) -> None:
            await self.controller.handle_event(event)
            self._render_event(event)
            self._open_questions_if_waiting()

        def _open_questions_if_waiting(self) -> None:
            """Show the question cards once the turn that asked them has ended.

            Deliberately not opened the moment ask_user fires: the turn is still
            finishing, and a dialog over a moving transcript hides the question's
            own text. Waiting for the turn to end means the questions are in the
            scrollback first, so closing the dialog leaves something to answer.
            """

            if self._questions_open or not self.vm.pending_questions:
                return
            if not self.vm.turn_is_over():
                return
            questionnaire = self.vm.take_questionnaire()
            if questionnaire.is_empty:
                return
            self._questions_open = True
            self.push_screen(QuestionnaireModal(questionnaire), self._questions_answered)

        def _questions_answered(self, answers) -> None:
            self._questions_open = False
            if not answers:
                # Dismissed without answering: the questions are in the
                # transcript and the input is right there, so there is nothing
                # to recover from.
                return
            self.run_worker(self.controller.answer_questions(answers))

        def _render_event(self, event) -> None:
            self._sync_conversation()
            self._refresh_status()

        def _commit_conversation_line(self, line: str) -> Widget:
            """Build the selectable widget for one committed transcript line.

            Each line is its own Static (a content widget) so Textual's screen
            text-selection can highlight and copy across the scrollback —
            something RichLog's Strips never supported. ``markup=False`` keeps
            literal text like ``tool> [..]`` from being parsed as console markup.

            Assistant answers render as Markdown flattened to native Content (see
            ``markdown_to_content``), so they stay selectable; the layout width is
            taken from the live conversation pane so the Markdown wraps to fit.

            The model's reasoning is folded into a collapsed ``Collapsible`` (like
            the VS Code extension's hideable "Thinking" section) so it never
            dominates the transcript, but stays one click away.
            """
            reasoning = thinking_body(line)
            if reasoning is not None:
                return Collapsible(
                    Static(reasoning, markup=False),
                    title="thinking",
                    collapsed=True,
                    classes="thinking-block",
                )
            return Static(
                render_conversation_line(
                    line, rich_markdown=True, width=self._conversation_width()
                ),
                markup=False,
                classes=conversation_line_class(line),
            )

        def _conversation_width(self) -> int | None:
            """Content width of the conversation pane, if it is laid out yet."""
            try:
                width = self.query_one("#conversation", VerticalScroll).content_size.width
            except Exception:
                return None
            return width or None

        def _sync_conversation(self) -> None:
            """Incrementally reflect the conversation buffer into the UI.

            The scrollback is append-only: each line is mounted exactly once as
            its own selectable Static, so the cost per event is proportional to
            the number of *new* lines, not the whole transcript.

            While the agent is working the last line may still be growing from
            streaming deltas, so it is held live in the ``#stream-tail`` Static
            and kept out of the scrollback (re-rendering a growing line every
            delta is what used to freeze the terminal). As soon as the agent
            goes idle that line is final: it is committed to the scrollback and
            the tail cleared, so a finished answer lands in the conversation
            instead of being stranded in the strip below it.
            """
            convo = self.vm.conversation
            log = self.query_one("#conversation", VerticalScroll)
            tail = self.query_one("#stream-tail", Static)

            if self._committed > len(convo):
                # The buffer shrank (only happens on clear, which also resets the
                # counter): rebuild defensively so the log never shows dead lines.
                log.remove_children()
                self._committed = 0

            # Hold the last line back in the live tail only while the agent is
            # working AND that line is still uncommitted (freshly streaming).
            # Lines already in the scrollback are never pulled back, so a new
            # turn starting (status flips to working before its first line
            # arrives) cannot trigger a flicker or a full re-render.
            working = self.vm.status in WORKING_STATES
            held_back = working and self._committed < len(convo)
            commit_upto = len(convo) - 1 if held_back else len(convo)

            mounted = False
            while self._committed < commit_upto:
                log.mount(self._commit_conversation_line(convo[self._committed]))
                self._committed += 1
                mounted = True

            if mounted:
                # Trim oldest lines past the cap and keep the newest in view,
                # both deferred until the freshly mounted widgets are laid out.
                self.call_after_refresh(self._trim_and_follow_conversation, log)

            self._paint_live_line(convo[-1] if held_back else "", tail)

        def _paint_live_line(self, line: str, tail: Static) -> None:
            """Show the line that is still streaming, wherever it belongs.

            Reasoning goes into the boxed thinking panel; everything else (the
            answer, the working channel, live command output) stays in the plain
            tail strip below it. Only one of the two is ever showing, so the
            reasoning cannot appear both boxed and loose.
            """
            panel = self.query_one("#thinking-panel", ThinkingPanel)
            if line.startswith(THINKING_PREFIX):
                opening = not panel.display
                panel.display = True
                panel.update_reasoning(line)
                if opening:
                    # Paint the first fragment straight away, so the box opens
                    # with reasoning in it rather than empty for a tick.
                    panel.flush()
                if self._changed("stream-tail", ""):
                    tail.update("")
                return
            if panel.display:
                panel.display = False
                panel.reset()
            if self._changed("stream-tail", line):
                tail.update(render_stream_tail(line) if line else "")

        def _trim_and_follow_conversation(self, log: VerticalScroll) -> None:
            excess = len(log.children) - _MAX_CONVERSATION_LINES
            for child in list(log.children)[:excess]:
                child.remove()
            if self.follow_output:
                log.scroll_end(animate=False)

        async def on_text_area_changed(self, event: TextArea.Changed) -> None:
            self._set_command_suggestions(event.text_area.text)

        async def on_multiline_input_submitted(self, event: MultilineInput.Submitted) -> None:
            text = event.value
            event.input.remember(text)
            event.input.clear_value()
            self._set_command_suggestions("")
            if text.strip() == "/quit":
                await self.action_quit()
                return
            if text.strip() == "/clear":
                await self.action_clear()
                return
            if text.strip() == "/compact":
                self._append_conversation_line(await self.controller.compact())
                return
            if text.strip() in {"/auto", "/plan", "/act"}:
                mode = text.strip().lstrip("/")
                if mode == "act":
                    await self._start_act_mode()
                    return
                await self.controller.set_planner_mode(mode)
                self._append_conversation_line(f"command> Planner mode set to {mode}")
                return
            if text.strip() == "/mode" or text.strip().startswith("/mode "):
                await self._handle_mode_command(text.strip())
                return
            if text.strip() == "/deep-plan" or text.strip().startswith("/deep-plan "):
                objective = text.strip()[len("/deep-plan") :].strip()
                await self._start_deep_plan(objective)
                return
            if text.strip() == "/plan-status":
                self._append_conversation_line(self.controller.plan_status())
                return
            if text.strip().startswith("/replan"):
                reason = text.strip()[len("/replan") :].strip() or None
                self._append_conversation_line(await self.controller.replan(reason))
                return
            if text.strip() == "/goal" or text.strip().startswith("/goal "):
                await self._handle_goal_command(text.strip())
                return
            if text.strip() == "/term" or text.strip().startswith("/term "):
                await self._handle_term_command(text.strip())
                return
            if text.strip() == "/cancel":
                await self.controller.cancel()
                return
            if text.strip() == "/debug" or text.strip().startswith("/debug "):
                self._append_conversation_line(
                    handle_debug_command(application, text.strip(), config_path=config_path)
                )
                return
            if text.strip() == "/status":
                self._append_conversation_line(self._session_text())
                return
            if text.strip() == "/doctor":
                self.push_screen(
                    DoctorModal(
                        application,
                        config_path=config_path,
                        on_change=self._refresh_status,
                    )
                )
                return
            if text.strip() == "/help":
                self._append_conversation_line(
                    render_suggestions("/", extra=asset_suggestions())
                )
                return
            if text.strip() == "/workflows":
                self._append_conversation_line(self._workflows_text())
                return
            if text.strip() == "/skills":
                self._append_conversation_line(self._skills_text())
                return
            if text.strip().startswith("/config"):
                await self._dispatch_config(text.strip())
                return
            # Last: a workflow or skill invoked by name (Cline-style "/deploy"), so
            # neither can ever shadow a command the app owns.
            if self._run_asset_command(text.strip(), images=event.images):
                return
            self._spawn(self.controller.submit(text, images=event.images), "the message")

        def _workflows_text(self) -> str:
            """Render the saved workflows, freshly re-read from disk."""

            if workflow_service is None:
                return "command> Workflows are not available in this session."
            records = workflow_records(refresh=True)
            if not records:
                dirs = "\n".join(f"  {source.root}" for source in workflow_service.sources)
                return (
                    "command> No workflows found. Add a markdown file to one of "
                    f"these directories and run it as /<name>:\n{dirs}"
                )
            lines = [f"command> {len(records)} workflow(s) available:"]
            for record in records:
                description = " ".join(record.description.split())
                suffix = f" ({record.origin})" if record.origin != "code-ai" else ""
                lines.append(f"  {record.command:<28} {description}{suffix}")
            return "\n".join(lines)

        def _skills_text(self) -> str:
            """Render the available skills, freshly re-read from disk."""

            if not session_skill_sources:
                return "command> Skills are not available in this session."
            records = skill_records(refresh=True)
            if not records:
                dirs = "\n".join(f"  {source.root}" for source in session_skill_sources)
                return (
                    "command> No skills found. Add a <name>/SKILL.md to one of these "
                    f"directories and force it with /<name>:\n{dirs}"
                )
            lines = [f"command> {len(records)} skill(s) available:"]
            for record in records:
                description = " ".join(record.description.split())
                suffix = f" ({record.origin})" if record.origin != "code-ai" else ""
                lines.append(f"  /{record.name:<27} {description}{suffix}")
            return "\n".join(lines)

        def _run_asset_command(self, stripped: str, *, images) -> bool:
            """Run ``/<name> [extra input]`` when it names a workflow or a skill.

            The saved steps (workflow) or instructions (skill) become the turn's
            prompt, so naming one is a guarantee it gets applied rather than a
            hint the model may ignore. Workflows are matched first: running a
            saved procedure is the more specific intent. Returns False when the
            text names neither, leaving it to be submitted as an ordinary message.
            """

            if not stripped.startswith("/"):
                return False
            token, _, argument = stripped.partition(" ")

            workflow = workflow_service.find(token) if workflow_service is not None else None
            if workflow is not None:
                self._append_conversation_line(
                    f"command> Running workflow {workflow.name} ({workflow.path})"
                )
                self._spawn(
                    self.controller.submit(
                        render_workflow_invocation(workflow, argument), images=images
                    ),
                    f"workflow {workflow.name}",
                )
                return True

            skill = self._find_skill(token)
            if skill is not None:
                self._append_conversation_line(
                    f"command> Using skill {skill.name} ({skill.path})"
                )
                self._spawn(
                    self.controller.submit(
                        render_skill_invocation(
                            skill,
                            argument,
                            max_chars=application.session.config.budgets.max_tool_output_chars,
                        ),
                        images=images,
                    ),
                    f"skill {skill.name}",
                )
                return True
            return False

        def _find_skill(self, token: str):
            """Resolve ``/<name>`` against the skills on disk, or None."""

            wanted = token.lstrip("/").strip().casefold()
            if not wanted:
                return None
            for record in skill_records(refresh=True):
                if record.name.casefold() == wanted:
                    return record
            return None

        async def _start_act_mode(self) -> None:
            """Switch to act mode and, when a plan is ready, run it right away.

            Plain ``/act`` used to only flip the mode, leaving the agent idle
            until the user typed something. When a plan was authored in plan
            mode, kick off the execution turn automatically: the approved
            checklist runs and its sidebar (collapsed when the plan turn ended)
            reappears with live progress. The turn is scheduled as a background
            task so the prompt stays responsive, mirroring a normal submit.
            """
            if self.controller.has_active_plan():
                self._append_conversation_line("command> Executando o plano aprovado…")
                self._spawn(self.controller.start_plan_execution(), "starting the plan")
                return
            await self.controller.set_planner_mode("act")
            self._append_conversation_line(
                "command> Modo act ativado. Descreva a tarefa para começar."
            )

        async def _handle_goal_command(self, stripped: str) -> None:
            """Dispatch ``/goal`` and its subcommands.

            Anything after ``/goal`` that is not a known subcommand is the
            objective itself. Defining a goal derives acceptance criteria via a
            model call, so it runs as a background task to keep the prompt
            responsive; the proposed criteria land in the transcript when ready.
            """
            argument = stripped[len("/goal") :].strip()
            if argument in {"", "status"}:
                self._append_conversation_line(self.controller.goal_status())
                return
            if argument in {"start", "resume"}:
                self._append_conversation_line(await self.controller.start_goal())
                return
            if argument == "stop":
                self._append_conversation_line(await self.controller.stop_goal())
                return
            self._append_conversation_line(
                "goal> Derivando critérios de aceitação para o objetivo…"
            )

            async def _define() -> None:
                line = await self.controller.define_goal(argument)
                self._append_conversation_line(line)

            self._spawn(_define(), "defining the goal")

        async def _handle_term_command(self, stripped: str) -> None:
            """Dispatch ``/term`` — the user's keyboard into the shared PTY.

            The interactive terminal session is the same one the model drives
            through its terminal tools, so the user can take over (answer a
            prompt, Ctrl+C a runaway server) at any point. Anything after
            ``/term`` that is not a known subcommand is typed into the session
            verbatim followed by Enter.
            """
            argument = stripped[len("/term") :].strip()
            if argument in {"", "status"}:
                self._append_conversation_line(self.controller.terminal_status())
                return
            if argument == "start" or argument.startswith("start "):
                command = argument[len("start") :].strip() or None
                self._append_conversation_line(await self.controller.terminal_start(command))
                return
            if argument == "kill":
                self._append_conversation_line(await self.controller.terminal_kill())
                return
            if argument == "enter":
                self._append_conversation_line(await self.controller.terminal_enter())
                return
            if argument == "ctrl" or argument.startswith("ctrl "):
                key = argument[len("ctrl") :].strip().lstrip("+").strip()
                if len(key) != 1:
                    self._append_conversation_line(
                        "term> Uso: /term ctrl <letra> (ex.: /term ctrl c)"
                    )
                    return
                self._append_conversation_line(await self.controller.terminal_control(key))
                return
            self._append_conversation_line(await self.controller.terminal_send(argument))

        async def _start_deep_plan(self, objective: str) -> None:
            """Switch to plan mode and, if an objective was given, plan it now.

            Mirrors Cline's Plan mode: ``/deep-plan <objective>`` flips the planner
            into plan mode and submits the objective in one go, so the model
            investigates and authors a thorough plan without touching the
            workspace. Bare ``/deep-plan`` just arms plan mode for the next message.
            """
            await self.controller.set_planner_mode("plan")
            if not objective:
                self._append_conversation_line(
                    "command> Plan mode on. Type /deep-plan <objetivo> (ou só "
                    "descreva o que quer planejar) e eu monto o plano sem alterar "
                    "nada. Depois use /act para executar."
                )
                return
            self._spawn(self.controller.submit(objective), "the deep plan")

        async def _dispatch_config(self, stripped: str) -> None:
            """Run a ``/config ...`` line, including its UI side effects.

            Shared by the typed prompt and the ``/config help`` picker so both
            paths behave identically (open sub-pickers, repaint the logo, etc.).
            """
            if stripped == "/config help":
                self.action_config_help()
                return
            if stripped == "/config models":
                await self._select_model_interactive()
                return
            self._append_conversation_line(
                handle_config_command(application, stripped, config_path=config_path)
            )
            if stripped.startswith("/config theme "):
                self._apply_configured_terminal_theme()
            if stripped.startswith("/config banner-font "):
                self._refresh_logo()
            if stripped.startswith("/config spinner "):
                self._refresh_spinner()

        def action_config_help(self) -> None:
            """Open a searchable palette of /config commands to pick from."""
            self.search_commands(
                [
                    SimpleCommand(
                        item.command,
                        partial(self._use_config_command, item),
                        item.description,
                    )
                    for item in config_commands()
                ],
                placeholder="Search config commands...",
            )

        def _use_config_command(self, item) -> None:
            input_widget = self.query_one("#input", MultilineInput)
            if "<" in item.command:
                # Needs a value: drop the command stem into the prompt (cursor at
                # the end) so the user types the argument and presses Enter.
                input_widget.text = item.completion_text
                input_widget.move_cursor(input_widget.document.end)
                input_widget.focus()
                self._set_command_suggestions(input_widget.text)
                return
            # No argument to fill — run it right away.
            self._set_command_suggestions("")
            self._spawn(self._dispatch_config(item.completion_text.strip()), "the command")

        def get_system_commands(self, screen) -> Any:
            yield from super().get_system_commands(screen)
            yield SystemCommand(
                "Banner Font",
                "Change the banner art font",
                self.action_change_banner_font,
            )
            yield SystemCommand(
                "Working Spinner",
                "Change the working-indicator animation",
                self.action_change_spinner,
            )

        async def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "toggle-session":
                self.action_toggle_session()

        def action_toggle_session(self) -> None:
            """Collapse the left SESSION panel to a thin strip, or restore it.

            Only the panel's title and details hide; the toggle button stays
            visible (its arrow flips) so the panel can always be reopened. The
            conversation pane is ``width: 1fr`` and reclaims the freed space.
            The choice is persisted so it survives a restart.
            """
            collapsed = not self.query_one("#session").has_class("collapsed")
            self._apply_session_collapsed(collapsed)
            config = application.session.config
            if config.terminal_session_collapsed == collapsed:
                return
            try:
                validated = persist_config_updates(
                    config,
                    {"terminal_session_collapsed": collapsed},
                    explicit_path=config_path,
                )
            except Exception as exc:
                self._append_conversation_line(
                    f"warning> Could not persist session panel state: {exc}"
                )
                return
            config.terminal_session_collapsed = validated.terminal_session_collapsed

        def _apply_session_collapsed(self, collapsed: bool) -> None:
            self.query_one("#session").set_class(collapsed, "collapsed")
            self.query_one("#toggle-session", Button).label = "›" if collapsed else "‹"

        async def on_select_changed(self, event: Select.Changed) -> None:
            if event.select.id != "mode-select":
                return
            value = event.value
            if not isinstance(value, str):
                return
            await self._apply_permission_mode(value)

        async def _handle_mode_command(self, text: str) -> None:
            modes = {"ask", "auto", "bypass"}
            parts = text.split(maxsplit=1)
            requested = parts[1].strip().lower() if len(parts) > 1 else ""
            if requested not in modes:
                current = application.session.config.permission_mode
                self._append_conversation_line(
                    "command> Permission modes:\n"
                    "  ask    - prompt before tools that write/run; escalate denials\n"
                    "  auto   - run allowed tools freely; only ask when policy blocks\n"
                    "  bypass - never prompt (run everything)\n"
                    f"current: {current}\n"
                    "usage: /mode ask | /mode auto | /mode bypass"
                )
                return
            await self._apply_permission_mode(requested)

        async def _apply_permission_mode(self, requested: str) -> None:
            # No-op when already active (e.g. the dropdown's initial Changed on
            # mount, or a re-selection of the current mode); just keep widgets synced.
            if requested == application.session.config.permission_mode:
                self.vm.permission_mode = requested
                self._sync_mode_select(requested)
                return
            try:
                await self.controller.set_permission_mode(requested)
            except Exception as exc:
                self._append_conversation_line(f"warning> Could not set mode: {exc}")
                self._sync_mode_select(application.session.config.permission_mode)
                return
            self.vm.permission_mode = requested
            self._sync_mode_select(requested)
            try:
                persist_config_updates(
                    application.session.config,
                    {"permission_mode": requested},
                    explicit_path=config_path,
                )
            except Exception as exc:
                self._append_conversation_line(
                    f"warning> Mode set for this session but not persisted: {exc}"
                )
            self._refresh_status()

        def _sync_mode_select(self, mode: str) -> None:
            try:
                select = self.query_one("#mode-select", Select)
            except Exception:
                return
            if select.value != mode:
                select.value = mode

        def _set_command_suggestions(self, text: str) -> None:
            suggestions = self.query_one("#command-suggestions", Static)
            rendered = render_suggestions(text, extra=asset_suggestions())
            suggestions.update(rendered)
            suggestions.display = bool(rendered)

        def _spawn(self, coro, what: str) -> asyncio.Task[Any]:
            """Run a coroutine in the background, owned and never silent.

            ``asyncio.create_task`` alone was neither: the loop keeps only a
            weak reference to a task nobody holds, and an exception inside one
            is reported only when it is garbage collected - to a stderr this app
            has taken over. So a turn that died on its way to the model took the
            whole flow with it and left the screen exactly as it was, which is
            what "it just freezes, for no reason" looked like from the outside.

            Held here until it finishes, and whatever it raises is put in the
            transcript where the user can read it.
            """
            task = asyncio.create_task(coro)
            self._tasks.add(task)

            def _finished(finished: asyncio.Task[Any]) -> None:
                self._tasks.discard(finished)
                if finished.cancelled():
                    return
                error = finished.exception()
                if error is not None:
                    logger.exception("%s failed", what, exc_info=error)
                    self._append_conversation_line(
                        f"error> {what} failed ({type(error).__name__}: {error})"
                    )

            task.add_done_callback(_finished)
            return task

        def _append_conversation_line(self, text: str) -> None:
            if not text:
                return
            self.vm.conversation.extend(text.splitlines())
            self._sync_conversation()
            self._refresh_status()

        def _apply_configured_terminal_theme(self) -> None:
            theme_name = application.session.config.terminal_theme
            if self.theme == theme_name:
                return
            if theme_name not in self.available_themes:
                self._append_conversation_line(
                    f"warning> Unknown terminal theme in config: {theme_name}"
                )
                return
            self.theme = theme_name

        def _refresh_logo(self) -> None:
            config = application.session.config
            config.terminal_banner_font = normalize_banner_font(config.terminal_banner_font)
            self.query_one("#logo", Static).update(load_code_ai_logo(config.terminal_banner_font))

        def _persist_banner_font(self, font: str) -> None:
            config = application.session.config
            normalized = normalize_banner_font(font)
            if config.terminal_banner_font == normalized:
                self._refresh_logo()
                return
            try:
                validated = persist_config_updates(
                    config,
                    {"terminal_banner_font": normalized},
                    explicit_path=config_path,
                )
            except Exception as exc:
                self._append_conversation_line(f"warning> Could not persist banner font: {exc}")
                return
            config.terminal_banner_font = validated.terminal_banner_font
            self._refresh_logo()

        def action_change_banner_font(self) -> None:
            self.search_commands(
                [
                    SimpleCommand(
                        font,
                        partial(self._persist_banner_font, font),
                        f"Use {font} for the Code-AI banner.",
                    )
                    for font in CODE_AI_BANNER_FONT_OPTIONS
                ],
                placeholder="Search for banner fonts...",
            )

        def _refresh_spinner(self) -> None:
            config = application.session.config
            style = resolve_spinner(config.terminal_spinner)
            self.query_one("#working-indicator", WorkingIndicator).set_style(style)
            self.query_one("#plan-body", PlanPanel).set_style(style)
            self.query_one("#subagents-body", SubagentPanel).set_style(style)
            self.query_one("#code-window", LiveCodePanel).set_style(style)

        def _persist_spinner(self, spinner: str) -> None:
            config = application.session.config
            if config.terminal_spinner == spinner:
                self._refresh_spinner()
                return
            try:
                validated = persist_config_updates(
                    config,
                    {"terminal_spinner": spinner},
                    explicit_path=config_path,
                )
            except Exception as exc:
                self._append_conversation_line(f"warning> Could not persist spinner: {exc}")
                return
            config.terminal_spinner = validated.terminal_spinner
            self._refresh_spinner()

        def action_change_spinner(self) -> None:
            self.search_commands(
                [
                    SimpleCommand(
                        style.label,
                        partial(self._persist_spinner, style.key),
                        f"Use the '{style.label}' working animation ({''.join(style.frames)}).",
                    )
                    for style in WORKING_SPINNERS.values()
                ],
                placeholder="Search for working spinners...",
            )

        async def _select_model_interactive(self) -> None:
            config = application.session.config
            self._append_conversation_line(f"command> Fetching models from {config.base_url} ...")
            try:
                models = await list_available_models(config)
            except Exception as exc:
                self._append_conversation_line(f"warning> Could not list models: {exc}")
                return
            current = config.model
            self.search_commands(
                [
                    SimpleCommand(
                        f"{model} (current)" if model == current else model,
                        partial(self._persist_model, model),
                        f"Switch the active model to {model}.",
                    )
                    for model in models
                ],
                placeholder="Search for a model...",
            )

        def _persist_model(self, model: str) -> None:
            config = application.session.config
            if config.model == model:
                self._append_conversation_line(f"command> Model already set to {model}.")
                return
            try:
                validated = persist_config_updates(
                    config,
                    {"model": model},
                    explicit_path=config_path,
                )
            except Exception as exc:
                self._append_conversation_line(f"warning> Could not switch model: {exc}")
                return
            config.model = validated.model
            self._append_conversation_line(f"command> Updated model={config.model}. Applied now.")
            self._refresh_status()

        def _persist_terminal_theme(self, theme) -> None:
            theme_name = getattr(theme, "name", self.theme)
            config = application.session.config
            if config.terminal_theme == theme_name:
                return
            try:
                validated = persist_config_updates(
                    config,
                    {"terminal_theme": theme_name},
                    explicit_path=config_path,
                )
            except Exception as exc:
                self._append_conversation_line(f"warning> Could not persist terminal theme: {exc}")
                return
            config.terminal_theme = validated.terminal_theme

        def _copy_selection(self) -> bool:
            """Copy any active drag-selection to the clipboard.

            Returns True when something was copied so callers can treat the
            keystroke as "copy" instead of its usual action.
            """
            try:
                selected = self.screen.get_selected_text()
            except Exception:
                selected = None
            # The prompt is a TextArea, which keeps its own mouse/keyboard
            # selection internally and never feeds the screen-level selection
            # that get_selected_text() reads. So a drag inside the input comes
            # back empty here — fall back to the focused input's own selection.
            if not selected:
                focused = self.focused
                if isinstance(focused, MultilineInput):
                    selected = focused.selected_text
            if not selected:
                return False
            # Textual's copy_to_clipboard relies on the terminal honouring an
            # OSC 52 escape sequence — macOS Terminal.app and several
            # multiplexers ignore it. Shell out to the OS clipboard tool too
            # so the copy actually lands, even when OSC 52 is a no-op.
            self.copy_to_clipboard(selected)
            copy_to_system_clipboard(selected)
            self.screen.clear_selection()
            self.notify("Texto copiado para a área de transferência.", timeout=2)
            return True

        def on_mouse_down(self, event) -> None:
            # Right-click copies the current selection, like a terminal's
            # context action. Left/middle clicks fall through untouched.
            if getattr(event, "button", 0) == 3 and self._copy_selection():
                event.stop()

        async def action_cancel_or_quit(self) -> None:
            # A drag-selection takes priority: Ctrl+C copies it (like a
            # terminal) and only cancels/quits when nothing is selected.
            if self._copy_selection():
                return
            if self.vm.status not in {"READY", "STARTING"}:
                await self.controller.cancel()
            else:
                await self.action_quit()

        async def action_clear(self) -> None:
            self.vm.conversation.clear()
            await self.query_one("#conversation", VerticalScroll).remove_children()
            self.query_one("#stream-tail", Static).update("")
            self._painted.pop("stream-tail", None)
            panel = self.query_one("#thinking-panel", ThinkingPanel)
            panel.display = False
            panel.reset()
            # The code window belongs to the transcript being wiped: a file
            # written before the clear is no longer on screen to explain it.
            self.vm.clear_code_stream()
            self._committed = 0
            # /clear wipes the transcript, not the live task: a checklist that
            # is still running (or paused waiting for the user) is runtime
            # state, and blind-hiding it lost sight of an in-flight task with
            # no event guaranteed to bring it back. Rebuild the panel from the
            # backend's authoritative snapshot; settled plans clear along with
            # the transcript they belong to.
            snapshot = self.controller.plan_snapshot()
            status = str(snapshot.get("status") or "")
            steps = (
                build_plan_steps(snapshot) if status in {"ACTIVE", "WAITING"} else []
            )
            self.vm.plan_steps = steps
            self.vm.plan_visible = bool(steps)
            if steps:
                self.vm.plan_progress = str(
                    snapshot.get("progress") or self.vm.plan_progress
                )
                self.vm.plan_status = status
            self._refresh_status()

        async def action_quit(self) -> None:
            # A second Ctrl+Q while armed bails out immediately, no matter what the
            # backend is doing — the graceful close keeps running in the background.
            if self._force_quit_armed:
                self.exit()
                return
            # First press: attempt a graceful shutdown, but never let it freeze the
            # terminal. If close() does not finish promptly (the usual cause is the
            # model hanging mid-request, which the cancel signal can't interrupt
            # until the HTTP call returns), arm force-quit and tell the user to
            # press again — mirroring how Claude Code handles a stuck exit.
            # Shield so a timeout leaves the close running in the background
            # instead of cancelling it half-way (which could leave the HTTP client
            # half-closed); keep a reference so the task isn't GC'd while pending.
            self._close_task = asyncio.ensure_future(application.close())
            try:
                await asyncio.wait_for(asyncio.shield(self._close_task), timeout=2.0)
            except TimeoutError:
                self._arm_force_quit()
                return
            except Exception:
                # A failed close still shouldn't trap the user in the terminal.
                self.exit()
                return
            self.exit()

        def _arm_force_quit(self) -> None:
            self._force_quit_armed = True
            self.notify(
                "Saída travada — o modelo não respondeu. "
                "Pressione Ctrl+Q novamente para forçar a saída.",
                severity="warning",
                timeout=5.0,
            )
            # Disarm after a short window so a much later Ctrl+Q starts over with a
            # fresh graceful-close attempt instead of force-quitting unexpectedly.
            self.set_timer(5.0, self._disarm_force_quit)

        def _disarm_force_quit(self) -> None:
            self._force_quit_armed = False

        def _changed(self, key: str, value: object) -> bool:
            """Whether ``key`` is showing something other than ``value`` now.

            Records ``value`` as painted when it differs, so the caller can skip
            the repaint entirely when it does not. Textual has no such guard of
            its own: ``Static.update`` rebuilds the visual and invalidates the
            layout whether or not the content actually changed.
            """
            if self._painted.get(key) == value:
                return False
            self._painted[key] = value
            return True

        def _refresh_status(self) -> None:
            status_line = (
                f"{self.vm.status} | {self.vm.phase} | {application.session.config.model} | "
                f"{application.session.config.workspace.name} | perm {self.vm.permission_mode} | "
                f"plan {self.vm.plan_progress}"
            )
            if self._changed("statusline", status_line):
                self.query_one("#statusline", Static).update(status_line)
            # Keyed on the numbers, not on the rendered meter: building the
            # renderable is the expensive half, so it is skipped too.
            meter = (self.vm.context_used, self.vm.context_budget, self.vm.context_threshold)
            if self._changed("context-meter", meter):
                self.query_one("#context-meter", Static).update(render_context_meter(*meter))
            session_text = self._session_text()
            if self._changed("session-info", session_text):
                self.query_one("#session-info", Static).update(session_text)
            working = self.vm.status in WORKING_STATES
            self.query_one("#working-indicator", WorkingIndicator).set_running(
                working, working_label(self.vm.status)
            )
            # The right sidebar shows only while there is a plan and/or live
            # sub-agents; each panel toggles on its own so one can show without
            # the other, and the column is reclaimed entirely when both are idle.
            self.query_one("#plan").display = self.vm.plan_visible
            self.query_one("#subagents").display = self.vm.subagents_visible
            self.query_one("#terminal").display = self.vm.terminal_visible
            self.query_one("#sidebar").display = (
                self.vm.plan_visible or self.vm.subagents_visible or self.vm.terminal_visible
            )
            if self.vm.terminal_visible:
                screen = (
                    self.vm.terminal_session_id,
                    self.vm.terminal_screen,
                    self.vm.terminal_rows,
                    self.vm.terminal_cols,
                    self.vm.terminal_closed,
                )
                if self._changed("terminal-body", screen):
                    self.query_one("#terminal-body", Static).update(
                        render_terminal_screen(*screen)
                    )
            self._refresh_code_window()
            self.query_one("#plan-body", PlanPanel).update_plan(
                self.vm.plan_steps, self.vm.plan_progress, self.vm.plan_status
            )
            self.query_one("#subagents-body", SubagentPanel).update_agents(self.vm.subagents_list())

        def _refresh_code_window(self) -> None:
            """Show (or hide) the file the model is writing right now."""
            window = self.query_one("#code-window", LiveCodePanel)
            visible = (
                self.vm.code_stream_visible
                and application.session.config.terminal_live_code
            )
            window.display = visible
            if visible:
                window.update_code(
                    self.vm.code_stream_tool,
                    self.vm.code_stream_path,
                    self.vm.code_stream_key,
                    # The model's own "why", shown on the same terms as in the
                    # approval dialog: only when /config learn is on.
                    self.vm.code_stream_reason
                    if application.session.config.learn
                    else "",
                    self.vm.code_stream_code,
                    self.vm.code_stream_complete,
                )

        def _session_text(self) -> str:
            config = application.session.config
            tools = ", ".join(application.orchestrator.tool_registry.names())
            return (
                f"status: {self.vm.status}\n"
                f"phase: {self.vm.phase}\n"
                f"workspace: {config.workspace}\n"
                f"provider: {config.base_url}\n"
                f"model: {config.model}\n"
                f"api mode: {config.api_mode}\n"
                f"permission: {self.vm.permission_mode}\n"
                f"planner: {self.vm.planner_mode}\n"
                f"plan progress: {self.vm.plan_progress}\n"
                f"current step: {self.vm.current_step}\n"
                f"verification: {self.vm.latest_verification_status}\n"
                f"usage: {self.vm.cumulative_usage}\n"
                f"state: {application.orchestrator.state.value}\n"
                f"tools: {tools}\n\n"
                "keys: Ctrl+C copiar seleção / cancelar | Ctrl+L limpar\n"
                "input: Enter envia · Shift+Enter/Ctrl+J nova linha\n"
                "colar: Ctrl+V texto ou imagem · Alt+V imagem quando o "
                "terminal fica com o Ctrl+V\n"
                "copiar: selecione com o mouse, botão direito ou Ctrl+C"
            )

    return CodeAITerminalApp()


def run_terminal_ui(
    *, config_path: Path | None = None, cli_overrides: dict[str, Any] | None = None
) -> int:
    try:
        import textual  # noqa: F401
    except Exception as exc:
        print(f"Textual UI is unavailable: {exc}")
        return 2

    application = build_application(config_path=config_path, cli_overrides=cli_overrides)
    create_terminal_app(application, config_path=config_path).run()
    return 0
