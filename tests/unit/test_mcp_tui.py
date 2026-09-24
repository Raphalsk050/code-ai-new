"""MCP in the terminal: the Doctor's MCP tab, the server-question form, /mcp commands.

The same things the settings panel does, driven through the real Textual
screens so a button that is never wired to its handler fails here.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from code_ai.app.commands import SessionCommands
from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.mcp import manager as manager_module
from code_ai.mcp.config import parse_import, quick_declaration
from code_ai.mcp.manager import McpManager
from code_ai.mcp.tools import McpTool
from code_ai.tools.registry import ToolRegistry
from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.doctor import DoctorModal
from code_ai.ui.terminal.doctor_mcp import McpDoctorPane
from code_ai.ui.terminal.mcp_question import McpQuestionModal
from tests.unit.test_terminal_ui import FakeTerminalApplication

SERVER = Path(__file__).with_name("mcp_fake_server.py")


def _server(*flags: str) -> dict[str, Any]:
    return {"command": sys.executable, "args": [str(SERVER), "--bare", *flags]}


class _App(FakeTerminalApplication):
    """The terminal fake, with a real tool registry and MCP manager behind it."""

    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        super().__init__(tmp_path)
        self.session.config = AppConfig.from_mapping(
            {"api_mode": "ollama", "workspace": str(tmp_path), "model": "m", **settings}
        )
        self.registry = ToolRegistry()
        self.orchestrator.tool_registry = self.registry
        self.saved: list[dict[str, Any]] = []

        async def persist(changes: dict[str, Any]) -> None:
            self.saved.append(changes)

        self.mcp = McpManager(
            config=self.session.config,
            registry=self.registry,
            event_bus=AsyncEventBus(),
            client_version="test",
            persist=persist,
        )


async def _settle(app: _App, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while any(s.status == "connecting" for s in app.mcp.servers.values()):
        assert asyncio.get_running_loop().time() < deadline, "servers never settled"
        await asyncio.sleep(0.05)


async def _open_mcp_tab(terminal_app, pilot) -> McpDoctorPane:
    terminal_app.query_one("#input", TextArea).value = "/doctor mcp"
    await pilot.press("enter")
    await pilot.pause(0.3)
    modal = terminal_app.screen
    assert isinstance(modal, DoctorModal)
    return modal.query_one(McpDoctorPane)


def _saved(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_the_mcp_tab_lists_servers_and_acts_on_them(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager_module, "_BACKOFF_S", 0.05)
    cfg_path = tmp_path / "config.json"
    fake_app = _App(tmp_path, mcp_servers={"fake": _server()})
    terminal_app = create_terminal_app(fake_app, config_path=cfg_path)

    async with terminal_app.run_test(size=(120, 160)) as pilot:
        await fake_app.mcp.start()
        await _settle(fake_app)
        pane = await _open_mcp_tab(terminal_app, pilot)
        await pane.refresh_servers(force=True)
        await pilot.pause(0.1)

        head = str(pane.query(".doctor-mcp-head").first().render())
        assert "fake" in head and "connected" in head
        for action in ("reconnect", "toggle", "edit", "remove"):
            pane.query_one(f"#doctor-mcp-{action}-fake", Button)

        # A tool switch saves disabled_tools and takes the tool out at once.
        box = pane.query_one("#doctor-mcp-tool-mcp__fake__echo", Checkbox)
        box.value = False
        await pilot.pause(0.2)
        assert "mcp__fake__echo" in _saved(cfg_path)["disabled_tools"]
        assert not fake_app.registry.has("mcp__fake__echo")

        # Switching the server off goes through the manager and its config.
        pane.query_one("#doctor-mcp-toggle-fake", Button).press()
        for _ in range(100):
            if fake_app.mcp.servers["fake"].status == "disabled":
                break
            await pilot.pause(0.05)
        assert fake_app.mcp.servers["fake"].status == "disabled"
        assert fake_app.saved[-1]["mcp_servers"]["fake"]["enabled"] is False

        # Remove asks for a second press.
        pane.query_one("#doctor-mcp-remove-fake", Button).press()
        await pilot.pause(0.2)
        assert "fake" in fake_app.mcp.servers
        pane.query_one("#doctor-mcp-remove-fake", Button).press()
        for _ in range(100):
            if "fake" not in fake_app.mcp.servers:
                break
            await pilot.pause(0.05)
        assert "fake" not in fake_app.mcp.servers
        await fake_app.mcp.close()


async def test_the_form_adds_and_the_json_box_imports(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager_module, "_BACKOFF_S", 0.05)
    fake_app = _App(tmp_path)
    terminal_app = create_terminal_app(fake_app, config_path=tmp_path / "config.json")

    async with terminal_app.run_test(size=(120, 160)) as pilot:
        await fake_app.mcp.start()
        pane = await _open_mcp_tab(terminal_app, pilot)

        pane.query_one("#doctor-mcp-name", Input).value = "added"
        command = " ".join(f'"{part}"' for part in [sys.executable, str(SERVER), "--bare"])
        pane.query_one("#doctor-mcp-target", Input).value = command
        pane.query_one("#doctor-mcp-extras", Input).value = "LOG_LEVEL=debug"
        pane.query_one("#doctor-mcp-add", Button).press()
        for _ in range(100):
            if "added" in fake_app.mcp.servers:
                break
            await pilot.pause(0.05)
        spec = fake_app.mcp.servers["added"].config
        assert spec.command == sys.executable
        assert spec.args == [str(SERVER), "--bare"]
        assert spec.env == {"LOG_LEVEL": "debug"}
        assert pane.query_one("#doctor-mcp-name", Input).value == "", "the form is cleared"

        pane.query_one("#doctor-mcp-json", TextArea).text = json.dumps(
            {"mcpServers": {"remote": {"type": "http", "url": "https://mcp.example.com/mcp"}}}
        )
        pane.query_one("#doctor-mcp-import", Button).press()
        for _ in range(100):
            if "remote" in fake_app.mcp.servers:
                break
            await pilot.pause(0.05)
        assert fake_app.mcp.servers["remote"].config.transport == "http"
        await fake_app.mcp.close()


async def test_switching_mcp_off_stops_every_server(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app = _App(tmp_path, mcp_servers={"fake": _server()})
    terminal_app = create_terminal_app(fake_app, config_path=cfg_path)

    async with terminal_app.run_test(size=(120, 160)) as pilot:
        await fake_app.mcp.start()
        await _settle(fake_app)
        pane = await _open_mcp_tab(terminal_app, pilot)

        pane.query_one("#doctor-mcp-enabled", Checkbox).value = False
        for _ in range(100):
            if fake_app.mcp.servers["fake"].status == "disabled":
                break
            await pilot.pause(0.05)

        assert _saved(cfg_path)["mcp_enabled"] is False
        assert fake_app.mcp.servers["fake"].status == "disabled"
        assert not fake_app.registry.has("mcp__fake__echo")


async def test_the_tools_tab_leaves_mcp_switches_alone(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app = _App(tmp_path)
    fake_app.registry.register(McpTool(manager=None, server="s", spec={"name": "x"}, trusted=False))
    fake_app.registry.set_disabled(["mcp__s__x"])
    terminal_app = create_terminal_app(fake_app, config_path=cfg_path)

    async with terminal_app.run_test(size=(120, 160)) as pilot:
        terminal_app.query_one("#input", TextArea).value = "/doctor tools"
        await pilot.press("enter")
        await pilot.pause(0.3)
        modal = terminal_app.screen

        assert not modal.query("#doctor-tool-mcp__s__x"), "MCP tools live on the MCP tab"
        modal.query_one("#doctor-tools-enable-all", Button).press()
        await pilot.pause(0.2)

        assert _saved(cfg_path)["disabled_tools"] == ["mcp__s__x"]


class _Broker:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, Any]] = []

    def answer(self, request_id: str, action: str, content: Any = None) -> str:
        self.calls.append((request_id, action, content))
        return self.answers.pop(0) if self.answers else ""


QUESTION = {
    "id": "mcp-ask-1",
    "server": "github",
    "message": "Where should the issue go?",
    "fields": [
        {"name": "org name", "title": "Organisation", "type": "string", "required": True},
        {"name": "private", "title": "Private", "type": "boolean", "required": False},
        {"name": "label", "title": "Label", "type": "string", "enum": ["bug", "feature"]},
        {"name": "count", "title": "Count", "type": "integer", "required": False},
    ],
}


async def test_a_server_question_opens_a_form_and_sends_typed_answers(tmp_path) -> None:
    fake_app = _App(tmp_path)
    broker = _Broker("Organisation must be lowercase.", "")
    fake_app.mcp.elicitation = broker
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 60)) as pilot:
        await fake_app.emit("mcp.elicitation.requested", QUESTION)
        await pilot.pause(0.3)
        modal = terminal_app.screen
        assert isinstance(modal, McpQuestionModal)
        assert "github asks" in str(modal.query_one("#mcp-question-title", Static).render())

        modal.query_one("#mcp-q-0", Input).value = "Code-AI"
        modal.query_one("#mcp-q-1", Checkbox).value = True
        modal.query_one("#mcp-q-2", Select).value = "bug"
        modal.query_one("#mcp-q-3", Input).value = "3"
        modal.query_one("#mcp-question-send", Button).press()
        await pilot.pause(0.2)

        # Refused: the form stays, saying why.
        assert terminal_app.screen is modal
        assert "lowercase" in str(modal.query_one("#mcp-question-error", Static).render())

        modal.query_one("#mcp-q-0", Input).value = "code-ai"
        modal.query_one("#mcp-question-send", Button).press()
        await pilot.pause(0.2)

        assert not isinstance(terminal_app.screen, McpQuestionModal)
        assert broker.calls[-1] == (
            "mcp-ask-1",
            "accept",
            {"org name": "code-ai", "private": True, "label": "bug", "count": "3"},
        )
        # The resolved event that follows must not pop the screen underneath.
        await fake_app.emit("mcp.elicitation.resolved", {"id": "mcp-ask-1", "action": "accept"})
        await pilot.pause(0.1)
        assert terminal_app.screen is not None


async def test_declining_and_an_answer_given_elsewhere_close_the_form(tmp_path) -> None:
    fake_app = _App(tmp_path)
    broker = _Broker()
    fake_app.mcp.elicitation = broker
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(120, 60)) as pilot:
        await fake_app.emit("mcp.elicitation.requested", QUESTION)
        await pilot.pause(0.3)
        terminal_app.screen.query_one("#mcp-question-decline", Button).press()
        await pilot.pause(0.2)
        assert broker.calls[-1][1] == "decline"
        assert not isinstance(terminal_app.screen, McpQuestionModal)

        await fake_app.emit("mcp.elicitation.requested", {**QUESTION, "id": "mcp-ask-2"})
        await pilot.pause(0.3)
        assert isinstance(terminal_app.screen, McpQuestionModal)
        await fake_app.emit("mcp.elicitation.resolved", {"id": "mcp-ask-2", "action": "cancel"})
        await pilot.pause(0.2)
        assert not isinstance(terminal_app.screen, McpQuestionModal)


async def test_mcp_add_and_remove_commands(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager_module, "_BACKOFF_S", 0.05)
    fake_app = _App(tmp_path)
    commands = SessionCommands(fake_app)  # type: ignore[arg-type]
    await fake_app.mcp.start()
    try:
        added = await commands.mcp_add("docs https://mcp.example.com/mcp")
        assert added.ok, added.message
        assert fake_app.mcp.servers["docs"].config.url == "https://mcp.example.com/mcp"
        assert not (await commands.mcp_add("lonely")).ok

        removed = await commands.mcp_action("remove", "docs")
        assert removed.message == "docs: removed."
        assert "docs" not in fake_app.mcp.servers
        assert "/doctor mcp" in commands.mcp_status().message or not fake_app.mcp.servers
    finally:
        await fake_app.mcp.close()


def test_one_line_declarations_and_pasted_json() -> None:
    assert quick_declaration("https://x/mcp", "Authorization=Bearer ${T}") == {
        "type": "http",
        "url": "https://x/mcp",
        "headers": {"Authorization": "Bearer ${T}"},
    }
    assert quick_declaration("https://x/sse", legacy_sse=True)["type"] == "sse"
    stdio = quick_declaration('npx -y "@scope/server name"', "A=1; B = two")
    assert stdio == {
        "command": "npx",
        "args": ["-y", "@scope/server name"],
        "env": {"A": "1", "B": "two"},
    }

    servers, problem = parse_import('{"servers": {"my server": {"url": "https://a"}}, "x": 1}')
    assert problem == ""
    assert servers == [("my_server", {"url": "https://a"})]
    assert "Not valid JSON" in parse_import("{")[1]
    assert "command or a url" in parse_import('{"mcpServers": {"a": {}}}')[1]


def test_a_server_from_another_agents_user_config_is_not_called_the_projects() -> None:
    server = {
        "source": "claude-code",
        "source_path": "/home/me/.claude.json",
        "source_label": "~/.claude.json",
        "user_level": True,
        "missing_env": [],
        "error": "",
        "status": "needs_approval",
    }

    lines = [line.plain for line in McpDoctorPane._detail_lines(server)]

    assert lines == ["Set up for claude-code in ~/.claude.json"]
    server["user_level"] = False
    assert McpDoctorPane._detail_lines(server)[0].plain.startswith("Declared by the project")
