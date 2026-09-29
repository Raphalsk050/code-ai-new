"""The terminal says which tool is running and when a request is being retried."""

from __future__ import annotations

from textual.content import Content

from code_ai.events.models import EventEnvelope
from code_ai.tools.summary import call_target
from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.palette import CODE_AI_PALETTE
from code_ai.ui.terminal.view_models import TerminalViewModel
from code_ai.ui.terminal.widgets import render_conversation_line, working_label
from tests.unit.test_terminal_ui import FakeTerminalApplication


def _event(event_type: str, payload: dict, sequence: int = 1) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event_type,
        session_id="s",
        sequence=sequence,
        payload=payload,
        source="test",
    )


def _styles(content: Content) -> str:
    return " ".join(str(span.style) for span in content.spans)


def test_a_call_is_described_by_what_it_works_on() -> None:
    assert call_target("read_file", {"path": "src/a.py"}) == "src/a.py"
    assert call_target("read_file", {"path": "a.py", "start_line": 3, "end_line": 9}) == (
        "a.py:3-9"
    )
    assert call_target("execute_command", {"command": "npm  test\n--ci"}) == "npm test --ci"
    assert call_target("search_code", {"query": "parse_config", "path": "src"}) == (
        "parse_config in src"
    )
    assert call_target("web_search", {"query": "textual themes"}) == "textual themes"
    assert call_target("dispatch_agent", {"tasks": [{}, {}]}) == "2 tasks"
    long_path = "deep/" * 30 + "file.py"
    assert call_target("read_file", {"path": long_path}).endswith("file.py")
    # Typed text and credentials never show up as the target.
    assert call_target("type_text", {"text": "hunter2"}) == ""
    assert call_target("mcp__x__login", {"password": "hunter2", "user": "ana"}) == "ana"
    assert call_target("screen_info", {}) == ""


def test_the_working_indicator_names_the_running_tool() -> None:
    vm = TerminalViewModel()
    vm.apply(_event("status.changed", {"state": "EXECUTING_TOOL"}))
    vm.apply(
        _event(
            "tool.call.started",
            {"tool_call_id": "c1", "name": "read_file", "target": "src/a.py"},
            2,
        )
    )

    assert vm.working_activity() == "running read_file · src/a.py"
    assert working_label(vm.status, activity=vm.working_activity()) == (
        "running read_file · src/a.py"
    )

    vm.apply(_event("tool.call.started", {"tool_call_id": "c2", "name": "search_code"}, 3))
    assert vm.working_activity() == "running 2 tools · read_file, search_code"

    vm.apply(
        _event(
            "tool.call.completed",
            {"tool_call_id": "c1", "name": "read_file", "target": "src/a.py", "result": {}},
            4,
        )
    )
    vm.apply(
        _event(
            "tool.call.failed",
            {"tool_call_id": "c2", "name": "search_code", "target": "x", "message": "boom"},
            5,
        )
    )
    assert vm.working_activity() == ""
    assert vm.conversation[-2:] == ["tool> read_file src/a.py", "tool> search_code x failed: boom"]


def test_a_written_file_is_not_named_twice() -> None:
    vm = TerminalViewModel()
    vm.apply(
        _event(
            "tool.call.completed",
            {"name": "write_file", "target": "app.py", "result": {"path": "app.py"}},
        )
    )
    assert vm.conversation == ["tool> write_file app.py"]


def test_a_tool_line_paints_the_name_the_target_and_the_result_apart() -> None:
    palette = CODE_AI_PALETTE
    line = render_conversation_line("tool> read_file src/a.py: 12 lines")

    assert line.plain == "tool> read_file src/a.py: 12 lines"
    styles = _styles(line)
    assert palette.info in styles
    assert palette.foreground in styles
    assert palette.trace in styles

    failed = render_conversation_line("tool> run_tests npm test failed: 1 failed")
    assert palette.error_soft in _styles(failed)
    # No target: the failure still reads as one.
    bare = render_conversation_line("tool> web_search failed: no results")
    assert palette.error_soft in _styles(bare)
    assert palette.foreground not in _styles(bare)


def test_a_retry_takes_back_what_the_broken_attempt_streamed() -> None:
    vm = TerminalViewModel()
    vm.apply(_event("user.message", {"text": "oi"}))
    vm.apply(_event("status.changed", {"state": "CALLING_MODEL"}, 2))
    vm.apply(_event("model.request.started", {"step": 0}, 3))
    vm.apply(_event("model.thinking.delta", {"text": "hmm"}, 4))
    vm.apply(_event("model.stream.delta", {"text": "Half an ans"}, 5))

    vm.apply(
        _event(
            "model.request.retrying",
            {
                "attempt": 1,
                "max_attempts": 20,
                "reason": "502 Bad Gateway",
                "delay_s": 0.5,
                "discarded": True,
            },
            6,
        )
    )

    assert vm.conversation == [
        "you> oi",
        "model> thinking step 0...",
        "model> retrying 1/20: 502 Bad Gateway",
    ]
    assert vm.take_rewind() == 2
    assert vm.take_rewind() is None
    assert vm.working_activity() == "retrying 1/20"

    # The next failure rewrites the same line instead of stacking another.
    vm.apply(
        _event(
            "model.request.retrying",
            {"attempt": 2, "max_attempts": 20, "reason": "timeout", "delay_s": 4},
            7,
        )
    )
    assert vm.conversation[-1] == "model> retrying 2/20 in 4s: timeout"
    assert len(vm.conversation) == 3

    # The model answering again ends the retry.
    vm.apply(_event("model.stream.delta", {"text": "Whole answer"}, 8))
    assert vm.retry_note == ""


def test_a_retry_line_stands_out_from_the_trace() -> None:
    line = render_conversation_line("model> retrying 1/20: 502")
    assert CODE_AI_PALETTE.amber in _styles(line)


async def test_the_screen_unmounts_what_a_retry_took_back(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app, config_path=tmp_path / "config.json")

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await fake_app.emit("user.message", {"text": "oi"})
        await fake_app.emit("status.changed", {"state": "CALLING_MODEL"})
        await fake_app.emit("model.request.started", {"step": 0})
        await fake_app.emit("model.thinking.delta", {"text": "some reasoning"})
        # Anything else closes the reasoning line, and it is mounted.
        await fake_app.emit("model.stream.delta", {"text": "Half"})
        await pilot.pause()
        mounted = len(terminal_app.query_one("#conversation").children)

        await fake_app.emit(
            "model.request.retrying",
            {"attempt": 1, "max_attempts": 20, "reason": "reset", "discarded": True},
        )
        await pilot.pause()

        # The folded reasoning block is gone from the scrollback.
        assert len(terminal_app.query_one("#conversation").children) == mounted - 1
        assert len(terminal_app.query(".thinking-block")) == 0


async def test_a_newer_release_shows_the_update_button(tmp_path, monkeypatch) -> None:
    from textual.widgets import Button

    from code_ai import updates
    from code_ai.ui.terminal import app as terminal_module

    release = updates.Release(
        version="9.9.9", tag="v9.9.9", name="", notes="", url="", prerelease=False, assets=()
    )

    async def newer(**_kwargs):
        return release

    monkeypatch.setattr(updates, "check", newer)
    monkeypatch.setattr(terminal_module, "_UPDATE_CHECK_DELAY_S", 0)
    terminal_app = create_terminal_app(
        FakeTerminalApplication(tmp_path), config_path=tmp_path / "config.json"
    )

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        button = terminal_app.query_one("#update-button", Button)
        assert button.display
        assert "v9.9.9" in str(button.label)
        assert any("v9.9.9 is out" in line for line in terminal_app.vm.conversation)

        # Running from source there is no binary to swap: it says how instead.
        await pilot.click("#update-button")
        await pilot.pause(0.3)
        assert any("runs from source" in line for line in terminal_app.vm.conversation)


async def test_update_says_where_to_set_the_repository(tmp_path, monkeypatch) -> None:
    from code_ai import updates

    # A frozen build, so /update gets past "runs from source".
    monkeypatch.setattr(updates, "running_binary", lambda: tmp_path / "code-ai.exe")
    terminal_app = create_terminal_app(
        FakeTerminalApplication(tmp_path), config_path=tmp_path / "config.json"
    )

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await terminal_app._install_update()
        await pilot.pause()
        assert f"update> {updates.UNSET_MESSAGE}" in terminal_app.vm.conversation


async def test_a_refused_lookup_says_why_and_clears_the_button(tmp_path, monkeypatch) -> None:
    from textual.widgets import Button

    from code_ai import updates

    async def refused(**_kwargs):
        raise updates.UpdateError("GitHub answered 404: sign in with git or gh")

    monkeypatch.setattr(updates, "running_binary", lambda: tmp_path / "code-ai.exe")
    monkeypatch.setattr(updates, "RELEASES_REPOSITORY", "team/code-ai")
    monkeypatch.setattr(updates, "check", refused)
    terminal_app = create_terminal_app(
        FakeTerminalApplication(tmp_path), config_path=tmp_path / "config.json"
    )

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await terminal_app._install_update()
        await pilot.pause()
        assert "update> GitHub answered 404: sign in with git or gh" in terminal_app.vm.conversation
        assert not terminal_app.query_one("#update-button", Button).display


async def test_no_release_no_button(tmp_path, monkeypatch) -> None:
    from textual.widgets import Button

    from code_ai.ui.terminal import app as terminal_module

    monkeypatch.setattr(terminal_module, "_UPDATE_CHECK_DELAY_S", 0)
    terminal_app = create_terminal_app(
        FakeTerminalApplication(tmp_path), config_path=tmp_path / "config.json"
    )

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        assert not terminal_app.query_one("#update-button", Button).display
