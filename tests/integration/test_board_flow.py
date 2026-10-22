"""The task board end to end: planners, parallel workers and the verifier are real
sub-agents of a real application; only the model is scripted.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)

PLAN = {
    "subtasks": [
        {"title": "Schema", "description": "models.py", "criteria": ["the table exists"]},
        {"title": "Endpoint", "description": "api.py"},
        {"title": "Tests", "description": "tests/", "depends_on": [1, 2]},
    ],
    "checklist": ["docs mention it"],
    "criteria": ["GET /items answers 200"],
}


def _task_of(request: ModelRequest) -> str:
    return "\n".join(
        str(message.content or "") for message in request.messages if message.role == "user"
    )


def passing_verdict(prompt: str) -> str:
    criteria = re.findall(r"criterion (v\d+):", prompt)
    checks = re.findall(r"check (c\d+):", prompt)
    return json.dumps(
        {
            "criteria": {item: {"pass": True, "note": "seen"} for item in criteria},
            "checklist": dict.fromkeys(checks, True),
            "summary": "All good.",
        }
    )


def failing_verdict(prompt: str) -> str:
    criteria = re.findall(r"criterion (v\d+):", prompt)
    return json.dumps(
        {"criteria": {item: {"pass": False, "note": "returns 500"} for item in criteria}}
    )


class BoardProvider:
    """Plans, works and judges by what each sub-agent was asked, counting overlap."""

    def __init__(
        self,
        *,
        plan: dict | None = None,
        verdicts: list[Callable[[str], str]] | None = None,
        work_s: float = 0.05,
        hold: asyncio.Event | None = None,
    ) -> None:
        self.plan = plan or PLAN
        self.verdicts = list(verdicts or [])
        self.work_s = work_s
        self.hold = hold
        self.active = 0
        self.peak = 0
        self.worked: list[str] = []
        self.started: list[str] = []
        self.saw_images = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def _answer(self, request: ModelRequest) -> str:
        prompt = _task_of(request)
        self.saw_images += sum(len(message.images or []) for message in request.messages)
        if "Break the task below into sub-tasks" in prompt:
            return "Here it is:\n```json\n" + json.dumps(self.plan) + "\n```"
        if "Judge whether the work below is done" in prompt:
            verdict = self.verdicts.pop(0) if self.verdicts else passing_verdict
            return verdict(prompt)
        piece = re.search(r"Your piece, (T\d+\.\d+)", prompt)
        name = piece.group(1) if piece else ("fix" if "is not done yet" in prompt else "whole")
        self.started.append(name)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.hold is not None:
                await self.hold.wait()
            await asyncio.sleep(self.work_s)
        finally:
            self.active -= 1
        self.worked.append(name)
        return f"Changed files for {name}; tests pass."

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        text = await self._answer(request)
        yield ProviderEvent(
            kind="completed", response=ModelResponse(text=text, finish_reason=FinishReason.STOP)
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=await self._answer(request), finish_reason=FinishReason.STOP)

    async def close(self) -> None:
        return None


def app_for(tmp_path, provider):
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "main-model",
            "permission_mode": "bypass",
            "learn": False,
        }
    )
    return build_application(config=config, provider=provider)


class Collector:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    async def __call__(self, envelope) -> None:
        self.rows.append((envelope.event_type, envelope.payload))

    def of(self, event_type: str) -> list[dict]:
        return [payload for name, payload in self.rows if name == event_type]

    def moves(self) -> list[dict]:
        return [move for payload in self.of("board.changed") for move in payload["moved"]]


async def wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


async def started_app(tmp_path, provider):
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()
    return app, collector


async def finish_run(app) -> None:
    await wait_for(lambda: not app.board.running and not app.board.planning)


async def test_planning_breaks_tasks_down_in_parallel_and_asks_for_review(tmp_path) -> None:
    app, events = await started_app(tmp_path, BoardProvider())
    await app.board.apply(
        [{"op": "add_task", "title": "Items API"}, {"op": "add_task", "title": "Items page"}]
    )

    result = await app.board.plan()
    assert result["ok"] and result["tasks"] == ["T1", "T2"]
    await finish_run(app)

    task = app.board.board.task("T1")
    assert task.status == "ready"
    assert [sub.title for sub in task.subtasks] == ["Schema", "Endpoint", "Tests"]
    assert task.subtasks[2].depends_on == ["T1.1", "T1.2"]
    assert task.criteria[0].text == "GET /items answers 200"
    assert task.checklist[0].text == "docs mention it"
    assert app.board.board.task("T2").status == "ready"
    # Planning never implements: nothing moved, and the user is asked to confirm.
    assert events.moves() == []
    review = events.of("board.review.requested")[-1]
    assert sorted(review["tasks"]) == ["T1", "T2"]
    await app.close()


async def test_play_runs_subtasks_in_parallel_then_checks_and_lands_in_done(tmp_path) -> None:
    provider = BoardProvider(work_s=0.15)
    app, events = await started_app(tmp_path, provider)
    await app.board.apply(
        [
            {
                "op": "add_task",
                "title": "Items API",
                "subtasks": [
                    {"title": "Schema", "criteria": ["the table exists"]},
                    {"title": "Endpoint", "checklist": ["logs requests"]},
                    {"title": "Tests", "depends_on": [1, 2]},
                ],
                "criteria": ["GET /items answers 200"],
            }
        ]
    )

    result = await app.board.play()
    assert result["ok"] and result["tasks"] == ["T1"]
    await finish_run(app)

    task = app.board.board.task("T1")
    assert task.status == "done"
    assert task.lane == "done"
    # The two independent pieces overlapped; Tests waited for both.
    assert provider.peak >= 2
    assert provider.worked.index("T1.3") > max(
        provider.worked.index("T1.1"), provider.worked.index("T1.2")
    )
    assert all(sub.status == "done" and sub.agent and sub.report for sub in task.subtasks)
    assert all(item.status == "passed" for item in [*task.criteria, *task.subtasks[0].criteria])
    assert task.subtasks[1].checklist[0].done is True
    # The card went To Do -> In Progress -> Done, which the panel animates.
    assert events.moves() == [
        {"task": "T1", "from": "todo", "to": "in_progress"},
        {"task": "T1", "from": "in_progress", "to": "done"},
    ]
    completed = events.of("board.run.completed")[-1]
    assert completed["status"] == "completed" and completed["done"] == ["T1"]
    assert events.of("board.activity")
    # Saved as it went.
    saved = json.loads((app.board._path).read_text(encoding="utf-8"))
    assert saved["tasks"][0]["status"] == "done"
    await app.close()


async def test_independent_tasks_run_side_by_side_and_dependent_ones_wait(tmp_path) -> None:
    provider = BoardProvider(work_s=0.1)
    app, _ = await started_app(tmp_path, provider)
    await app.board.apply(
        [
            {"op": "add_task", "title": "A", "subtasks": ["a1"]},
            {"op": "add_task", "title": "B", "subtasks": ["b1"]},
            {"op": "add_task", "title": "C", "subtasks": ["c1"], "depends_on": ["T1"]},
        ]
    )
    await app.board.play()
    await finish_run(app)
    assert {task.status for task in app.board.board.tasks} == {"done"}
    assert provider.peak >= 2
    assert provider.started.index("T3.1") > provider.worked.index("T1.1")
    await app.close()


async def test_a_failed_check_gets_one_fix_round(tmp_path) -> None:
    provider = BoardProvider(verdicts=[failing_verdict, passing_verdict])
    app, _ = await started_app(tmp_path, provider)
    await app.board.apply(
        [{"op": "add_task", "title": "API", "subtasks": ["x"], "criteria": ["answers 200"]}]
    )
    await app.board.play()
    await finish_run(app)
    task = app.board.board.task("T1")
    assert task.status == "done"
    assert "fix" in provider.worked
    assert task.criteria[0].status == "passed"
    await app.close()


async def test_a_task_that_keeps_failing_its_check_stays_in_progress(tmp_path) -> None:
    provider = BoardProvider(verdicts=[failing_verdict, failing_verdict])
    app, events = await started_app(tmp_path, provider)
    await app.board.apply(
        [{"op": "add_task", "title": "API", "subtasks": ["x"], "criteria": ["answers 200"]}]
    )
    await app.board.play()
    await finish_run(app)
    task = app.board.board.task("T1")
    assert task.status == "failed"
    assert task.lane == "in_progress"
    assert "answers 200" in task.error and "returns 500" in task.error
    assert task.criteria[0].status == "failed"
    assert events.of("board.run.completed")[-1]["failed"] == ["T1"]
    await app.close()


async def test_stop_halts_the_agents_and_play_picks_the_work_up_again(tmp_path) -> None:
    hold = asyncio.Event()
    provider = BoardProvider(hold=hold)
    app, events = await started_app(tmp_path, provider)
    await app.board.apply([{"op": "add_task", "title": "API", "subtasks": ["one", "two"]}])
    await app.board.play()
    await wait_for(lambda: provider.active == 2)

    # A running task cannot be edited.
    refused = await app.board.apply([{"op": "update_task", "task": "T1", "title": "Other"}])
    assert refused["ok"] is False and "being worked on" in refused["message"]

    assert (await app.board.stop())["ok"]
    await finish_run(app)
    task = app.board.board.task("T1")
    assert task.status == "stopped"
    assert {sub.status for sub in task.subtasks} == {"stopped"}
    assert events.of("board.run.completed")[-1]["status"] == "stopped"

    hold.set()
    result = await app.board.play()
    assert result["tasks"] == ["T1"]
    await finish_run(app)
    assert app.board.board.task("T1").status == "done"
    await app.close()


async def test_play_plans_drafts_instead_of_running_them(tmp_path) -> None:
    provider = BoardProvider()
    app, events = await started_app(tmp_path, provider)
    await app.board.apply([{"op": "add_task", "title": "Never planned"}])

    result = await app.board.play()
    assert result["ok"] and result["tasks"] == [] and result["planning"] == ["T1"]
    await finish_run(app)
    assert app.board.board.task("T1").status == "ready"
    assert provider.worked == []
    assert events.of("board.review.requested")
    await app.close()


async def test_the_agent_sets_up_the_board_and_hands_it_over(tmp_path) -> None:
    app, events = await started_app(tmp_path, BoardProvider())
    registry = app.orchestrator.tool_registry
    context = app.orchestrator.tool_context_factory(None)

    edited = await registry.execute(
        "board_edit",
        {
            "operations": json.dumps(
                [
                    {"op": "add_lane", "title": "Review"},
                    {"op": "add_task", "title": "Search", "subtasks": [{"title": "Index"}]},
                ]
            )
        },
        context,
    )
    assert edited["created"] == ["lane-1", "T1"]
    view = await registry.execute("board_view", {}, context)
    assert "## Review" in view["board"] and "T1.1 Index" in view["board"]

    handed = await registry.execute("board_submit", {"message": "Check the split"}, context)
    assert handed["waiting_for_user"] == ["T1"]
    assert events.of("board.review.requested")[-1]["message"] == "Check the split"
    # Handing over starts nothing: only the user's Play does.
    assert app.board.running is False
    assert app.board.board.task("T1").status == "ready"

    # Sub-agents never get the board.
    coder = app.subagent_runtime.build(app.subagent_profiles.get("coder"))
    assert not any(name.startswith("board_") for name in coder.orchestrator.tool_registry.names())
    await app.close()


async def test_sub_cards_are_pulled_across_the_lanes_as_their_agents_work(tmp_path) -> None:
    hold = asyncio.Event()
    provider = BoardProvider(hold=hold)
    app, events = await started_app(tmp_path, provider)
    await app.board.apply(
        [
            {
                "op": "add_task",
                "title": "API",
                "subtasks": [
                    {"title": "one"},
                    {"title": "two"},
                    {"title": "three", "depends_on": [1]},
                ],
            }
        ]
    )
    # Before the run every sub-card sits under its task.
    view = app.board.snapshot()["tasks"][0]
    assert {sub["lane"] for sub in view["subtasks"]} == {"todo"}

    await app.board.play()
    await wait_for(lambda: provider.active == 2)
    view = app.board.snapshot()["tasks"][0]
    lanes = {sub["id"]: sub["lane"] for sub in view["subtasks"]}
    # The task was pulled; the two its agents took went with it; the third waits in To Do.
    assert view["lane"] == "in_progress"
    assert lanes == {"T1.1": "in_progress", "T1.2": "in_progress", "T1.3": "todo"}
    running = [sub for sub in app.board.board.task("T1").subtasks if sub.status == "running"]
    assert all(sub.started_at > 0 and sub.agent for sub in running)

    hold.set()
    await finish_run(app)
    view = app.board.snapshot()["tasks"][0]
    assert view["lane"] == "done"
    assert {sub["lane"] for sub in view["subtasks"]} == {"done"}
    assert all(sub["finished_at"] >= sub["started_at"] > 0 for sub in view["subtasks"])
    # Each sub-card's own pull reached the panel as a board change.
    seen = [
        {sub["id"]: sub["lane"] for sub in payload["board"]["tasks"][0]["subtasks"]}
        for payload in events.of("board.changed")
        if payload["board"]["tasks"]
    ]
    assert any(lanes["T1.1"] == "done" and lanes["T1.3"] == "todo" for lanes in seen)
    await app.close()


async def test_sub_tasks_are_pulled_in_their_order(tmp_path) -> None:
    provider = BoardProvider()
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "main-model",
            "permission_mode": "bypass",
            "learn": False,
            "budgets": {"max_concurrent_subagents": 1},
        }
    )
    app = build_application(config=config, provider=provider)
    await app.start()
    await app.board.apply([{"op": "add_task", "title": "API", "subtasks": ["a", "b", "c", "d"]}])
    await app.board.play()
    await finish_run(app)
    assert provider.started == ["T1.1", "T1.2", "T1.3", "T1.4"]
    await app.close()


async def test_an_attached_picture_reaches_the_workers(tmp_path) -> None:
    provider = BoardProvider()
    app, _ = await started_app(tmp_path, provider)
    await app.board.apply([{"op": "add_task", "title": "Page", "subtasks": ["Layout"]}])
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    assert (await app.board.attach("T1", name="mockup.png", data=png))["ok"]
    await app.board.play()
    await finish_run(app)
    assert app.board.board.task("T1").status == "done"
    assert provider.saw_images >= 1
    await app.close()
