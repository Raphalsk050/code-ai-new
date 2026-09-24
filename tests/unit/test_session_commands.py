from __future__ import annotations

import asyncio
import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_ai.app.commands import SessionCommands
from code_ai.app.slash_catalog import SLASH_COMMANDS, bridge_supports, catalog_payload
from code_ai.bridge.server import BridgeServer
from code_ai.core.errors import ConfigurationError, GoalStateError, TerminalSessionError


class _Bus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, payload: dict, source: str = "") -> None:
        self.emitted.append((event_type, payload))


class _Manager:
    """A PTY manager that records what it was told to do."""

    def __init__(self, *, session_id: str | None = "sess-1234abcd") -> None:
        self.session_id = session_id
        self.text: list[str] = []
        self.controls: list[str] = []
        self.enters = 0
        self.terminated: list[str] = []
        self.created: dict | None = None
        self.fail_with: Exception | None = None

    def create(self, *, cwd, command=None, env=None) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.created = {"cwd": cwd, "command": command}
        self.session_id = "sess-1234abcd"
        return self.session_id

    def latest_session_id(self) -> str | None:
        return self.session_id

    def send_text(self, session_id: str, text: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.text.append(text)

    def send_enter(self, session_id: str) -> None:
        self.enters += 1

    def send_control(self, session_id: str, key: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.controls.append(key)

    def terminate(self, session_id: str) -> None:
        self.terminated.append(session_id)

    def read_screen(self, session_id: str) -> dict:
        return {"session_id": session_id, "screen": "$ ls\n", "rows": 24, "columns": 80,
                "closed": False}


class _Report:
    def summary(self) -> str:
        return "12 files"


class _Workflow:
    def __init__(self, name: str) -> None:
        self.name = name
        self.command = f"/{name}"
        self.description = f"Run {name}"
        self.origin = "code-ai"
        self.path = f"/wf/{name}.md"


class _Skill:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"Use {name}"
        self.origin = "code-ai"
        self.path = f"/skills/{name}/SKILL.md"


class _Workflows:
    def __init__(self, records: list[_Workflow]) -> None:
        self._records = records

    def load(self) -> list[_Workflow]:
        return list(self._records)

    def find(self, name: str) -> _Workflow | None:
        wanted = name.lstrip("/").casefold()
        for record in self._records:
            if record.name.casefold() == wanted:
                return record
        return None


class _App:
    """Only the surface SessionCommands touches."""

    def __init__(self, *, manager: _Manager | None = None) -> None:
        self.event_bus = _Bus()
        self.session = SimpleNamespace(config=SimpleNamespace(workspace=Path("/ws")))
        self.terminal_manager = manager
        self.plan = {"mode": "auto", "progress": "1/3"}
        self.replan_reason: str | None = None
        self.plan_started = True
        self.goal: dict = {"status": "draft", "criteria": [], "started": False}
        self.goal_error: Exception | None = None
        self.index_error: Exception | None = None
        self.index_calls: list[tuple[bool, str]] = []
        self.orchestrator = SimpleNamespace(approval_gateway=None)
        self.workflows = _Workflows([_Workflow("deploy")])
        self.skill_sources = ()
        self.submitted: list[str] = []

    async def submit_user_message(self, text: str, **kwargs: object) -> None:
        self.submitted.append(text)

    # planner
    def get_plan_snapshot(self) -> dict:
        return self.plan

    async def request_replan(self, reason: str | None = None) -> str:
        self.replan_reason = reason
        return "replan queued"

    async def start_plan_execution(self) -> bool:
        return self.plan_started

    # goal
    async def define_goal(self, objective: str) -> dict:
        if self.goal_error is not None:
            raise self.goal_error
        self.goal = {"status": "draft", "objective": objective, "started": False}
        return self.goal

    async def start_goal(self) -> dict:
        if self.goal_error is not None:
            raise self.goal_error
        self.goal = {**self.goal, "status": "active"}
        return self.goal

    async def stop_goal(self) -> dict:
        if self.goal_error is not None:
            raise self.goal_error
        self.goal = {**self.goal, "status": "stopped"}
        return self.goal

    def goal_snapshot(self) -> dict:
        return self.goal

    # index
    async def refresh_code_index(self, *, full: bool = False, subtree: str = "") -> _Report:
        if self.index_error is not None:
            raise self.index_error
        self.index_calls.append((full, subtree))
        return _Report()

    def code_index_status(self):
        return SimpleNamespace(
            files=3, chunks=9, embedded_chunks=9, embedding_model="e5",
            lexical_engine="sqlite", last_refresh="now", refreshing=False,
            pending_touches=0, last_error="", empty=False,
        )

    # bridge lifecycle
    def subscribe(self, fn):
        return fn

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


# -- terminal --------------------------------------------------------------


async def test_terminal_start_emits_the_screen_every_ui_reads() -> None:
    app = _App(manager=_Manager(session_id=None))
    result = await SessionCommands(app).terminal_start("npm run watch")

    assert result.ok is True
    assert app.terminal_manager.created == {"cwd": Path("/ws"), "command": "npm run watch"}
    assert [name for name, _ in app.event_bus.emitted] == ["terminal.screen.updated"]


async def test_terminal_write_types_and_presses_enter() -> None:
    manager = _Manager()
    result = await SessionCommands(_App(manager=manager)).terminal_write("pytest -q")

    assert result.ok is True
    assert manager.text == ["pytest -q"]
    assert manager.enters == 1


async def test_terminal_action_without_a_session_is_a_failure_not_an_exception() -> None:
    result = await SessionCommands(_App(manager=_Manager(session_id=None))).terminal_control("c")

    assert result.ok is False
    assert "No terminal session" in result.message


async def test_terminal_session_error_becomes_a_failed_result() -> None:
    manager = _Manager()
    manager.fail_with = TerminalSessionError("pty is gone")
    result = await SessionCommands(_App(manager=manager)).terminal_write("ls")

    assert result.ok is False
    assert result.message == "pty is gone"


async def test_terminal_kill_marks_the_screen_closed() -> None:
    manager = _Manager()
    app = _App(manager=manager)
    result = await SessionCommands(app).terminal_kill()

    assert result.ok is True
    assert manager.terminated == ["sess-1234abcd"]
    (_, payload), = app.event_bus.emitted
    assert payload["closed"] is True


async def test_terminal_without_a_backend_reports_it() -> None:
    result = await SessionCommands(_App(manager=None)).terminal_start()

    assert result.ok is False
    assert "terminal backend" in result.message


# -- goal ------------------------------------------------------------------


async def test_goal_define_requires_an_objective() -> None:
    result = await SessionCommands(_App()).goal_define("   ")

    assert result.ok is False


async def test_goal_define_returns_the_snapshot() -> None:
    result = await SessionCommands(_App()).goal_define("cobrir o ranking com testes")

    assert result.ok is True
    assert result.data["goal"]["objective"] == "cobrir o ranking com testes"


async def test_goal_state_error_is_reported_not_raised() -> None:
    app = _App()
    app.goal_error = GoalStateError("The goal loop is already running.")
    result = await SessionCommands(app).goal_start()

    assert result.ok is False
    assert "already running" in result.message


# -- plan and index --------------------------------------------------------


async def test_replan_carries_the_plan_snapshot_back() -> None:
    app = _App()
    result = await SessionCommands(app).replan("os testes quebraram")

    assert result.ok is True
    assert app.replan_reason == "os testes quebraram"
    assert result.data["plan"] == app.plan


async def test_start_plan_without_a_waiting_plan_fails() -> None:
    app = _App()
    app.plan_started = False
    result = await SessionCommands(app).start_plan()

    assert result.ok is False


async def test_index_refresh_reports_the_scope() -> None:
    app = _App()
    result = await SessionCommands(app).index_refresh(full=True, subtree="src")

    assert app.index_calls == [(True, "src")]
    assert "Rebuilt" in result.message and "(src)" in result.message


async def test_index_refresh_configuration_error_is_a_failed_result() -> None:
    app = _App()
    app.index_error = ConfigurationError("index disabled")
    result = await SessionCommands(app).index_refresh()

    assert result.ok is False
    assert result.message == "index disabled"


# -- slash catalog ---------------------------------------------------------


def test_catalog_marks_what_the_bridge_can_run() -> None:
    payload = {entry["command"]: entry for entry in catalog_payload()}

    assert payload["/compact"]["available"] is True
    assert payload["/goal start"]["available"] is True
    assert payload["/quit"]["available"] is False
    assert payload["/doctor"]["available"] is False


def test_catalog_entries_carry_a_completion() -> None:
    for entry in catalog_payload():
        assert entry["completion"], entry["command"]


def test_bridge_supports_matches_the_command_word_not_the_argument() -> None:
    assert bridge_supports("/goal <objetivo>") is True
    assert bridge_supports("/term ctrl <c|d|z>") is True
    assert bridge_supports("/config model <name>") is False


def test_catalog_is_the_terminals_catalog() -> None:
    # One list, two front ends: the terminal imports it from here, so a command
    # added for the TUI shows up in the extension without a second edit.
    assert len(catalog_payload()) == len(SLASH_COMMANDS)


# -- bridge dispatch -------------------------------------------------------


def _server(app: _App) -> tuple[BridgeServer, io.StringIO]:
    out = io.StringIO()
    return BridgeServer(app, stdin=io.StringIO(), stdout=out), out


def _messages(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


async def test_bridge_exposes_the_slash_catalog() -> None:
    server, out = _server(_App())
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "listSlashCommands", "params": {}})
    )

    (response,) = _messages(out)
    assert any(c["command"] == "/goal start" for c in response["result"]["commands"])


async def test_bridge_terminal_write_reaches_the_manager() -> None:
    manager = _Manager()
    app = _App(manager=manager)
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "terminalWrite",
                    "params": {"text": "ls -la"}})
    )

    assert manager.text == ["ls -la"]
    (response,) = _messages(out)
    assert response["result"]["ok"] is True


async def test_bridge_goal_stop_runs_off_the_read_loop() -> None:
    # Stopping cancels the in-flight turn, which can await the model; holding the
    # read loop for it would freeze approvals arriving on the same pipe.
    assert "goalStop" in BridgeServer._CONCURRENT_METHODS
    assert "indexRefresh" in BridgeServer._CONCURRENT_METHODS


async def test_bridge_reports_a_command_failure_as_a_result_not_an_error() -> None:
    app = _App(manager=_Manager(session_id=None))
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "terminalKill", "params": {}})
    )

    (response,) = _messages(out)
    assert "error" not in response
    assert response["result"]["ok"] is False


@pytest.mark.parametrize(
    "method",
    [
        "planSnapshot",
        "replan",
        "startPlan",
        "goalDefine",
        "goalStart",
        "goalStop",
        "goalStatus",
        "terminalStart",
        "terminalWrite",
        "terminalControl",
        "terminalKill",
        "terminalStatus",
        "indexRefresh",
        "indexStatus",
        "listSlashCommands",
    ],
)
def test_every_new_method_is_registered(method: str) -> None:
    handler = BridgeServer._HANDLERS.get(method)
    assert handler is not None
    assert inspect.iscoroutinefunction(handler)


# -- workflows and skills --------------------------------------------------


def test_assets_list_the_user_s_own_commands(monkeypatch) -> None:
    app = _App()
    result = SessionCommands(app).list_assets()

    assert result.ok is True
    assert result.data["workflows"][0]["command"] == "/deploy"
    assert result.data["skills"] == []


def test_resolving_a_workflow_returns_the_prompt_not_a_turn() -> None:
    app = _App()
    result = SessionCommands(app).resolve_asset("/deploy", "version 0.2")

    assert result.ok is True
    assert result.data["kind"] == "workflow"
    # Starting the turn is the caller's business: nothing was submitted here.
    assert app.submitted == []
    assert result.data["prompt"]


def test_resolving_an_unknown_name_is_a_miss_not_an_error() -> None:
    result = SessionCommands(_App()).resolve_asset("/nao-existe")

    assert result.ok is False
    assert "nao-existe" in result.message


def test_resolving_nothing_is_rejected() -> None:
    assert SessionCommands(_App()).resolve_asset("/").ok is False


async def test_the_catalog_includes_assets_marked_by_kind() -> None:
    from code_ai.app.slash_catalog import catalog_payload

    entries = catalog_payload(
        workflows=[{"name": "deploy", "command": "/deploy", "description": "Publica"}],
        skills=[{"name": "tdd", "command": "/tdd", "description": "Vermelho, verde"}],
    )
    by_command = {entry["command"]: entry for entry in entries}
    assert by_command["/deploy"]["kind"] == "workflow"
    assert by_command["/tdd"]["kind"] == "skill"
    assert by_command["/compact"]["kind"] == "command"
    assert by_command["/deploy"]["available"] is True


async def test_bridge_run_asset_starts_the_turn_in_the_background() -> None:
    app = _App()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "runAsset",
                    "params": {"token": "/deploy", "argument": "0.2"}})
    )
    await asyncio.gather(*server._turn_tasks)

    (response,) = _messages(out)
    assert response["result"]["ok"] is True
    assert app.submitted, "the workflow prompt should have been submitted as a turn"


async def test_bridge_run_asset_reports_a_miss() -> None:
    server, out = _server(_App())
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 10, "method": "runAsset",
                    "params": {"token": "/nada"}})
    )
    await asyncio.gather(*server._turn_tasks)

    (response,) = _messages(out)
    assert response["result"]["ok"] is False
