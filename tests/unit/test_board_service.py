"""The board as the front ends reach it: the bridge methods, /board, the agent's
tools under plan mode, and how the planner's and verifier's answers are read.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from code_ai.app.board import BoardService
from code_ai.app.commands import SessionCommands
from code_ai.bridge.server import BridgeServer
from code_ai.core.board import prompts
from code_ai.core.board.models import Board
from code_ai.core.planning.models import PlannerMode, PlanningPhase
from code_ai.core.planning.policy import PlannerToolPolicy
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.board import BoardEditTool, BoardPlanTool, BoardSubmitTool, BoardViewTool
from code_ai.tools.registry import ToolRegistry


def service(tmp_path, runtime=None) -> BoardService:
    return BoardService(
        path=tmp_path / "board.json",
        event_bus=AsyncEventBus(),
        runtime=lambda: runtime,
        max_parallel=lambda: 4,
    )


class _App:
    def __init__(self, board: BoardService) -> None:
        self.board = board
        self.desktop_avatar = SimpleNamespace(attach=lambda app: None)
        self.orchestrator = SimpleNamespace(approval_gateway=None)


async def call(server: BridgeServer, out: io.StringIO, method: str, params: dict) -> dict:
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    )
    line = out.getvalue().splitlines()[-1]
    return json.loads(line)["result"]


async def test_the_bridge_reads_edits_and_refuses_on_the_board(tmp_path) -> None:
    out = io.StringIO()
    board = service(tmp_path)
    server = BridgeServer(_App(board), stdin=io.StringIO(), stdout=out)

    got = await call(server, out, "boardGet", {})
    assert [lane["id"] for lane in got["board"]["lanes"]] == ["todo", "in_progress", "done"]

    edited = await call(
        server,
        out,
        "boardEdit",
        {"operations": [{"op": "add_task", "title": "Search", "subtasks": ["Index"]}]},
    )
    assert edited["ok"] and edited["created"] == ["T1"]
    assert edited["board"]["tasks"][0]["subtasks"][0]["id"] == "T1.1"
    assert (
        json.loads((tmp_path / "board.json").read_text(encoding="utf-8"))["tasks"][0]["title"]
        == "Search"
    )

    refused = await call(
        server, out, "boardEdit", {"operations": [{"op": "remove_lane", "lane": "done"}]}
    )
    assert refused["ok"] is False and "renamed only" in refused["message"]

    # No sub-agent runtime: Play says why instead of pretending.
    played = await call(server, out, "boardPlay", {"tasks": ["T1"]})
    assert played["ok"] is False and "sub-agents" in played["message"]
    stopped = await call(server, out, "boardStop", {})
    assert stopped["ok"] is False


async def test_board_slash_subcommands(tmp_path) -> None:
    commands = SessionCommands(_App(service(tmp_path)))
    added = await commands.board_command("add Dark mode")
    assert added.ok and "T1" in added.message
    lane = await commands.board_command("lane add Review")
    assert lane.ok
    assert [item.title for item in commands._board().board.lanes][2] == "Review"
    removed = await commands.board_command("lane remove Review")
    assert removed.ok
    gone = await commands.board_command("remove T1")
    assert gone.ok and commands._board().board.tasks == []
    usage = await commands.board_command("dance")
    assert not usage.ok and "/board add" in usage.message


async def test_the_board_tools_are_allowed_in_plan_mode(tmp_path) -> None:
    board = service(tmp_path)
    registry = ToolRegistry()
    for tool in (
        BoardViewTool(lambda: board),
        BoardEditTool(lambda: board),
        BoardPlanTool(lambda: board),
        BoardSubmitTool(lambda: board),
    ):
        registry.register(tool)
    profile = SimpleNamespace(
        requires_workspace_mutation=True,
        requires_local_context=True,
        requires_external_information=False,
        allows_web_first=False,
        intent=None,
    )
    allowed = PlannerToolPolicy().allowed_tool_names(
        registry=registry,
        profile=profile,
        mode=PlannerMode.PLAN,
        phase=PlanningPhase.DISCOVER_LOCAL,
        current_step=None,
    )
    assert {"board_view", "board_edit", "board_plan", "board_submit"} <= allowed


def test_a_plan_is_read_fenced_or_after_prose() -> None:
    fenced = (
        'Sure.\n```json\n{"subtasks": [{"title": "A"}, {"title": "B", "depends_on": [1]}]}\n```'
    )
    plan = prompts.parse_plan(fenced)
    assert plan is not None and [sub.title for sub in plan.subtasks] == ["A", "B"]
    assert plan.subtasks[1].depends_on == [1]
    bare = 'I read the code. {"subtasks": ["only one"], "criteria": ["works"]} Done.'
    plan = prompts.parse_plan(bare)
    assert plan is not None and plan.subtasks[0].title == "only one" and plan.criteria == ["works"]
    assert prompts.parse_plan("no json here") is None
    assert prompts.parse_plan('{"subtasks": []}') is None


def test_a_verdict_passes_only_when_every_item_was_seen() -> None:
    board = Board.empty()
    from code_ai.core.board import apply_operations

    board, _ = apply_operations(
        board,
        [
            {
                "op": "add_task",
                "title": "API",
                "subtasks": [{"title": "x", "criteria": ["200"]}],
                "checklist": ["docs"],
                "criteria": ["fast"],
            }
        ],
    )
    task = board.task("T1")
    ids = {
        item.text: item.id for item in [*task.criteria, *task.checklist, *task.subtasks[0].criteria]
    }
    passing = prompts.parse_verdict(
        json.dumps(
            {
                "criteria": {ids["fast"]: {"pass": True}, ids["200"]: True},
                "checklist": {ids["docs"]: True},
            }
        )
    )
    assert passing.passed(task)
    missing = prompts.parse_verdict(json.dumps({"criteria": {ids["fast"]: True}}))
    assert not missing.passed(task)
    assert "200: not checked" in missing.findings(task)
    unreadable = prompts.parse_verdict("Looks fine to me")
    assert not unreadable.passed(task) and unreadable.findings(task) == "Looks fine to me"
    # The verifier sees every id it has to judge.
    prompt = prompts.verifier_prompt(task)
    assert all(f"{item_id}:" in prompt for item_id in ids.values())
