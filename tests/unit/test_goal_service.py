from __future__ import annotations

import pytest

from code_ai.config.models import GoalConfig
from code_ai.core.errors import GoalStateError
from code_ai.core.goal.models import (
    AcceptanceCriterion,
    CriterionKind,
    CriterionResult,
    GoalEvaluationReport,
    GoalIterationRecord,
    GoalStatus,
)
from code_ai.core.goal.service import GoalService
from code_ai.events.bus import AsyncEventBus


def _service(**config) -> tuple[GoalService, list]:
    bus = AsyncEventBus()
    events: list = []
    bus.subscribe(lambda envelope: events.append(envelope))
    return GoalService(config=GoalConfig.from_mapping(config), event_bus=bus), events


def _criterion(description: str = "the feature verifiably works") -> AcceptanceCriterion:
    return AcceptanceCriterion(kind=CriterionKind.JUDGE, description=description)


async def _activated(**config) -> tuple[GoalService, list]:
    service, events = _service(**config)
    await service.define("implemente o recurso X")
    await service.propose_criteria([_criterion()])
    await service.activate()
    return service, events


def _report(service: GoalService, *, met: bool) -> GoalEvaluationReport:
    criterion = service.goal.criteria[0]
    return GoalEvaluationReport(
        results=[
            CriterionResult(
                criterion_id=criterion.criterion_id, met=met, detail="judged"
            )
        ]
    )


def _record(
    service: GoalService, index: int, *, met: bool = False, marker: str = "m"
) -> GoalIterationRecord:
    return GoalIterationRecord(
        index=index,
        prompt="continue",
        progress_marker=marker,
        report=_report(service, met=met),
    )


async def test_define_propose_activate_emits_events() -> None:
    service, events = await _activated()
    assert service.goal.status == GoalStatus.ACTIVE
    kinds = [envelope.event_type for envelope in events]
    assert kinds == ["goal.defined", "goal.criteria.proposed", "goal.activated"]


async def test_define_rejected_while_goal_is_live() -> None:
    service, _ = await _activated()
    with pytest.raises(GoalStateError):
        await service.define("outro objetivo")


async def test_define_allowed_after_terminal_goal() -> None:
    service, _ = await _activated()
    await service.stop("user")
    replacement = await service.define("novo objetivo")
    assert replacement.status == GoalStatus.DRAFT


async def test_activate_requires_criteria() -> None:
    service, _ = _service()
    await service.define("objetivo")
    with pytest.raises(GoalStateError):
        await service.activate()


async def test_first_continuation_prompt_is_the_objective() -> None:
    service, _ = await _activated()
    assert service.continuation_prompt() == "implemente o recurso X"


async def test_continuation_prompt_lists_unmet_criteria() -> None:
    service, _ = await _activated()
    await service.record_iteration(_record(service, 1, met=False))
    prompt = service.continuation_prompt()
    assert "reprovou" in prompt
    assert "the feature verifiably works" in prompt
    assert "judged" in prompt


async def test_record_iteration_emits_criterion_and_iteration_events() -> None:
    service, events = await _activated()
    events.clear()
    await service.record_iteration(_record(service, 1, met=False))
    kinds = [envelope.event_type for envelope in events]
    assert kinds == ["goal.criterion.evaluated", "goal.iteration.completed"]
    completed = events[-1].payload
    assert completed["criteria_met"] == 0
    assert completed["criteria_total"] == 1


async def test_satisfy_requires_all_met() -> None:
    service, _ = await _activated()
    with pytest.raises(GoalStateError):
        await service.satisfy(_report(service, met=False))
    await service.satisfy(_report(service, met=True))
    assert service.goal.status == GoalStatus.SATISFIED


async def test_resume_only_from_blocked() -> None:
    service, _ = await _activated()
    with pytest.raises(GoalStateError):
        await service.resume()
    await service.block("stuck")
    assert service.goal.status == GoalStatus.BLOCKED
    await service.resume()
    assert service.goal.status == GoalStatus.ACTIVE


async def test_stop_is_idempotent_on_terminal_goals() -> None:
    service, _ = await _activated()
    await service.stop("user")
    again = await service.stop("user again")
    assert again.status == GoalStatus.STOPPED
    assert again.stop_reason == "user"


async def test_no_progress_requires_identical_failures_and_markers() -> None:
    service, _ = await _activated(max_no_progress_iterations=2)
    await service.record_iteration(_record(service, 1, marker="same"))
    assert not service.no_progress_exceeded()
    await service.record_iteration(_record(service, 2, marker="same"))
    assert service.no_progress_exceeded()


async def test_no_progress_not_triggered_by_changing_markers() -> None:
    service, _ = await _activated(max_no_progress_iterations=2)
    await service.record_iteration(_record(service, 1, marker="hash-a"))
    await service.record_iteration(_record(service, 2, marker="hash-b"))
    assert not service.no_progress_exceeded()


async def test_no_progress_ignores_fully_met_iterations() -> None:
    service, _ = await _activated(max_no_progress_iterations=2)
    await service.record_iteration(_record(service, 1, met=True, marker="same"))
    await service.record_iteration(_record(service, 2, met=True, marker="same"))
    assert not service.no_progress_exceeded()


async def test_context_block_reports_criterion_state() -> None:
    service, _ = await _activated()
    block = service.context_block(iteration=1)
    assert "not evaluated yet" in block
    await service.record_iteration(_record(service, 1, met=False))
    block = service.context_block(iteration=2)
    assert "NOT met" in block
    assert "Iteration: 2" in block
