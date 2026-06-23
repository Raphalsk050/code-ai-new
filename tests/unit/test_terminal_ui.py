from __future__ import annotations

import inspect
import json
from importlib import resources
from types import SimpleNamespace

from textual.widgets import Input, Static

import code_ai.ui.terminal as terminal_package
from code_ai.config.models import AppConfig
from code_ai.core.orchestration import TurnResult
from code_ai.core.state import AgentState
from code_ai.events.models import EventEnvelope
from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.slash_commands import (
    command_completion,
    handle_config_command,
    render_suggestions,
)


class FakeTerminalApplication:
    def __init__(self, tmp_path) -> None:
        self.subscribers = []
        self.submitted: list[str] = []
        self.sequence = 0
        config = AppConfig.from_mapping(
            {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake-model"}
        )
        self.session = SimpleNamespace(config=config)
        self.orchestrator = SimpleNamespace(
            state=AgentState.READY,
            tool_registry=SimpleNamespace(names=lambda: ["read_file"]),
        )

    def subscribe(self, subscriber):
        self.subscribers.append(subscriber)
        return subscriber

    async def start(self) -> None:
        await self.emit("status.changed", {"state": "READY"})
        await self.emit("phase.changed", {"phase": "waiting_user"})

    async def submit_user_message(self, text: str) -> TurnResult:
        self.submitted.append(text)
        await self.emit("user.message", {"text": text})
        await self.emit("model.stream.delta", {"text": "ok"})
        return TurnResult(text="ok", response=None)

    async def request_context_compression(self) -> None:
        return None

    async def cancel_current_turn(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def emit(self, event_type: str, payload: dict[str, object]) -> None:
        self.sequence += 1
        event = EventEnvelope.create(
            event_type=event_type,
            session_id="fake-session",
            sequence=self.sequence,
            payload=payload,
            source="test",
        )
        for subscriber in list(self.subscribers):
            result = subscriber(event)
            if inspect.isawaitable(result):
                await result


async def test_terminal_enter_submits_input_and_renders_events(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", Input)
        input_widget.value = "hello from tui"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert fake_app.submitted == ["hello from tui"]
        assert "you> hello from tui" in terminal_app.vm.conversation
        assert "ai> ok" in terminal_app.vm.conversation


async def test_streaming_deltas_do_not_rerender_whole_transcript(tmp_path) -> None:
    from textual.widgets import RichLog

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        log = terminal_app.query_one("#conversation", RichLog)
        writes = 0
        original_write = log.write

        def counting_write(*args, **kwargs):
            nonlocal writes
            writes += 1
            return original_write(*args, **kwargs)

        log.write = counting_write  # type: ignore[method-assign]

        # The agent is working (the model is streaming), as in a real turn.
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        # Two finalized lines followed by 50 streaming deltas on one answer line.
        await fake_app.emit("user.message", {"text": "hi"})
        await fake_app.emit("model.request.started", {})
        for index in range(50):
            await fake_app.emit("model.stream.delta", {"text": f"{index} "})
        await pilot.pause(0.05)

        # Only the two finalized lines were ever written to the append-only log;
        # the 50 deltas mutate a single line shown live in the tail, not the log.
        assert writes == 2
        assert terminal_app._committed == len(terminal_app.vm.conversation) - 1
        # The whole stream collapsed into one live line held in the tail Static.
        assert terminal_app.vm.conversation[-1].startswith("ai> ")
        assert "49" in terminal_app.vm.conversation[-1]

        # When the turn finishes the streamed answer must flow into the log
        # instead of being stranded in the live tail below it.
        await fake_app.emit("status.changed", {"state": "READY"})
        await pilot.pause(0.05)
        assert writes == 3  # the final answer line was committed exactly once
        assert terminal_app._committed == len(terminal_app.vm.conversation)


def test_stream_tail_keeps_newest_rows_of_a_long_streaming_line() -> None:
    from code_ai.ui.terminal.app import render_stream_tail

    line = "ai> " + "\n".join(f"row {i}" for i in range(40))
    rendered = render_stream_tail(line)
    text = rendered if isinstance(rendered, str) else rendered.plain

    # The newest rows survive; the oldest are dropped so nothing is clipped.
    assert "row 39" in text
    assert "row 0\n" not in text
    assert text.count("\n") <= 14


def test_stream_tail_passes_short_lines_through_unchanged() -> None:
    from code_ai.ui.terminal.app import render_stream_tail

    assert render_stream_tail("ai> hello") == "ai> hello"


async def test_new_turn_start_does_not_rerender_committed_log(tmp_path) -> None:
    from textual.widgets import RichLog

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        # Finish a turn: a couple of lines settle into the log while idle.
        await fake_app.emit("status.changed", {"state": "READY"})
        await fake_app.emit("user.message", {"text": "hi"})
        await fake_app.emit("assistant.final", {"text": "done"})
        await pilot.pause(0.05)
        committed_before = terminal_app._committed
        assert committed_before == len(terminal_app.vm.conversation)

        log = terminal_app.query_one("#conversation", RichLog)
        writes = 0
        original_write = log.write

        def counting_write(*args, **kwargs):
            nonlocal writes
            writes += 1
            return original_write(*args, **kwargs)

        log.write = counting_write  # type: ignore[method-assign]

        # The next turn flips status to working *before* its first line arrives.
        # The already-committed answer must not be pulled back or re-rendered.
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await pilot.pause(0.05)

        assert writes == 0
        assert terminal_app._committed == committed_before


async def test_clear_resets_incremental_render_state(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await fake_app.emit("user.message", {"text": "one"})
        await fake_app.emit("tool.call.started", {"name": "read_file"})
        await pilot.pause(0.05)
        assert terminal_app._committed > 0

        await terminal_app.action_clear()
        assert terminal_app._committed == 0
        assert terminal_app.vm.conversation == []


def test_terminal_logo_loads_from_banner_resource() -> None:
    from code_ai.ui.terminal.widgets import (
        BANNER_RESOURCE,
        CODE_AI_LOGO_FONT,
        load_banner_source,
        load_code_ai_logo,
    )

    banner = resources.files(terminal_package).joinpath(BANNER_RESOURCE).read_text(
        encoding="utf-8"
    )
    rendered = load_code_ai_logo()

    assert banner.strip()
    assert load_banner_source() == "code.ai"
    assert rendered.plain
    assert "█▀▀" in rendered.plain
    assert "█▄▄" in rendered.plain
    assert CODE_AI_LOGO_FONT == "tarty2"


def test_terminal_logo_styles_tarty2_banner_lines() -> None:
    from code_ai.ui.terminal.widgets import (
        CODE_AI_BANNER_FONT_OPTIONS,
        CODE_AI_LOGO_STYLES,
        load_code_ai_logo,
    )

    rendered = load_code_ai_logo()
    span_styles = [str(span.style) for span in rendered.spans]

    assert span_styles[:2] == [CODE_AI_LOGO_STYLES[1], CODE_AI_LOGO_STYLES[0]]
    assert "future_1" in CODE_AI_BANNER_FONT_OPTIONS
    assert "xsansi" in CODE_AI_BANNER_FONT_OPTIONS


def test_terminal_styles_thinking_lines_darker_gray() -> None:
    from rich.text import Text

    from code_ai.ui.terminal.widgets import THINKING_LINE_STYLE, render_conversation_line

    thinking = render_conversation_line("thinking> checking files")
    activity = render_conversation_line("model> thinking step 0...")
    answer = render_conversation_line("ai> done")

    assert isinstance(thinking, Text)
    assert isinstance(activity, Text)
    assert str(thinking.style) == THINKING_LINE_STYLE
    assert str(activity.style) == THINKING_LINE_STYLE
    assert answer == "ai> done"


def test_view_model_replaces_recovered_tool_call_text() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    # The raw tool-call markup streamed into the chat as an ai> line.
    view_model.conversation.append('ai> Sure. <tool_call>{"name": "read_file"}</tool_call>')
    event = EventEnvelope.create(
        event_type="tool.calls.recovered",
        session_id="fake-session",
        sequence=1,
        payload={"count": 1, "names": ["read_file"], "text": "Sure."},
        source="core.orchestrator",
    )

    view_model.apply(event)

    assert view_model.conversation[-1] == "ai> Sure."


def test_view_model_drops_pure_tool_call_text_on_recovery() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    view_model.conversation.append("you> read it")
    view_model.conversation.append('ai> <tool_call>{"name": "read_file"}</tool_call>')
    event = EventEnvelope.create(
        event_type="tool.calls.recovered",
        session_id="fake-session",
        sequence=1,
        payload={"count": 1, "names": ["read_file"], "text": ""},
        source="core.orchestrator",
    )

    view_model.apply(event)

    assert view_model.conversation == ["you> read it"]


def test_terminal_view_model_shows_command_output() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    event = EventEnvelope.create(
        event_type="tool.call.completed",
        session_id="fake-session",
        sequence=1,
        payload={
            "name": "execute_command",
            "result": {"stdout": "/Users/rafaelmorais/_PROJECTS/python_agent\n"},
        },
        source="test",
    )
    view_model.apply(event)
    assert "python_agent" in view_model.conversation[-1]


def test_terminal_view_model_shows_web_search_results() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    event = EventEnvelope.create(
        event_type="tool.call.completed",
        session_id="fake-session",
        sequence=1,
        payload={
            "name": "web_search",
            "result": {
                "query": "world cup schedule today",
                "results": [
                    {
                        "title": "FIFA World Cup schedule",
                        "url": "https://www.fifa.com/en/tournaments/mens/worldcup",
                    }
                ],
            },
        },
        source="test",
    )
    view_model.apply(event)
    assert "1 result" in view_model.conversation[-1]
    assert "FIFA World Cup schedule" in view_model.conversation[-1]


def test_terminal_view_model_shows_tool_failures() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    event = EventEnvelope.create(
        event_type="tool.call.failed",
        session_id="fake-session",
        sequence=1,
        payload={
            "name": "web_search",
            "message": "No web search provider returned usable results.",
        },
        source="test",
    )
    view_model.apply(event)
    assert "tool> web_search failed" in view_model.conversation[-1]


def test_terminal_view_model_shows_model_activity_and_public_thinking() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    started = EventEnvelope.create(
        event_type="model.request.started",
        session_id="fake-session",
        sequence=1,
        payload={"step": 0},
        source="test",
    )
    thinking = EventEnvelope.create(
        event_type="model.thinking.delta",
        session_id="fake-session",
        sequence=2,
        payload={"text": "checking files"},
        source="test",
    )
    view_model.apply(started)
    view_model.apply(thinking)
    assert "model> thinking step 0..." in view_model.conversation
    assert "thinking> checking files" in view_model.conversation


async def test_terminal_shows_slash_command_suggestions(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)):
        input_widget = terminal_app.query_one("#input", Input)
        input_widget.value = "/config"
        terminal_app._set_command_suggestions(input_widget.value)
        suggestions = terminal_app.query_one("#command-suggestions", Static)
        assert suggestions.display is True


def test_render_suggestions_lists_config_commands() -> None:
    rendered = render_suggestions("/config")
    assert "/config show" in rendered
    assert "/config api-mode" in rendered


def test_command_completion_completes_config_command_text() -> None:
    assert command_completion("/config api-m") == "/config api-mode "
    assert command_completion("/config api-k") == "/config api-key "
    assert command_completion("/config api-mode o") == "/config api-mode ollama"
    assert command_completion("/config th") == "/config theme "
    assert command_completion("/config theme tok") == "/config theme tokyo-night"
    assert command_completion("/config banner-font fut") == "/config banner-font future_1"


async def test_left_arrow_accepts_slash_command_completion(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", Input)
        input_widget.value = "/config api-m"
        input_widget.cursor_position = len(input_widget.value)
        await pilot.press("left")
        await pilot.pause(0.2)

        assert input_widget.value == "/config api-mode "
        assert input_widget.cursor_position == len(input_widget.value)


def test_config_model_command_persists_and_updates_active_config(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config model other-model",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Applied now" in result
    assert fake_app.session.config.model == "other-model"
    assert saved["model"] == "other-model"


def test_config_api_key_command_persists_and_redacts(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config api-key sk-secret-123",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    # The key is persisted but never echoed back to the conversation log.
    assert "sk-secret-123" not in result
    assert "<redacted>" in result
    assert saved["api_key"] == "sk-secret-123"
    assert fake_app.session.config.api_key == "sk-secret-123"


def test_config_theme_command_persists_and_updates_active_config(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config theme tokyo-night",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Applied now" in result
    assert fake_app.session.config.terminal_theme == "tokyo-night"
    assert saved["terminal_theme"] == "tokyo-night"


def test_config_banner_font_command_persists_and_updates_active_config(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config banner-font future_2",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Applied now" in result
    assert fake_app.session.config.terminal_banner_font == "future_2"
    assert saved["terminal_banner_font"] == "future_2"


async def test_terminal_persists_theme_changed_from_palette(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.session.config.terminal_theme = "textual-light"
    config_path = tmp_path / "config.json"
    terminal_app = create_terminal_app(fake_app, config_path=config_path)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        assert terminal_app.theme == "textual-light"

        terminal_app.theme = "textual-dark"
        await pilot.pause(0.2)

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert fake_app.session.config.terminal_theme == "textual-dark"
        assert saved["terminal_theme"] == "textual-dark"


async def test_terminal_command_palette_exposes_banner_font_command(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(110, 44)) as pilot:
        commands = list(terminal_app.get_system_commands(terminal_app.screen))
        await pilot.pause(0.1)

    assert any(command.title == "Banner Font" for command in commands)


async def test_terminal_banner_font_command_updates_logo_and_persists(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    terminal_app = create_terminal_app(fake_app, config_path=config_path)

    async with terminal_app.run_test(size=(110, 44)) as pilot:
        logo = terminal_app.query_one("#logo", Static)

        before = str(logo.render())
        terminal_app._persist_banner_font("future_1")
        await pilot.pause(0.2)

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert fake_app.session.config.terminal_banner_font == "future_1"
        assert saved["terminal_banner_font"] == "future_1"
        assert str(logo.render()) != before


def test_config_effort_command_persists_and_updates_active_config(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config effort high",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Applied now" in result
    assert fake_app.session.config.sampling.reasoning_effort == "high"
    assert saved["sampling"]["reasoning_effort"] == "high"


def test_config_effort_command_rejects_unknown_value(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config effort turbo",
        config_path=config_path,
    )
    assert "Unsupported reasoning effort" in result
    assert fake_app.session.config.sampling.reasoning_effort is None
    assert not config_path.exists()


def test_config_effort_completes_and_suggests_values() -> None:
    assert command_completion("/config eff") == "/config effort "
    assert command_completion("/config effort hi") == "/config effort high"
    rendered = render_suggestions("/config effort ")
    assert "/config effort minimal" in rendered
    assert "/config effort xhigh" in rendered


async def test_up_arrow_recalls_previous_submitted_entries(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", Input)

        input_widget.value = "first message"
        await pilot.press("enter")
        input_widget.value = "second message"
        await pilot.press("enter")
        await pilot.pause(0.1)

        # Up walks back from newest to oldest.
        await pilot.press("up")
        assert input_widget.value == "second message"
        await pilot.press("up")
        assert input_widget.value == "first message"
        # Already at the oldest entry — Up holds position.
        await pilot.press("up")
        assert input_widget.value == "first message"

        # Down walks forward and restores the empty draft past the newest entry.
        await pilot.press("down")
        assert input_widget.value == "second message"
        await pilot.press("down")
        assert input_widget.value == ""


async def test_history_preserves_unsent_draft_and_skips_duplicates(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", Input)

        input_widget.value = "ls"
        await pilot.press("enter")
        # Submitting the same entry twice must not create a duplicate slot.
        input_widget.value = "ls"
        await pilot.press("enter")
        await pilot.pause(0.1)

        # Start typing a fresh draft, then browse history and come back.
        input_widget.value = "draft in progress"
        await pilot.press("up")
        assert input_widget.value == "ls"
        # A single Up reached the oldest, proving the duplicate was collapsed.
        await pilot.press("down")
        assert input_widget.value == "draft in progress"


def test_config_help_command_lists_config_commands_as_text(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    result = handle_config_command(fake_app, "/config help", config_path=None)
    # Lists the other config commands but does not list a way back into itself.
    assert "/config show" in result
    assert "/config effort" in result
    assert "/config model " in result
    assert "/config help" not in result.replace("run /config help", "")


async def test_config_help_picker_prefills_arg_commands(tmp_path) -> None:
    from code_ai.ui.terminal.slash_commands import SlashCommand

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(110, 44)) as pilot:
        input_widget = terminal_app.query_one("#input", Input)

        # A command that needs a value drops its stem into the prompt for editing.
        terminal_app._use_config_command(
            SlashCommand("/config model <name>", "x", "/config model ")
        )
        await pilot.pause(0.1)
        assert input_widget.value == "/config model "
        assert input_widget.cursor_position == len(input_widget.value)


async def test_config_help_picker_runs_argless_commands(tmp_path) -> None:
    from code_ai.ui.terminal.slash_commands import SlashCommand

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(110, 44)) as pilot:
        input_widget = terminal_app.query_one("#input", Input)

        # An argument-free command runs immediately instead of pre-filling.
        terminal_app._use_config_command(SlashCommand("/config show", "x"))
        await pilot.pause(0.1)
        assert input_widget.value == ""
        # /config show appends the redacted config to the conversation.
        assert any('"model"' in line for line in terminal_app.vm.conversation)
