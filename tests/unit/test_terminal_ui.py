from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from textual.widgets import Input, Static

from code_ai.config.models import AppConfig
from code_ai.core.orchestration import TurnResult
from code_ai.core.state import AgentState
from code_ai.events.models import EventEnvelope
from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.slash_commands import handle_config_command, render_suggestions


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
