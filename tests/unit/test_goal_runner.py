from __future__ import annotations

from code_ai.app.goal_runner import GoalRunner
from code_ai.config.models import GoalConfig
from code_ai.core.goal.models import (
    AcceptanceCriterion,
    CriterionKind,
    CriterionResult,
    GoalEvaluationReport,
    GoalStatus,
)
from code_ai.core.goal.service import GoalService
from code_ai.core.orchestration import TurnResult
from code_ai.events.bus import AsyncEventBus


class _ScriptedEvaluator:
    """Returns pre-scripted reports; repeats the last one when exhausted."""

    def __init__(self, reports: list[GoalEvaluationReport]) -> None:
        self._reports = list(reports)

    async def evaluate(self, goal, context) -> GoalEvaluationReport:
        if len(self._reports) > 1:
            return self._reports.pop(0)
        return self._reports[0]


async def _service(**config) -> GoalService:
    service = GoalService(
        config=GoalConfig.from_mapping(config), event_bus=AsyncEventBus()
    )
    await service.define("implemente e verifique o recurso")
    await service.propose_criteria(
        [AcceptanceCriterion(kind=CriterionKind.JUDGE, description="recurso funciona")]
    )
    await service.activate()
    return service


def _report(service: GoalService, *, met: bool) -> GoalEvaluationReport:
    criterion = service.goal.criteria[0]
    return GoalEvaluationReport(
        results=[CriterionResult(criterion_id=criterion.criterion_id, met=met)]
    )


def _runner(
    service: GoalService,
    reports: list[GoalEvaluationReport],
    *,
    turn_results: list[TurnResult] | None = None,
    markers: list[str] | None = None,
    config: GoalConfig | None = None,
) -> tuple[GoalRunner, list[str]]:
    prompts: list[str] = []
    default_turn = TurnResult(text="worked on it", response=None)
    marker_state = {"index": 0}

    async def run_iteration(prompt: str, context: str) -> TurnResult:
        prompts.append(prompt)
        if turn_results:
            return turn_results.pop(0) if len(turn_results) > 1 else turn_results[0]
        return default_turn

    def progress_marker() -> str:
        if markers is None:
            marker_state["index"] += 1
            return f"marker-{marker_state['index']}"
        value = markers[min(marker_state["index"], len(markers) - 1)]
        marker_state["index"] += 1
        return value

    runner = GoalRunner(
        service=service,
        evaluator=_ScriptedEvaluator(reports),
        config=config or service.config,
        run_iteration=run_iteration,
        progress_marker=progress_marker,
        evidence_summary=lambda: "[]",
    )
    return runner, prompts


async def test_runner_stops_exactly_when_criteria_pass() -> None:
    service = await _service()
    reports = [
        _report(service, met=False),
        _report(service, met=False),
        _report(service, met=True),
    ]
    runner, prompts = _runner(service, reports)
    goal = await runner.run()
    assert goal.status == GoalStatus.SATISFIED
    assert len(goal.iterations) == 3
    assert len(prompts) == 3
    # First prompt is the objective; later ones carry the unmet-criteria feedback.
    assert prompts[0] == "implemente e verifique o recurso"
    assert "reprovou" in prompts[1]


async def test_runner_exhausts_at_iteration_ceiling() -> None:
    service = await _service(max_iterations=2)
    runner, prompts = _runner(service, [_report(service, met=False)])
    goal = await runner.run()
    assert goal.status == GoalStatus.EXHAUSTED
    assert "iteration ceiling" in goal.stop_reason
    assert len(prompts) == 2


async def test_runner_blocks_on_stagnation() -> None:
    service = await _service(max_no_progress_iterations=2, max_iterations=10)
    runner, prompts = _runner(
        service, [_report(service, met=False)], markers=["frozen"]
    )
    goal = await runner.run()
    assert goal.status == GoalStatus.BLOCKED
    assert "no workspace progress" in goal.stop_reason
    assert len(prompts) == 2  # two identical laps, then the guard fires


async def test_runner_stops_when_turn_is_cancelled() -> None:
    service = await _service()
    cancelled = TurnResult(text="", response=None, cancelled=True)
    runner, prompts = _runner(
        service, [_report(service, met=False)], turn_results=[cancelled]
    )
    goal = await runner.run()
    assert goal.status == GoalStatus.STOPPED
    assert len(prompts) == 1
    assert len(goal.iterations) == 0  # a cancelled turn records no evaluation


async def test_runner_honors_stop_request_before_first_iteration() -> None:
    service = await _service()
    runner, prompts = _runner(service, [_report(service, met=False)])
    runner.request_stop()
    goal = await runner.run()
    assert goal.status == GoalStatus.STOPPED
    assert prompts == []


async def test_runner_blocks_after_consecutive_turn_errors() -> None:
    service = await _service(max_iterations=10)
    erroring = TurnResult(text="", response=None, error="provider down")
    runner, prompts = _runner(
        service, [_report(service, met=False)], turn_results=[erroring]
    )
    goal = await runner.run()
    assert goal.status == GoalStatus.BLOCKED
    assert "turn errors" in goal.stop_reason
    assert len(prompts) == 2


async def test_runner_blocks_when_iteration_cannot_run() -> None:
    service = await _service()

    async def failing_iteration(prompt: str, context: str) -> TurnResult:
        raise RuntimeError("A turn is already running.")

    runner = GoalRunner(
        service=service,
        evaluator=_ScriptedEvaluator([_report(service, met=False)]),
        config=service.config,
        run_iteration=failing_iteration,
        progress_marker=lambda: "",
        evidence_summary=lambda: "",
    )
    goal = await runner.run()
    assert goal.status == GoalStatus.BLOCKED
    assert "could not run" in goal.stop_reason
