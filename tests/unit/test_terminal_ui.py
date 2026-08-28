from __future__ import annotations

import inspect
import json
from importlib import resources
from types import SimpleNamespace

from textual.widgets import Static, TextArea
from textual.widgets.text_area import Selection

import code_ai.ui.terminal as terminal_package
from code_ai.config.models import AppConfig
from code_ai.context.compression import CompressionResult
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
        self.submitted_images: list[list[object]] = []
        self.sequence = 0
        self.plan_snapshot: dict[str, object] = {}
        config = AppConfig.from_mapping(
            {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake-model"}
        )
        self.session = SimpleNamespace(config=config)
        self.orchestrator = SimpleNamespace(
            state=AgentState.READY,
            tool_registry=SimpleNamespace(names=lambda: ["read_file"]),
        )
        # /term drives the shared PTY through the application's manager and
        # event bus; tests plug a fake manager in when they exercise it.
        self.terminal_manager = None
        # Saved workflows the TUI turns into slash commands; tests that exercise
        # them assign a real WorkflowService before creating the app.
        self.workflows = None
        # Skill directories the TUI turns into slash commands; tests that
        # exercise them assign real sources before creating the app.
        self.skill_sources = ()
        self.event_bus = SimpleNamespace(emit=self._emit_bus)

    async def _emit_bus(self, event_type: str, payload: dict[str, object], source=None) -> None:
        await self.emit(event_type, payload)

    def get_plan_snapshot(self) -> dict[str, object]:
        return self.plan_snapshot

    def subscribe(self, subscriber):
        self.subscribers.append(subscriber)
        return subscriber

    async def start(self) -> None:
        await self.emit("status.changed", {"state": "READY"})
        await self.emit("phase.changed", {"phase": "waiting_user"})

    async def submit_user_message(self, text: str, *, images=None) -> TurnResult:
        self.submitted.append(text)
        self.submitted_images.append(list(images or []))
        await self.emit("user.message", {"text": text})
        await self.emit("model.stream.delta", {"text": "ok"})
        return TurnResult(text="ok", response=None)

    async def request_context_compression(self) -> CompressionResult:
        return CompressionResult(True, 4000, False, 18000)

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
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "hello from tui"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert fake_app.submitted == ["hello from tui"]
        assert "you> hello from tui" in terminal_app.vm.conversation
        assert "ai> ok" in terminal_app.vm.conversation


async def test_doctor_modal_navigates_menu_and_saves(tmp_path) -> None:
    from textual.widgets import Button, Input

    from code_ai.ui.terminal.doctor import DoctorModal

    cfg_path = tmp_path / "config.json"
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app, config_path=cfg_path)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/doctor"
        await pilot.press("enter")
        await pilot.pause(0.2)

        # /doctor pushed the guided-setup modal; query within it (the top screen).
        modal = terminal_app.screen
        assert isinstance(modal, DoctorModal)

        # The main menu lists every setup topic.
        for step_id in ("api_mode", "base_url", "api_key", "model", "vision_model", "workspace"):
            modal.query_one(f"#doctor-menu-{step_id}", Button)

        # The model step offers list/test/save and reveals the back button.
        await modal._set_step("model")
        await pilot.pause(0.1)
        modal.query_one("#doctor-input-model", Input)
        modal.query_one("#doctor-test-model", Button)
        modal.query_one("#doctor-list-model", Button)
        assert modal.query_one("#doctor-back", Button).has_class("doctor-hidden") is False

        # The vision-model step reuses the same list/test/save tooling and
        # saving it persists like any other field.
        await modal._set_step("vision_model")
        await pilot.pause(0.1)
        modal.query_one("#doctor-list-vision_model", Button)
        modal.query_one("#doctor-test-vision_model", Button)
        modal.query_one("#doctor-input-vision_model", Input).value = "qwen2.5-vl:7b"
        await pilot.click("#doctor-save-vision_model")
        await pilot.pause(0.1)
        assert fake_app.session.config.vision_model == "qwen2.5-vl:7b"

        # The back button (always at the top of the dialog) returns to the menu.
        await pilot.click("#doctor-back")
        await pilot.pause(0.1)
        assert modal._step == "menu"
        modal.query_one("#doctor-menu-model", Button)

        # The base URL step exposes a Validate button (reachability check).
        await modal._set_step("base_url")
        await pilot.pause(0.1)
        modal.query_one("#doctor-validate-base_url", Button)

        # Saving a text field via its button persists to the config file and
        # applies live.
        await modal._set_step("language")
        await pilot.pause(0.1)
        modal.query_one("#doctor-input-language", Input).value = "pt-BR"
        await pilot.click("#doctor-save-language")
        await pilot.pause(0.1)
        assert fake_app.session.config.language == "pt-BR"
        assert cfg_path.exists()


async def test_subagent_events_populate_agents_panel(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        sidebar = terminal_app.query_one("#sidebar")
        agents_panel = terminal_app.query_one("#subagents")
        # Idle: the whole sidebar (and the agents division) is hidden.
        assert sidebar.display is False
        assert agents_panel.display is False

        await fake_app.emit(
            "subagent.dispatch.requested", {"count": 2, "types": ["explorer", "explorer"]}
        )
        await fake_app.emit(
            "subagent.started",
            {"agent_id": "a1", "agent_type": "explorer", "task": "find the loader"},
        )
        await fake_app.emit(
            "subagent.started",
            {"agent_id": "a2", "agent_type": "coder", "task": "add a flag"},
        )
        await pilot.pause(0.2)

        # A dispatch makes the sidebar and the AGENTS division appear.
        assert sidebar.display is True
        assert agents_panel.display is True
        types = {a["agent_type"] for a in terminal_app.vm.subagents_list()}
        assert types == {"explorer", "coder"}

        await fake_app.emit(
            "subagent.completed",
            {"agent_id": "a1", "agent_type": "explorer", "summary": "found it"},
        )
        await pilot.pause(0.1)
        assert terminal_app.vm.subagents["a1"]["status"] == "completed"
        # The panel widget was fed the roster it renders from.
        panel = terminal_app.query_one("#subagents-body")
        panel_types = {agent["agent_type"] for agent in panel._agents}
        assert panel_types == {"explorer", "coder"}


def _widget_text(widget) -> str:
    """Everything a mounted widget is currently painting, as plain text."""
    from textual.geometry import Region

    # outer_size, not size: the box includes the border and padding rows.
    region = Region(0, 0, widget.outer_size.width, widget.outer_size.height)
    return "\n".join(strip.text for strip in widget.render_lines(region))


def _write_opened(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "write_file",
        "call_started": True,
        "writes": True,
        "path": "src/app.py",
        "chars": 30,
    }
    payload.update(extra)
    return payload


def _code_progress(offset: int, delta: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "write_file",
        "writes": True,
        "path": "src/app.py",
        "code_key": "content",
        "code_offset": offset,
        "code_delta": delta,
        "code_complete": False,
        "chars": 100 + offset,
    }
    payload.update(extra)
    return payload


async def test_the_window_opens_with_its_context_before_the_code(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        window = terminal_app.query_one("#code-window")
        # Idle: no write in flight, so the window stays out of the way.
        assert window.display is False

        await fake_app.emit(
            "tool.call.progress", _write_opened(reason="bound the cache")
        )
        await pilot.pause(0.3)

        # Open, framed and explained - with no source in it yet.
        assert window.display is True
        assert window.border_title == "write_file"
        assert window.border_subtitle == "src/app.py"
        painted = _widget_text(window)
        assert "Create / overwrite:  src/app.py" in painted
        assert "Why: bound the cache" in painted
        assert "receiving" in painted


async def test_streamed_code_fills_the_open_window(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        window = terminal_app.query_one("#code-window")
        await fake_app.emit("tool.call.progress", _write_opened())
        await fake_app.emit("tool.call.progress", _code_progress(0, "def main():\n"))
        await pilot.pause(0.2)
        assert terminal_app.vm.code_stream_code == "def main():\n"

        await fake_app.emit(
            "tool.call.progress",
            _code_progress(12, "    return 0\n", code_complete=True),
        )
        await pilot.pause(0.2)
        assert terminal_app.vm.code_stream_code == "def main():\n    return 0\n"
        assert terminal_app.vm.code_stream_complete is True
        # A second settle: the window is height:auto, so it grows a row on the
        # layout pass that follows the repaint.
        await pilot.pause(0.2)
        # The window renders the source itself, not just a progress count.
        painted = _widget_text(window)
        assert "def main():" in painted
        assert "return 0" in painted
        assert "✓" in painted


async def test_the_reason_follows_the_learn_setting(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.session.config.learn = False
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await fake_app.emit("tool.call.progress", _write_opened(reason="bound the cache"))
        await pilot.pause(0.3)

        painted = _widget_text(terminal_app.query_one("#code-window"))
        assert "Create / overwrite" in painted
        assert "Why:" not in painted


async def test_live_window_can_be_turned_off(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.session.config.terminal_live_code = False
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await fake_app.emit("tool.call.progress", _write_opened())
        await fake_app.emit("tool.call.progress", _code_progress(0, "x = 1\n"))
        await pilot.pause(0.2)

        assert terminal_app.query_one("#code-window").display is False
        # The one-line progress trace still reports the write.
        assert any(line.startswith("tool~ write_file:") for line in terminal_app.vm.conversation)


async def test_clear_closes_the_live_code_window(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await fake_app.emit("tool.call.progress", _write_opened())
        await pilot.pause(0.2)
        assert terminal_app.query_one("#code-window").display is True

        await terminal_app.action_clear()
        await pilot.pause(0.2)
        assert terminal_app.query_one("#code-window").display is False


def _plan_snapshot(status: str = "ACTIVE") -> dict[str, object]:
    return {
        "status": status,
        "progress": "1/3",
        "current_step": "Implement the module",
        "current_step_status": "IN_PROGRESS",
        "completed_steps": ["Inspect files"],
        "remaining_steps": ["Implement the module", "Verify"],
    }


async def test_clear_keeps_the_live_task_checklist_on_screen(tmp_path) -> None:
    # Regression: /clear (Ctrl+L) blind-hid the plan panel even mid-task. With
    # no plan event guaranteed to fire again soon (a long step, a paused plan),
    # the user lost sight of the in-flight task with no way to bring it back.
    # Clearing wipes the transcript; the live checklist is runtime state and
    # must be rebuilt from the backend snapshot.
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.plan_snapshot = _plan_snapshot()
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await fake_app.emit("planning.plan.created", _plan_snapshot())
        await pilot.pause(0.1)
        assert terminal_app.query_one("#plan").display is True

        await terminal_app.action_clear()
        await pilot.pause(0.1)

        assert terminal_app.vm.conversation == []
        assert terminal_app.vm.plan_visible is True
        assert terminal_app.query_one("#plan").display is True
        titles = [step["title"] for step in terminal_app.vm.plan_steps]
        assert titles == ["Inspect files", "Implement the module", "Verify"]
        assert terminal_app.vm.plan_progress == "1/3"


async def test_clear_keeps_a_paused_checklist_and_drops_a_settled_one(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        # A plan paused at turn end (WAITING) is still the current task: keep it.
        fake_app.plan_snapshot = _plan_snapshot(status="WAITING")
        await fake_app.emit("planning.plan.created", _plan_snapshot())
        await pilot.pause(0.1)
        await terminal_app.action_clear()
        assert terminal_app.vm.plan_visible is True
        assert terminal_app.vm.plan_status == "WAITING"

        # A settled plan belongs to the cleared transcript: it goes with it.
        fake_app.plan_snapshot = _plan_snapshot(status="COMPLETED")
        await terminal_app.action_clear()
        assert terminal_app.vm.plan_visible is False
        assert terminal_app.vm.plan_steps == []

        # No plan at all (nothing submitted yet): panel stays hidden.
        fake_app.plan_snapshot = {}
        await terminal_app.action_clear()
        assert terminal_app.vm.plan_visible is False


async def test_ctrl_j_inserts_newline_and_enter_submits_multiline(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.focus()
        # Ctrl+J adds a newline mid-prompt; Enter only sends once the whole
        # multi-line draft is composed.
        await pilot.press("l", "i", "n", "e", "1")
        await pilot.press("ctrl+j")
        await pilot.press("l", "i", "n", "e", "2")
        assert input_widget.text == "line1\nline2"

        await pilot.press("enter")
        await pilot.pause(0.2)
        assert fake_app.submitted == ["line1\nline2"]
        assert input_widget.text == ""


async def test_ctrl_v_attaches_clipboard_image_and_submits_it(tmp_path, monkeypatch) -> None:
    import base64

    png = b"\x89PNG\r\n\x1a\nfake-pixels"
    monkeypatch.setattr(
        "code_ai.ui.terminal.app.paste_image_from_system_clipboard",
        lambda: (png, "image/png"),
    )
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.focus()
        await pilot.press("l", "o", "o", "k", "space")
        await pilot.press("ctrl+v")
        assert input_widget.text == "look [Image #1]"

        await pilot.press("enter")
        await pilot.pause(0.2)
        assert fake_app.submitted == ["look [Image #1]"]
        (images,) = fake_app.submitted_images
        assert [image.data for image in images] == [base64.b64encode(png).decode("ascii")]
        assert images[0].media_type == "image/png"


async def test_deleting_the_placeholder_drops_the_attachment(tmp_path, monkeypatch) -> None:
    png = b"\x89PNG\r\n\x1a\nfake-pixels"
    monkeypatch.setattr(
        "code_ai.ui.terminal.app.paste_image_from_system_clipboard",
        lambda: (png, "image/png"),
    )
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.focus()
        await pilot.press("ctrl+v")
        assert input_widget.text == "[Image #1]"
        # Editing the placeholder out of the prompt removes the attachment.
        input_widget.value = "just words"

        await pilot.press("enter")
        await pilot.pause(0.2)
        assert fake_app.submitted == ["just words"]
        assert fake_app.submitted_images == [[]]


async def test_ctrl_v_falls_back_to_text_paste_without_an_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "code_ai.ui.terminal.app.paste_image_from_system_clipboard", lambda: None
    )
    monkeypatch.setattr(
        "code_ai.ui.terminal.app.paste_from_system_clipboard", lambda: "copied text"
    )
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.focus()
        await pilot.press("ctrl+v")
        assert input_widget.text == "copied text"


async def test_ctrl_c_copies_active_selection_instead_of_quitting(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)):
        copied: list[str] = []
        cleared: list[bool] = []
        terminal_app.copy_to_clipboard = lambda text: copied.append(text)
        terminal_app.screen.get_selected_text = lambda: "selected text"
        terminal_app.screen.clear_selection = lambda: cleared.append(True)

        # Ctrl+C copies the active selection (like a terminal) rather than
        # cancelling/quitting the session.
        await terminal_app.action_cancel_or_quit()

        assert copied == ["selected text"]
        assert cleared == [True]


async def test_ctrl_c_copies_selection_made_inside_the_prompt(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        copied: list[str] = []
        terminal_app.copy_to_clipboard = lambda text: copied.append(text)
        # The prompt is a TextArea that keeps its selection internally, so the
        # screen-level selection is empty even when the user has highlighted
        # text inside the input.
        terminal_app.screen.get_selected_text = lambda: None

        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "hello world"
        input_widget.focus()
        await pilot.pause()
        # Select "hello" inside the prompt, then copy with Ctrl+C.
        input_widget.selection = Selection((0, 0), (0, 5))
        await terminal_app.action_cancel_or_quit()

        assert copied == ["hello"]


async def test_compact_command_runs_immediately_and_reports_token_counts(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/compact"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert any(
            "18000 → 4000 tokens" in line for line in terminal_app.vm.conversation
        )


async def test_streaming_deltas_do_not_rerender_whole_transcript(tmp_path) -> None:
    from textual.containers import VerticalScroll

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        log = terminal_app.query_one("#conversation", VerticalScroll)
        mounts = 0
        original_mount = log.mount

        def counting_mount(*args, **kwargs):
            nonlocal mounts
            mounts += 1
            return original_mount(*args, **kwargs)

        log.mount = counting_mount  # type: ignore[method-assign]

        # The agent is working (the model is streaming), as in a real turn.
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        # Two finalized lines followed by 50 streaming deltas on one answer line.
        await fake_app.emit("user.message", {"text": "hi"})
        await fake_app.emit("model.request.started", {})
        for index in range(50):
            await fake_app.emit("model.stream.delta", {"text": f"{index} "})
        await pilot.pause(0.05)

        # Only the two finalized lines were ever mounted into the scrollback;
        # the 50 deltas mutate a single line shown live in the tail, not the log.
        assert mounts == 2
        assert terminal_app._committed == len(terminal_app.vm.conversation) - 1
        # The whole stream collapsed into one live line held in the tail Static.
        assert terminal_app.vm.conversation[-1].startswith("ai> ")
        assert "49" in terminal_app.vm.conversation[-1]

        # When the turn finishes the streamed answer must flow into the log
        # instead of being stranded in the live tail below it.
        await fake_app.emit("status.changed", {"state": "READY"})
        await pilot.pause(0.05)
        assert mounts == 3  # the final answer line was committed exactly once
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
    from textual.containers import VerticalScroll

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

        log = terminal_app.query_one("#conversation", VerticalScroll)
        mounts = 0
        original_mount = log.mount

        def counting_mount(*args, **kwargs):
            nonlocal mounts
            mounts += 1
            return original_mount(*args, **kwargs)

        log.mount = counting_mount  # type: ignore[method-assign]

        # The next turn flips status to working *before* its first line arrives.
        # The already-committed answer must not be pulled back or re-rendered.
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await pilot.pause(0.05)

        assert mounts == 0
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


async def test_conversation_history_is_drag_selectable(tmp_path) -> None:
    from textual import events
    from textual.containers import VerticalScroll
    from textual.geometry import Offset

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    def forward(cls, sx, sy):
        event = cls(
            terminal_app.screen, sx, sy, 0, 0, 1, False, False, False,
            screen_x=sx, screen_y=sy,
        )
        terminal_app.mouse_position = Offset(sx, sy)
        terminal_app.screen._forward_event(event)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        # Settle a few static history lines into the scrollback.
        await fake_app.emit("status.changed", {"state": "READY"})
        await fake_app.emit("user.message", {"text": "first question here"})
        await fake_app.emit("assistant.final", {"text": "an answer line here"})
        await pilot.pause(0.05)

        log = terminal_app.query_one("#conversation", VerticalScroll)
        # Each committed line is its own selectable widget — the property that
        # RichLog lacked, which is what stopped the history from selecting.
        assert len(log.children) == len(terminal_app.vm.conversation)

        # Drag across the history and confirm the screen selection extracts the
        # text (RichLog always returned None/"" here).
        region = log.children[0].region
        forward(events.MouseDown, region.x + 2, region.y)
        await pilot.pause()
        for sx in range(region.x + 3, region.x + 12):
            forward(events.MouseMove, sx, region.y)
            await pilot.pause()
        forward(events.MouseUp, region.x + 11, region.y)
        await pilot.pause()

        selected = terminal_app.screen.get_selected_text()
        assert selected
        assert "first" in selected


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


def test_trace_lines_are_plain_and_compacted() -> None:
    from code_ai.ui.terminal.widgets import render_conversation_line

    # Trace lines (thinking/model/tool/...) are plain text — dimmed and indented
    # by CSS, not chipped — so they read as a subordinate work trace.
    assert render_conversation_line("model> thinking step 0...") == "model> thinking step 0..."
    # Multi-line reasoning has its blank lines collapsed so the dim block stays
    # compact instead of sprawling.
    collapsed = render_conversation_line("thinking> one\n\n\ntwo\n\nthree")
    assert collapsed == "thinking> one\ntwo\nthree"


def test_user_chip_and_answer_chip_and_trace_classes() -> None:
    from textual.content import Content

    from code_ai.ui.terminal.widgets import (
        _MODEL_COLOR,
        _USER_COLOR,
        conversation_line_class,
        render_conversation_line,
    )

    # The user prompt is a green chip inline with the (literal) message.
    you = render_conversation_line("you> ola [x]")
    assert isinstance(you, Content)
    assert you.plain == " you  ola [x]"
    assert any(f"on {_USER_COLOR}" in str(span.style) for span in you.spans)

    # The agent's answer carries the orange chip above the formatted Markdown.
    answer = render_conversation_line("ai> the answer", rich_markdown=True, width=60)
    assert isinstance(answer, Content)
    assert answer.plain.startswith(" model ")
    assert "the answer" in answer.plain
    assert any(f"on {_MODEL_COLOR}" in str(span.style) for span in answer.spans)

    # Messages sit at column 0; every working-trace line shares one indent class.
    assert conversation_line_class("you> ola") == "turn-user"
    assert conversation_line_class("ai> answer") == "turn-answer"
    for trace in ("model> x", "thinking> x", "tool> x", "evidence> x", "plan> x"):
        assert conversation_line_class(trace) == "turn-trace"
    # Problems are not part of the dim trace — they keep full prominence.
    assert conversation_line_class("error> boom") == ""


def test_assistant_line_renders_as_selectable_markdown_content() -> None:
    from textual.content import Content

    from code_ai.ui.terminal.widgets import render_conversation_line

    # Committed answers format as Markdown but as native Textual Content, not a
    # wrapped Rich renderable: Content is what keeps the line mouse-selectable and
    # copyable (a RichVisual returns None from get_selection — it breaks copy).
    answer = render_conversation_line(
        "ai> # Title\n\n- **bold** item", rich_markdown=True, width=60
    )
    assert isinstance(answer, Content)
    plain = answer.plain
    # The formatting is applied (the leading "# " is gone) but the words — and so
    # the text the user can select and copy — survive.
    assert "# Title" not in plain
    assert "Title" in plain and "bold" in plain and "item" in plain

    # ...but the live-streaming path (no flag) stays plain so a half-written
    # fence does not render as a broken Markdown block mid-stream.
    streaming = render_conversation_line("ai> ```py\nprint(", rich_markdown=False)
    assert streaming == "ai> ```py\nprint("

    # An empty assistant line falls through to the plain string, not an empty box.
    assert render_conversation_line("ai> ", rich_markdown=True) == "ai> "


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


def test_view_model_collapses_streamed_working_trace_into_final_answer() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    # The answer prose streamed on the "working" channel (tool-required task),
    # then the runtime announced the same text as the turn's final answer.
    view_model.conversation.append("working> Preciso de mais detalhes.")
    event = EventEnvelope.create(
        event_type="assistant.final",
        session_id="fake-session",
        sequence=1,
        payload={"text": "Preciso de mais detalhes."},
        source="core.orchestrator",
    )

    view_model.apply(event)

    assert view_model.conversation == ["ai> Preciso de mais detalhes."]


def test_view_model_keeps_working_trace_that_differs_from_final_answer() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    view_model.conversation.append("working> Analisando o projeto...")
    event = EventEnvelope.create(
        event_type="assistant.final",
        session_id="fake-session",
        sequence=1,
        payload={"text": "Qual banco de dados devo usar?"},
        source="core.orchestrator",
    )

    view_model.apply(event)

    assert view_model.conversation == [
        "working> Analisando o projeto...",
        "ai> Qual banco de dados devo usar?",
    ]


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
        input_widget = terminal_app.query_one("#input", TextArea)
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


async def test_tab_accepts_slash_command_completion(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/config api-m"
        await pilot.press("tab")
        await pilot.pause(0.2)

        assert input_widget.value == "/config api-mode "
        # The cursor lands at the end of the accepted completion.
        assert input_widget.cursor_at_end_of_text


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


def test_config_vision_model_command_persists_and_updates_active_config(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config vision-model qwen2.5-vl:7b",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Applied now" in result
    assert fake_app.session.config.vision_model == "qwen2.5-vl:7b"
    assert saved["vision_model"] == "qwen2.5-vl:7b"

    # "off" clears the setting, sending images to the main model again.
    handle_config_command(fake_app, "/config vision-model off", config_path=config_path)
    assert fake_app.session.config.vision_model == ""


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


def test_config_max_context_window_command_persists_and_updates_active_config(
    tmp_path,
) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config max-context-window 128000",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Restart Code-AI" in result
    assert fake_app.session.config.budgets.max_context_tokens == 128000
    assert saved["budgets"]["max_context_tokens"] == 128000


def test_config_max_context_window_command_rejects_non_integer(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config max-context-window not-a-number",
        config_path=config_path,
    )
    assert "Invalid token count" in result
    assert not config_path.exists()


def test_config_max_context_window_command_rejects_below_minimum(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config max-context-window 1000",
        config_path=config_path,
    )
    assert "Config not changed" in result
    assert not config_path.exists()


def test_config_learn_command_persists_and_updates_active_config(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config learn off",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Learn mode off" in result
    assert fake_app.session.config.learn is False
    assert saved["learn"] is False


def test_config_learn_command_rejects_invalid_value(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config learn maybe",
        config_path=config_path,
    )
    assert "Usage: /config learn" in result
    assert not config_path.exists()


def test_config_learn_completes_and_suggests_values() -> None:
    assert command_completion("/config lea") == "/config learn "
    assert command_completion("/config learn of") == "/config learn off"
    rendered = render_suggestions("/config learn ")
    assert "/config learn on" in rendered
    assert "/config learn off" in rendered


def test_config_live_code_command_persists_and_updates_active_config(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config live-code off",
        config_path=config_path,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Live code off" in result
    assert fake_app.session.config.terminal_live_code is False
    assert saved["terminal_live_code"] is False


def test_config_live_code_command_rejects_invalid_value(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    result = handle_config_command(
        fake_app,
        "/config live-code sometimes",
        config_path=config_path,
    )
    assert "Usage: /config live-code" in result
    assert not config_path.exists()


def test_config_live_code_completes_and_suggests_values() -> None:
    assert command_completion("/config live-c") == "/config live-code "
    assert command_completion("/config live-code of") == "/config live-code off"
    rendered = render_suggestions("/config live-code ")
    assert "/config live-code on" in rendered
    assert "/config live-code off" in rendered


async def test_up_arrow_recalls_previous_submitted_entries(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)

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
        input_widget = terminal_app.query_one("#input", TextArea)

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


async def test_toggle_button_collapses_and_persists_session_panel(tmp_path) -> None:
    from textual.widgets import Button

    fake_app = FakeTerminalApplication(tmp_path)
    config_path = tmp_path / "config.json"
    terminal_app = create_terminal_app(fake_app, config_path=config_path)

    async with terminal_app.run_test(size=(120, 44)) as pilot:
        session = terminal_app.query_one("#session")
        button = terminal_app.query_one("#toggle-session", Button)
        assert not session.has_class("collapsed")

        # Pressing the toggle collapses the panel, flips the arrow, and persists.
        terminal_app.action_toggle_session()
        await pilot.pause(0.1)
        assert session.has_class("collapsed")
        assert str(button.label) == "›"
        assert fake_app.session.config.terminal_session_collapsed is True
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["terminal_session_collapsed"] is True

        # Toggling again restores it and persists the restored state.
        terminal_app.action_toggle_session()
        await pilot.pause(0.1)
        assert not session.has_class("collapsed")
        assert str(button.label) == "‹"
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["terminal_session_collapsed"] is False


async def test_session_panel_starts_collapsed_when_configured(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.session.config.terminal_session_collapsed = True
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 44)) as pilot:
        await pilot.pause(0.1)
        assert terminal_app.query_one("#session").has_class("collapsed")


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
        input_widget = terminal_app.query_one("#input", TextArea)

        # A command that needs a value drops its stem into the prompt for editing.
        terminal_app._use_config_command(
            SlashCommand("/config model <name>", "x", "/config model ")
        )
        await pilot.pause(0.1)
        assert input_widget.value == "/config model "
        # The cursor sits at the end so the user types the argument straight away.
        assert input_widget.cursor_at_end_of_text


async def test_config_help_picker_runs_argless_commands(tmp_path) -> None:
    from code_ai.ui.terminal.slash_commands import SlashCommand

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(110, 44)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)

        # An argument-free command runs immediately instead of pre-filling.
        terminal_app._use_config_command(SlashCommand("/config show", "x"))
        await pilot.pause(0.1)
        assert input_widget.value == ""
        # /config show appends the redacted config to the conversation.
        assert any('"model"' in line for line in terminal_app.vm.conversation)


async def test_agents_panel_mounts_one_card_per_subagent(tmp_path) -> None:
    from textual.widgets import Collapsible

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 45)) as pilot:
        await fake_app.emit("user.message", {"text": "explore"})
        for agent_id, name, task in (
            ("a1", "Kepler", "map the loader and every config path in the repo"),
            ("a2", "Feynman", "review the domain code"),
        ):
            await fake_app.emit(
                "subagent.started",
                {"agent_id": agent_id, "agent_type": "explorer", "name": name, "task": task},
            )
        await pilot.pause(0.2)

        panel = terminal_app.query_one("#subagents-body")
        cards = panel.query(".agent-card")
        assert len(cards) == 2
        assert "2 running" in str(panel.query_one(".agents-summary", Static).render())

        # Each card: a header row and the delegated task folded shut.
        first = cards.nodes[0]
        header = str(first.query_one(".agent-card-header", Static).render())
        assert "Kepler" in header and "explorer" in header
        collapsible = first.query_one(Collapsible)
        assert collapsible.collapsed is True
        assert "\n" not in collapsible.title

        # Clicking the preview expands the full task text.
        await pilot.click(first.query_one("CollapsibleTitle"))
        await pilot.pause(0.1)
        assert collapsible.collapsed is False
        body = str(first.query_one(".agent-task-body", Static).render())
        assert "map the loader and every config path in the repo" in body


async def test_agents_panel_keeps_expanded_card_across_updates(tmp_path) -> None:
    from textual.widgets import Collapsible

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 45)) as pilot:
        await fake_app.emit("user.message", {"text": "explore"})
        await fake_app.emit(
            "subagent.started",
            {"agent_id": "a1", "agent_type": "explorer", "name": "Kepler", "task": "t1"},
        )
        await pilot.pause(0.2)

        panel = terminal_app.query_one("#subagents-body")
        first = panel.query(".agent-card").nodes[0]
        await pilot.click(first.query_one("CollapsibleTitle"))
        await pilot.pause(0.1)
        assert first.query_one(Collapsible).collapsed is False

        # Progress and a later dispatch repaint the roster in place: the
        # expanded card must survive, not be remounted shut.
        await fake_app.emit(
            "subagent.progress",
            {"agent_id": "a1", "event": "tool.call.started", "tool": "read_file"},
        )
        await fake_app.emit(
            "subagent.started",
            {"agent_id": "a2", "agent_type": "coder", "name": "Newton", "task": "t2"},
        )
        await pilot.pause(0.2)
        cards = panel.query(".agent-card")
        assert len(cards) == 2
        assert cards.nodes[0] is first
        assert first.query_one(Collapsible).collapsed is False
        assert "read_file" in str(first.query_one(".agent-task-body", Static).render())

        # A settled agent flips its marker; a new turn clears the roster.
        await fake_app.emit(
            "subagent.completed",
            {"agent_id": "a1", "agent_type": "explorer", "name": "Kepler", "summary": "ok"},
        )
        await pilot.pause(0.2)
        assert str(first.query_one(".agent-card-header", Static).render()).startswith("✓")

        await fake_app.emit("user.message", {"text": "next"})
        await pilot.pause(0.2)
        assert len(panel.query(".agent-card")) == 0


class FakeUiTerminalManager:
    """Manager double for the /term flow: records input, serves one screen."""

    def __init__(self) -> None:
        self.sent_text: list[tuple[str, str]] = []
        self.entered: list[str] = []
        self.controls: list[tuple[str, str]] = []
        self.killed: list[str] = []
        self.screen_text = "C:\\workspace> _"
        self._session_id = "sess-1234abcd"

    def create(self, *, cwd, command=None, rows: int = 24, cols: int = 80) -> str:
        return self._session_id

    def latest_session_id(self) -> str | None:
        return self._session_id

    def send_text(self, session_id: str, text: str) -> None:
        self.sent_text.append((session_id, text))

    def send_enter(self, session_id: str) -> None:
        self.entered.append(session_id)

    def send_control(self, session_id: str, key: str) -> None:
        self.controls.append((session_id, key))

    def terminate(self, session_id: str) -> None:
        self.killed.append(session_id)

    def read_screen(self, session_id: str, *, include_cursor: bool = True) -> dict[str, object]:
        return {
            "session_id": session_id,
            "rows": 24,
            "columns": 80,
            "screen": self.screen_text,
            "closed": False,
        }


async def test_term_commands_drive_the_shared_terminal_session(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    manager = FakeUiTerminalManager()
    fake_app.terminal_manager = manager
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 44)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)

        # /term start opens the session and the TERMINAL panel appears with
        # the live screen (fed by the terminal.screen.updated event).
        input_widget.value = "/term start"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert any(
            line.startswith("term> Sessão sess-123") for line in terminal_app.vm.conversation
        )
        assert terminal_app.vm.terminal_visible is True
        assert terminal_app.vm.terminal_screen == "C:\\workspace> _"
        assert terminal_app.query_one("#terminal").display is True
        assert terminal_app.query_one("#sidebar").display is True

        # Free text after /term is typed into the session followed by Enter —
        # the user acts on the same PTY the agent drives.
        input_widget.value = "/term echo hi"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert manager.sent_text == [("sess-1234abcd", "echo hi")]
        assert manager.entered == ["sess-1234abcd"]

        # Ctrl+C reaches the session (interrupt a runaway process).
        input_widget.value = "/term ctrl c"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert manager.controls == [("sess-1234abcd", "c")]

        # /term kill terminates it and the panel flips to closed.
        input_widget.value = "/term kill"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert manager.killed == ["sess-1234abcd"]
        assert terminal_app.vm.terminal_closed is True


async def test_term_without_a_manager_degrades_gracefully(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/term start"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert any("indisponível" in line for line in terminal_app.vm.conversation)
        assert terminal_app.vm.terminal_visible is False


def test_view_model_streams_command_output_chunks_in_place() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    for sequence, chunk in enumerate(("building", " wheel", "\ndone"), start=1):
        view_model.apply(
            EventEnvelope.create(
                event_type="command.output",
                session_id="fake-session",
                sequence=sequence,
                payload={"stream": "stdout", "text": chunk},
                source="tool.execute_command",
            )
        )

    # All chunks merged into one live cmd~ line, not one line per chunk.
    assert view_model.conversation == ["cmd~ building wheel\ndone"]


def test_view_model_keeps_terminal_panel_across_turns() -> None:
    from code_ai.ui.terminal.view_models import TerminalViewModel

    view_model = TerminalViewModel()
    view_model.apply(
        EventEnvelope.create(
            event_type="terminal.screen.updated",
            session_id="fake-session",
            sequence=1,
            payload={
                "session_id": "abc12345",
                "rows": 24,
                "columns": 80,
                "screen": "$ npm run dev\nserver listening on :3000",
                "closed": False,
            },
            source="terminal.poller",
        )
    )
    assert view_model.terminal_visible is True
    assert "listening" in view_model.terminal_screen

    # A new user turn clears plan/agents (turn-scoped) but the terminal is
    # session-scoped: a dev server keeps running, so the panel must survive.
    view_model.apply(
        EventEnvelope.create(
            event_type="user.message",
            session_id="fake-session",
            sequence=2,
            payload={"text": "continue"},
            source="test",
        )
    )
    assert view_model.terminal_visible is True
    assert view_model.terminal_session_id == "abc12345"


def test_markdown_answer_keeps_raw_xml_visible() -> None:
    # Rich's Markdown silently drops html_block/html_inline tokens, so an
    # answer quoting an XML document outside a code fence vanished from the
    # chat entirely - the user saw prose around a hole where the XML should be.
    from code_ai.ui.terminal.widgets import markdown_to_content

    body = (
        "Aqui esta o exemplo.xml:\n\n"
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<biblioteca nome="minha">\n'
        '  <livro id="1" ano="1899">\n'
        "    <titulo>Dom Casmurro</titulo>\n"
        "  </livro>\n"
        "</biblioteca>\n\n"
        "O arquivo <b>foi criado</b>."
    )
    plain = markdown_to_content(body, 78).plain

    for fragment in (
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<biblioteca nome="minha">',
        "<titulo>Dom Casmurro</titulo>",
        "</biblioteca>",
        "<b>foi criado</b>",  # inline HTML also renders literally
    ):
        assert fragment in plain, f"missing from rendered answer: {fragment}"
    # The prolog and the root element are one contiguous block: no blank hole
    # between the merged html_block tokens.
    xml_start = plain.index("<?xml")
    xml_end = plain.index("</biblioteca>")
    assert "\n\n" not in plain[xml_start:xml_end]


def test_markdown_fenced_code_still_renders_normally() -> None:
    from code_ai.ui.terminal.widgets import markdown_to_content

    body = 'Veja:\n\n```xml\n<a b="c"/>\n```\n'
    plain = markdown_to_content(body, 78).plain
    assert '<a b="c"/>' in plain


def _workflow_app(tmp_path, files: dict[str, str]):
    """A fake app whose workflows come from a real directory on disk."""

    from code_ai.core.workflows import WorkflowService, WorkflowSource

    root = tmp_path / "workflows"
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.workflows = WorkflowService(
        sources=[WorkflowSource(root=root, scope="project", origin="cline")]
    )
    return fake_app


async def test_slash_workflow_submits_its_saved_steps(tmp_path) -> None:
    fake_app = _workflow_app(tmp_path, {"deploy.md": "# Deploy\n\n1. Build\n2. Ship"})
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/deploy 1.4.0"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert len(fake_app.submitted) == 1
        submitted = fake_app.submitted[0]
        assert 'Run the "deploy" workflow' in submitted
        assert "1. Build" in submitted
        assert "Additional input for this run: 1.4.0" in submitted
        assert any("Running workflow deploy" in line for line in terminal_app.vm.conversation)


async def test_unknown_slash_text_is_submitted_as_a_message(tmp_path) -> None:
    fake_app = _workflow_app(tmp_path, {"deploy.md": "1. Build"})
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/not-a-workflow"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert fake_app.submitted == ["/not-a-workflow"]


async def test_a_workflow_cannot_shadow_a_builtin_command(tmp_path) -> None:
    fake_app = _workflow_app(tmp_path, {"status.md": "Never run this."})
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/status"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert fake_app.submitted == []
        assert not any("Never run this" in line for line in terminal_app.vm.conversation)


async def test_workflows_command_lists_them_with_origin(tmp_path) -> None:
    fake_app = _workflow_app(
        tmp_path, {"deploy.md": "Ship a release.", "triage.md": "Triage a bug."}
    )
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)):
        terminal_app._append_conversation_line(terminal_app._workflows_text())
        rendered = "\n".join(terminal_app.vm.conversation)

        assert "/deploy" in rendered
        assert "Ship a release." in rendered
        assert "(cline)" in rendered


async def test_workflow_appears_in_command_suggestions(tmp_path) -> None:
    fake_app = _workflow_app(tmp_path, {"deploy.md": "Ship a release."})
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)):
        terminal_app._set_command_suggestions("/dep")
        suggestions = terminal_app.query_one("#command-suggestions", Static)
        assert suggestions.display is True
        rendered = str(suggestions.render())
        assert "/deploy" in rendered
        assert "Ship a release." in rendered


async def test_tab_completes_a_workflow_name(tmp_path) -> None:
    fake_app = _workflow_app(tmp_path, {"deploy.md": "Ship a release."})
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/depl"
        await pilot.press("tab")
        await pilot.pause(0.2)

        assert input_widget.value == "/deploy "


def _skill_app(tmp_path, files: dict[str, str]):
    """A fake app whose skills come from a real directory on disk."""

    from code_ai.tools.skills.common import SkillSource

    root = tmp_path / "skills"
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.skill_sources = (SkillSource(root=root, origin="cline"),)
    return fake_app


async def test_slash_skill_forces_its_instructions(tmp_path) -> None:
    fake_app = _skill_app(
        tmp_path,
        {
            "pdf-magic/SKILL.md": (
                "---\nname: pdf-magic\ndescription: Extract tables from PDFs.\n---\n\n"
                "Use pdfplumber, never regex."
            )
        },
    )
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/pdf-magic extrair a tabela do relatorio.pdf"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert len(fake_app.submitted) == 1
        submitted = fake_app.submitted[0]
        assert 'Use the "pdf-magic" skill' in submitted
        assert "Use pdfplumber, never regex." in submitted
        assert "Task: extrair a tabela do relatorio.pdf" in submitted
        assert any("Using skill pdf-magic" in line for line in terminal_app.vm.conversation)


async def test_tab_completes_a_skill_name(tmp_path) -> None:
    fake_app = _skill_app(
        tmp_path,
        {"pdf-magic/SKILL.md": "---\nname: pdf-magic\ndescription: Extract tables.\n---\n\nSteps."},
    )
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/pdf-m"
        await pilot.press("tab")
        await pilot.pause(0.2)

        assert input_widget.value == "/pdf-magic "


async def test_skills_command_lists_them_with_origin(tmp_path) -> None:
    fake_app = _skill_app(
        tmp_path,
        {"pdf-magic/SKILL.md": "---\nname: pdf-magic\ndescription: Extract tables.\n---\n\nSteps."},
    )
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)):
        terminal_app._append_conversation_line(terminal_app._skills_text())
        rendered = "\n".join(terminal_app.vm.conversation)

        assert "/pdf-magic" in rendered
        assert "Extract tables." in rendered
        assert "(cline)" in rendered


async def test_a_workflow_wins_over_a_same_named_skill(tmp_path) -> None:
    from code_ai.core.workflows import WorkflowService, WorkflowSource

    fake_app = _skill_app(
        tmp_path,
        {"release/SKILL.md": "---\nname: release\ndescription: The skill.\n---\n\nSkill body."},
    )
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir()
    (workflows_root / "release.md").write_text("Workflow steps.", encoding="utf-8")
    fake_app.workflows = WorkflowService(
        sources=[WorkflowSource(root=workflows_root, scope="project", origin="code-ai")]
    )
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/release"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert "Workflow steps." in fake_app.submitted[0]
        assert "Skill body." not in fake_app.submitted[0]


async def test_disabled_skill_is_not_a_slash_command(tmp_path) -> None:
    fake_app = _skill_app(
        tmp_path,
        {
            "legacy/SKILL.md": (
                "---\nname: legacy\ndescription: Old.\ndisabled: true\n---\n\nDo not use."
            )
        },
    )
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "/legacy"
        await pilot.press("enter")
        await pilot.pause(0.2)

        # Not a command and not an instruction dump: it travels as plain text.
        assert fake_app.submitted == ["/legacy"]


async def test_bracketed_trace_text_never_raises_markup_error(tmp_path) -> None:
    # Regression: every Static that receives a runtime-built string used to parse
    # it as console markup, so a bracket in model or tool text raised MarkupError
    # inside the event subscriber ("Event subscriber failed while handling
    # planning.evidence.recorded (MarkupError)"). Static.update() visualizes
    # eagerly, so the exception surfaced synchronously on the emit.
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        # Working, so the newest line is held back in the live tail — the widget
        # that actually blew up.
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await fake_app.emit(
            "planning.evidence.recorded",
            {"type": "FILE_READ", "summary": "read parser.py [1/3] lines [10:40]"},
        )
        await pilot.pause(0.05)

        assert "[1/3]" in terminal_app.vm.conversation[-1]

        # The same hazard reaches the plan step through the session panel and the
        # model name through the status line.
        await fake_app.emit(
            "planning.step.started",
            {"current_step": "fix [bug] in parser", "plan_progress": "1/2"},
        )
        await pilot.pause(0.05)

        for widget_id in ("#stream-tail", "#statusline", "#session-info", "#command-suggestions"):
            assert terminal_app.query_one(widget_id, Static)._render_markup is False


async def test_reasoning_streams_into_its_own_panel_not_loose_on_screen(tmp_path) -> None:
    # The model's reasoning used to stream straight into the tail strip, which
    # has no frame: it spilled down the pane at full width and read as if it had
    # escaped the conversation. It now streams into a contained, titled box, and
    # only one of the two ever holds it.
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 40)) as pilot:
        await fake_app.emit("user.message", {"text": "go"})
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await fake_app.emit("model.thinking.delta", {"text": "weighing the options"})
        await pilot.pause(0.2)

        panel = terminal_app.query_one("#thinking-panel", Static)
        assert panel.display is True
        assert "weighing the options" in str(panel.render())
        assert panel.border_title == "thinking"
        # The prefix belongs to the transcript buffer, not to the panel: the
        # border already says what the box holds.
        assert "thinking>" not in str(panel.render())
        # ...and nothing is left loose in the strip below it.
        assert str(terminal_app.query_one("#stream-tail", Static).render()) == ""

        # The answer is a message, not reasoning: it goes back to the strip and
        # the box closes, so reasoning is never shown boxed and loose at once.
        await fake_app.emit("model.stream.delta", {"text": "here is the answer"})
        await pilot.pause(0.2)
        assert panel.display is False
        assert "here is the answer" in str(terminal_app.query_one("#stream-tail", Static).render())


async def test_thinking_panel_shows_only_the_rows_it_can_fit(tmp_path) -> None:
    # A reasoning block has no natural length. The panel is a fixed-height box,
    # so it renders only its newest rows however long the model reasons — that
    # bound is what keeps the per-fragment cost constant — while the border
    # subtitle still reports the size of the whole block.
    from code_ai.ui.terminal.widgets import (
        THINKING_PANEL_MAX_CHARS,
        THINKING_PANEL_MAX_ROWS,
    )

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 40)) as pilot:
        await fake_app.emit("user.message", {"text": "go"})
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        for index in range(400):
            await fake_app.emit("model.thinking.delta", {"text": f"line {index} of reasoning\n"})
        await pilot.pause(0.3)

        panel = terminal_app.query_one("#thinking-panel", Static)
        body = str(panel.render())
        assert len(body.splitlines()) <= THINKING_PANEL_MAX_ROWS
        assert len(body) <= THINKING_PANEL_MAX_CHARS
        # The newest reasoning is what survives the trim, not the oldest.
        assert "line 399 of reasoning" in body
        assert "line 0 of reasoning" not in body
        # Nothing is lost from the transcript itself.
        assert "line 0 of reasoning" in terminal_app.vm.conversation[-1]
        assert "chars" in (panel.border_subtitle or "")


def test_committed_reasoning_is_bounded_before_it_reaches_a_widget() -> None:
    # The folded block has to word-wrap every character it holds the moment the
    # user expands it, so a pathological block is trimmed to its newest text
    # rather than handed to the widget whole.
    from code_ai.ui.terminal.widgets import THINKING_BLOCK_MAX_CHARS, thinking_body

    short = thinking_body("thinking> one\n\n\ntwo")
    assert short == "one\ntwo"

    huge = "thinking> " + ("x" * 10 + "\n") * 40_000
    bounded = thinking_body(huge)
    assert len(bounded) <= THINKING_BLOCK_MAX_CHARS + 40
    assert bounded.startswith("… earlier reasoning trimmed …")
    # Asking for the whole thing is still possible, for callers that can take it.
    assert len(thinking_body(huge, max_chars=None)) > THINKING_BLOCK_MAX_CHARS
    # A line that is not reasoning is still not claimed by this renderer.
    assert thinking_body("ai> answer") is None


async def test_clearing_the_screen_empties_the_thinking_panel(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 40)) as pilot:
        await fake_app.emit("user.message", {"text": "go"})
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await fake_app.emit("model.thinking.delta", {"text": "mid-thought"})
        await pilot.pause(0.2)
        assert terminal_app.query_one("#thinking-panel", Static).display is True

        await terminal_app.action_clear()
        await pilot.pause(0.2)
        panel = terminal_app.query_one("#thinking-panel", Static)
        assert panel.display is False
        assert str(panel.render()) == ""


async def test_a_reasoning_flood_does_not_repaint_the_status_area(tmp_path) -> None:
    # The freeze: every event refreshed the whole status area, and a reasoning
    # model emits hundreds of events a second. Six widgets were rebuilt - and
    # six layouts invalidated - per fragment, none of them showing anything new.
    # This is the guard: a flood that changes nothing must repaint nothing.
    import collections

    counts: collections.Counter[str] = collections.Counter()
    original = Static.update

    def counting_update(self, renderable="", *args, **kwargs):
        counts[self.id or type(self).__name__] += 1
        return original(self, renderable, *args, **kwargs)

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 40)) as pilot:
        await fake_app.emit("user.message", {"text": "go"})
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await pilot.pause(0.1)

        Static.update = counting_update
        try:
            for _ in range(500):
                await fake_app.emit("model.thinking.delta", {"text": "reasoning on and on, "})
            await pilot.pause(0.1)
        finally:
            Static.update = original

        for widget_id in ("statusline", "context-meter", "session-info", "stream-tail"):
            assert counts[widget_id] == 0, f"{widget_id} repainted {counts[widget_id]}x"
        assert counts["plan-body"] == 0
        # The panel itself is rate-limited by its timer, so it paints a handful
        # of times per second - never once per fragment.
        assert counts["thinking-panel"] < 20, counts["thinking-panel"]

        # ...and a real change still repaints, or the guard would be a bug of
        # its own: nothing on screen would ever update again.
        Static.update = counting_update
        try:
            await fake_app.emit("status.changed", {"state": "READY"})
            await pilot.pause(0.1)
        finally:
            Static.update = original
        assert counts["statusline"] == 1


async def test_status_widgets_still_update_when_their_content_changes(tmp_path) -> None:
    # Companion to the repaint guard above: skipping unchanged repaints must not
    # cost the UI a single real update.
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 40)) as pilot:
        await fake_app.emit(
            "planning.step.started", {"current_step": "read the parser", "progress": "1/3"}
        )
        await pilot.pause(0.1)
        assert "1/3" in str(terminal_app.query_one("#statusline", Static).render())
        assert "read the parser" in str(terminal_app.query_one("#session-info", Static).render())

        await fake_app.emit(
            "planning.step.completed", {"current_step": "write the fix", "progress": "2/3"}
        )
        await pilot.pause(0.1)
        assert "2/3" in str(terminal_app.query_one("#statusline", Static).render())
        assert "write the fix" in str(terminal_app.query_one("#session-info", Static).render())

        await fake_app.emit(
            "usage.updated",
            {"active_context_tokens": 4000, "context_budget": 8000, "context_threshold": 0.82},
        )
        await pilot.pause(0.1)
        assert "50%" in str(terminal_app.query_one("#context-meter", Static).render())

        # The tail only holds a line while the agent is working; committed lines
        # are never pulled back into it.
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await fake_app.emit("model.stream.delta", {"text": "an answer"})
        await pilot.pause(0.1)
        assert "an answer" in str(terminal_app.query_one("#stream-tail", Static).render())


def test_a_streaming_line_grows_in_linear_time() -> None:
    # ``conversation[-1] += text`` copies the whole line on every fragment: the
    # cost of a reasoning block grows with the square of its length, so the long
    # blocks - the ones that hurt - are exactly the ones that stall. Growing the
    # line in one place instead keeps it linear.
    import time

    from code_ai.ui.terminal.view_models import TerminalViewModel

    def flood(count: int) -> float:
        view_model = TerminalViewModel()
        event = EventEnvelope.create(
            event_type="model.thinking.delta",
            session_id="fake-session",
            sequence=1,
            payload={"text": "reasoning about the next step in some detail, "},
            source="test",
        )
        start = time.perf_counter()
        for _ in range(count):
            view_model.apply(event)
        return time.perf_counter() - start

    flood(2_000)  # warm up the interpreter, not the measurement
    small = flood(10_000)
    large = flood(40_000)

    # Four times the fragments: linear lands near 4x, quadratic near 16x. The
    # bound is deliberately loose so a busy machine cannot fail it, and still
    # catches the regression by a wide margin.
    assert large < small * 8, f"{small:.4f}s -> {large:.4f}s: growth is not linear"


async def test_an_expanded_reasoning_block_scrolls_instead_of_unrolling(tmp_path) -> None:
    # An expanded block used to be as tall as the model was verbose: hundreds of
    # rows laid out at once, pushing the rest of the turn off screen. It is a
    # panel now - capped, with the reasoning scrolling inside it.
    from textual.widgets import Collapsible

    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 40)) as pilot:
        await fake_app.emit("user.message", {"text": "go"})
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        for index in range(300):
            await fake_app.emit("model.thinking.delta", {"text": f"row {index} of reasoning\n"})
        # The turn ends, so the block is committed to the transcript.
        await fake_app.emit("status.changed", {"state": "READY"})
        await pilot.pause(0.2)

        block = terminal_app.query(".thinking-block").first(Collapsible)
        block.collapsed = False
        await pilot.pause(0.2)

        contents = block.query_one(Collapsible.Contents)
        assert contents.size.height <= 18, contents.size.height
        assert contents.is_vertical_scroll_end is False or contents.max_scroll_y > 0
