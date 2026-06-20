from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import Any

from code_ai.bootstrap import build_application
from code_ai.config.loader import persist_config_updates
from code_ai.ui.terminal.controller import TerminalController
from code_ai.ui.terminal.slash_commands import (
    command_completion,
    handle_config_command,
    render_suggestions,
)
from code_ai.ui.terminal.view_models import TerminalViewModel
from code_ai.ui.terminal.widgets import (
    CODE_AI_BANNER_FONT_OPTIONS,
    load_code_ai_logo,
    normalize_banner_font,
    render_conversation_line,
)


def create_terminal_app(application, *, config_path: Path | None = None):
    from textual.app import App, ComposeResult, SystemCommand
    from textual.command import SimpleCommand
    from textual.containers import Container, Horizontal, Vertical
    from textual.suggester import Suggester
    from textual.widgets import Footer, Header, Input, RichLog, Static

    class SlashCommandSuggester(Suggester):
        async def get_suggestion(self, value: str) -> str | None:
            return command_completion(value)

    class CommandInput(Input):
        def action_cursor_left(self, select: bool = False) -> None:
            completion = command_completion(self.value)
            if not select and self.cursor_at_end and completion:
                self.value = completion
                self.cursor_position = len(completion)
                return
            super().action_cursor_left(select)

    class CodeAITerminalApp(App[None]):
        CSS_PATH = "theme.tcss"
        BINDINGS = [
            ("ctrl+c", "cancel_or_quit", "Cancel/Quit"),
            ("ctrl+q", "quit", "Quit"),
            ("ctrl+l", "clear", "Clear"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.vm = TerminalViewModel()
            self.controller = TerminalController(application, self.vm)
            self.follow_output = True

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
                with Horizontal(id="main"):
                    with Vertical(id="session"):
                        yield Static("SESSION", classes="panel-title")
                        yield Static("", id="session-info")
                    yield RichLog(id="conversation", wrap=True, highlight=False, markup=False)
                suggestions = Static("", id="command-suggestions")
                suggestions.display = False
                yield suggestions
                yield CommandInput(
                    placeholder="you>",
                    id="input",
                    suggester=SlashCommandSuggester(case_sensitive=True),
                )
            yield Footer()

        async def on_mount(self) -> None:
            application.subscribe(self._on_event)
            self._apply_configured_terminal_theme()
            self.theme_changed_signal.subscribe(self, self._persist_terminal_theme)
            await application.start()
            self._refresh_status()
            self.query_one("#input", Input).focus()

        async def _on_event(self, event) -> None:
            await self.controller.handle_event(event)
            self._render_event(event)

        def _render_event(self, event) -> None:
            conversation = self.query_one("#conversation", RichLog)
            if event.event_type in {
                "user.message",
                "model.request.started",
                "model.thinking.delta",
                "model.stream.delta",
                "model.response.completed",
                "tool.call.started",
                "tool.call.completed",
                "tool.call.failed",
                "planning.plan.created",
                "planning.plan.revised",
                "planning.step.started",
                "planning.step.completed",
                "planning.step.failed",
                "planning.evidence.recorded",
                "planning.policy.denied",
                "planning.completion.rejected",
                "assistant.final",
                "warning",
                "error",
            }:
                conversation.clear()
                for line in self.vm.conversation[-300:]:
                    conversation.write(render_conversation_line(line))
            self._refresh_status()

        async def on_input_changed(self, event: Input.Changed) -> None:
            self._set_command_suggestions(event.value)

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value
            event.input.value = ""
            self._set_command_suggestions("")
            if text.strip() == "/quit":
                await self.action_quit()
                return
            if text.strip() == "/clear":
                await self.action_clear()
                return
            if text.strip() == "/compact":
                await self.controller.compact()
                return
            if text.strip() in {"/auto", "/plan", "/act"}:
                mode = text.strip().lstrip("/")
                await self.controller.set_planner_mode(mode)
                self._append_conversation_line(f"command> Planner mode set to {mode}")
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
            if text.strip() == "/status":
                self.query_one("#conversation", RichLog).write(self._session_text())
                return
            if text.strip() == "/help":
                self._append_conversation_line(render_suggestions("/"))
                return
            if text.strip().startswith("/config"):
                stripped = text.strip()
                self._append_conversation_line(
                    handle_config_command(application, stripped, config_path=config_path)
                )
                if stripped.startswith("/config theme "):
                    self._apply_configured_terminal_theme()
                if stripped.startswith("/config banner-font "):
                    self._refresh_logo()
                return
            asyncio.create_task(self.controller.submit(text))

        def get_system_commands(self, screen) -> Any:
            yield from super().get_system_commands(screen)
            yield SystemCommand(
                "Banner Font",
                "Change the banner art font",
                self.action_change_banner_font,
            )

        def _set_command_suggestions(self, text: str) -> None:
            suggestions = self.query_one("#command-suggestions", Static)
            rendered = render_suggestions(text)
            suggestions.update(rendered)
            suggestions.display = bool(rendered)

        def _append_conversation_line(self, text: str) -> None:
            if not text:
                return
            self.vm.conversation.extend(text.splitlines())
            conversation = self.query_one("#conversation", RichLog)
            conversation.clear()
            for line in self.vm.conversation[-300:]:
                conversation.write(render_conversation_line(line))
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
            self.query_one("#conversation", RichLog).clear()

        async def action_quit(self) -> None:
            await application.close()
            self.exit()

        def _refresh_status(self) -> None:
            self.query_one("#statusline", Static).update(
                f"{self.vm.status} | {self.vm.phase} | {application.session.config.model} | "
                f"{application.session.config.workspace.name} | plan {self.vm.plan_progress} | "
                f"ctx {self.vm.active_context_tokens}"
            )
            self.query_one("#session-info", Static).update(self._session_text())

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
