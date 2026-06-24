from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any

from rich.text import Text

from code_ai.bootstrap import build_application
from code_ai.config.loader import persist_config_updates
from code_ai.providers.model_listing import list_available_models
from code_ai.ui.terminal.controller import TerminalController
from code_ai.ui.terminal.slash_commands import (
    command_completion,
    config_commands,
    handle_config_command,
    handle_debug_command,
    render_suggestions,
)
from code_ai.ui.terminal.view_models import TerminalViewModel
from code_ai.ui.terminal.widgets import (
    CODE_AI_BANNER_FONT_OPTIONS,
    WORKING_BASE_COLOR,
    WORKING_IDLE_STYLE,
    WORKING_LABEL_STYLE,
    WORKING_PULSE_PERIOD,
    WORKING_SPINNERS,
    WORKING_STATES,
    SpinnerStyle,
    load_code_ai_logo,
    normalize_banner_font,
    render_context_meter,
    render_conversation_line,
    render_plan,
    resolve_spinner,
    spinner_color,
    working_label,
)

# Labels shown in the permission-mode dropdown next to the input, mapped to the
# config values consumed by AppConfig.permission_mode.
_PERMISSION_MODE_OPTIONS = [
    ("perm: solicitar", "ask"),
    ("perm: automático", "auto"),
    ("perm: ignorar", "bypass"),
]

# How many rows of the live streaming line the (non-scrolling) tail strip shows.
_STREAM_TAIL_MAX_ROWS = 14


def render_stream_tail(line: str):
    """Render the live streaming line, keeping its newest rows visible.

    The tail Static cannot scroll, so a streaming answer taller than the strip
    would otherwise clip its newest (bottom) text off-screen and look frozen.
    Show only the final rows while it streams; the complete text lands in the
    scrollable log once the turn finishes.
    """
    rows = line.split("\n")
    if len(rows) > _STREAM_TAIL_MAX_ROWS:
        line = "\n".join(rows[-_STREAM_TAIL_MAX_ROWS:])
    return render_conversation_line(line)


def create_terminal_app(application, *, config_path: Path | None = None):
    from textual.app import App, ComposeResult, SystemCommand
    from textual.command import SimpleCommand
    from textual.containers import Container, Horizontal, Vertical
    from textual.suggester import Suggester
    from textual.widgets import Button, Footer, Header, Input, RichLog, Select, Static

    from code_ai.ui.terminal.approval import TerminalApprovalGateway

    class SlashCommandSuggester(Suggester):
        async def get_suggestion(self, value: str) -> str | None:
            return command_completion(value)

    class CommandInput(Input):
        """Single-line prompt with shell-style history recall.

        Submitted prompts (commands and messages alike) are pushed onto a
        history stack; Up walks back through older entries and Down walks
        forward, restoring the in-progress draft once you step past the newest
        entry — exactly how a terminal behaves.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._history: list[str] = []
            # None means "not browsing"; the live draft is whatever is typed.
            self._history_index: int | None = None
            self._draft = ""

        def action_cursor_left(self, select: bool = False) -> None:
            completion = command_completion(self.value)
            if not select and self.cursor_at_end and completion:
                self.value = completion
                self.cursor_position = len(completion)
                return
            super().action_cursor_left(select)

        def remember(self, text: str) -> None:
            """Record a submitted entry and reset the browse cursor."""
            entry = text.rstrip("\n")
            # Skip blanks and consecutive duplicates, like a shell history.
            if entry.strip() and (not self._history or self._history[-1] != entry):
                self._history.append(entry)
            self._history_index = None
            self._draft = ""

        def _recall(self, value: str) -> None:
            self.value = value
            self.cursor_position = len(value)

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

        async def on_key(self, event) -> None:
            if event.key == "up" and self._history_prev():
                event.prevent_default()
                event.stop()
            elif event.key == "down" and self._history_next():
                event.prevent_default()
                event.stop()

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
            self._frame = 0
            self._running = False
            self._timer = None

        def on_mount(self) -> None:
            self._timer = self.set_interval(self.TICK, self._tick, pause=True)
            self._paint()

        def set_style(self, style: SpinnerStyle) -> None:
            self._style = style
            self._paint()

        def update_plan(
            self, steps: list[dict[str, str]], progress: str, status: str
        ) -> None:
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
            self.update(
                render_plan(self._steps, self._progress, self._status, glyph, color)
            )

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

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="root"):
                with Container(id="topbar"):
                    yield Static(
                        load_code_ai_logo(application.session.config.terminal_banner_font),
                        id="logo",
                    )
                    yield Static("Any model. Real tools. Local control.", id="subtitle")
                    yield Static("READY", id="statusline")
                    yield Static("", id="context-meter")
                with Horizontal(id="main"):
                    with Vertical(id="session"):
                        with Horizontal(id="session-header"):
                            yield Static("SESSION", classes="panel-title", id="session-title")
                            yield Button("‹", id="toggle-session", classes="collapse-btn")
                        yield Static("", id="session-info")
                    with Vertical(id="conversation-pane"):
                        yield RichLog(
                            id="conversation",
                            wrap=True,
                            highlight=False,
                            markup=False,
                            max_lines=2000,
                        )
                        yield Static("", id="stream-tail")
                    with Vertical(id="plan"):
                        yield Static("PLAN", classes="panel-title")
                        yield PlanPanel(
                            resolve_spinner(application.session.config.terminal_spinner),
                            id="plan-body",
                        )
                yield WorkingIndicator(
                    resolve_spinner(application.session.config.terminal_spinner),
                    id="working-indicator",
                )
                suggestions = Static("", id="command-suggestions")
                suggestions.display = False
                yield suggestions
                with Horizontal(id="input-row"):
                    yield Select(
                        _PERMISSION_MODE_OPTIONS,
                        value=application.session.config.permission_mode,
                        allow_blank=False,
                        id="mode-select",
                    )
                    yield CommandInput(
                        placeholder="you>",
                        id="input",
                        suggester=SlashCommandSuggester(case_sensitive=True),
                    )
            yield Footer()

        async def on_mount(self) -> None:
            application.subscribe(self._on_event)
            # Route gated tool calls through an interactive approve/deny modal.
            application.orchestrator.approval_gateway = TerminalApprovalGateway(
                self, application.session.config
            )
            self._apply_configured_terminal_theme()
            self._apply_session_collapsed(
                application.session.config.terminal_session_collapsed
            )
            self.theme_changed_signal.subscribe(self, self._persist_terminal_theme)
            await application.start()
            self._refresh_status()
            self.query_one("#input", Input).focus()

        async def _on_event(self, event) -> None:
            await self.controller.handle_event(event)
            self._render_event(event)

        def _render_event(self, event) -> None:
            self._sync_conversation()
            self._refresh_status()

        def _sync_conversation(self) -> None:
            """Incrementally reflect the conversation buffer into the UI.

            The RichLog is append-only: each line is written exactly once, so
            the cost per event is proportional to the number of *new* lines, not
            the whole transcript.

            While the agent is working the last line may still be growing from
            streaming deltas, so it is held live in the ``#stream-tail`` Static
            and kept out of the log (re-rendering a growing line every delta is
            what used to freeze the terminal). As soon as the agent goes idle
            that line is final: it is committed to the log and the tail cleared,
            so a finished answer lands in the conversation instead of being
            stranded in the strip below it.
            """
            convo = self.vm.conversation
            log = self.query_one("#conversation", RichLog)
            tail = self.query_one("#stream-tail", Static)

            if self._committed > len(convo):
                # The buffer shrank (only happens on clear, which also resets the
                # counter): rebuild defensively so the log never shows dead lines.
                log.clear()
                self._committed = 0

            # Hold the last line back in the live tail only while the agent is
            # working AND that line is still uncommitted (freshly streaming).
            # Lines already in the append-only log are never pulled back, so a
            # new turn starting (status flips to working before its first line
            # arrives) cannot trigger a flicker or a full re-render.
            working = self.vm.status in WORKING_STATES
            held_back = working and self._committed < len(convo)
            commit_upto = len(convo) - 1 if held_back else len(convo)

            while self._committed < commit_upto:
                log.write(render_conversation_line(convo[self._committed]))
                self._committed += 1

            tail.update(render_stream_tail(convo[-1]) if held_back else "")

        async def on_input_changed(self, event: Input.Changed) -> None:
            self._set_command_suggestions(event.value)

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value
            if isinstance(event.input, CommandInput):
                event.input.remember(text)
            event.input.value = ""
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
                await self.controller.set_planner_mode(mode)
                self._append_conversation_line(f"command> Planner mode set to {mode}")
                return
            if text.strip() == "/mode" or text.strip().startswith("/mode "):
                await self._handle_mode_command(text.strip())
                return
            if text.strip() == "/deep-plan":
                self._append_conversation_line(await self.controller.deep_plan())
                return
            if text.strip() == "/plan-status":
                self._append_conversation_line(self.controller.plan_status())
                return
            if text.strip().startswith("/replan"):
                reason = text.strip()[len("/replan") :].strip() or None
                self._append_conversation_line(await self.controller.replan(reason))
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
                self.query_one("#conversation", RichLog).write(self._session_text())
                return
            if text.strip() == "/help":
                self._append_conversation_line(render_suggestions("/"))
                return
            if text.strip().startswith("/config"):
                await self._dispatch_config(text.strip())
                return
            asyncio.create_task(self.controller.submit(text))

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
            input_widget = self.query_one("#input", CommandInput)
            if "<" in item.command:
                # Needs a value: drop the command stem into the prompt (cursor at
                # the end) so the user types the argument and presses Enter.
                input_widget.value = item.completion_text
                input_widget.cursor_position = len(input_widget.value)
                input_widget.focus()
                self._set_command_suggestions(input_widget.value)
                return
            # No argument to fill — run it right away.
            self._set_command_suggestions("")
            asyncio.create_task(self._dispatch_config(item.completion_text.strip()))

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
            rendered = render_suggestions(text)
            suggestions.update(rendered)
            suggestions.display = bool(rendered)

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
                self._append_conversation_line(
                    f"warning> Could not persist banner font: {exc}"
                )
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
                self._append_conversation_line(
                    f"warning> Could not persist spinner: {exc}"
                )
                return
            config.terminal_spinner = validated.terminal_spinner
            self._refresh_spinner()

        def action_change_spinner(self) -> None:
            self.search_commands(
                [
                    SimpleCommand(
                        style.label,
                        partial(self._persist_spinner, style.key),
                        f"Use the '{style.label}' working animation ("
                        f"{''.join(style.frames)}).",
                    )
                    for style in WORKING_SPINNERS.values()
                ],
                placeholder="Search for working spinners...",
            )

        async def _select_model_interactive(self) -> None:
            config = application.session.config
            self._append_conversation_line(
                f"command> Fetching models from {config.base_url} ..."
            )
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
                self._append_conversation_line(
                    f"warning> Could not persist terminal theme: {exc}"
                )
                return
            config.terminal_theme = validated.terminal_theme

        async def action_cancel_or_quit(self) -> None:
            if self.vm.status not in {"READY", "STARTING"}:
                await self.controller.cancel()
            else:
                await self.action_quit()

        async def action_clear(self) -> None:
            self.vm.conversation.clear()
            self.vm.plan_visible = False
            self.vm.plan_steps = []
            self.query_one("#conversation", RichLog).clear()
            self.query_one("#stream-tail", Static).update("")
            self._committed = 0
            self._refresh_status()

        async def action_quit(self) -> None:
            await application.close()
            self.exit()

        def _refresh_status(self) -> None:
            self.query_one("#statusline", Static).update(
                f"{self.vm.status} | {self.vm.phase} | {application.session.config.model} | "
                f"{application.session.config.workspace.name} | perm {self.vm.permission_mode} | "
                f"plan {self.vm.plan_progress}"
            )
            self.query_one("#context-meter", Static).update(
                render_context_meter(
                    self.vm.context_used,
                    self.vm.context_budget,
                    self.vm.context_threshold,
                )
            )
            self.query_one("#session-info", Static).update(self._session_text())
            working = self.vm.status in WORKING_STATES
            self.query_one("#working-indicator", WorkingIndicator).set_running(
                working, working_label(self.vm.status)
            )
            self.query_one("#plan").display = self.vm.plan_visible
            self.query_one("#plan-body", PlanPanel).update_plan(
                self.vm.plan_steps, self.vm.plan_progress, self.vm.plan_status
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
                "keys: Ctrl+C cancel/quit | Ctrl+L clear"
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
