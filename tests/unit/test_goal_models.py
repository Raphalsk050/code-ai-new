from __future__ import annotations

import pytest

from code_ai.core.goal.models import (
    AcceptanceCriterion,
    CriterionKind,
    CriterionResult,
    Goal,
    GoalEvaluationReport,
    GoalIterationRecord,
    GoalStatus,
)


def _criterion(kind: CriterionKind = CriterionKind.JUDGE, **kwargs) -> AcceptanceCriterion:
    defaults = {"description": "the objective is verifiably met"}
    defaults.update(kwargs)
    return AcceptanceCriterion(kind=kind, **defaults)


def test_command_criterion_requires_command() -> None:
    with pytest.raises(ValueError):
        _criterion(CriterionKind.COMMAND)
    criterion = _criterion(CriterionKind.COMMAND, command="pytest -q")
    assert criterion.command == "pytest -q"


def test_file_criterion_requires_path() -> None:
    with pytest.raises(ValueError):
        _criterion(CriterionKind.FILE)
    criterion = _criterion(CriterionKind.FILE, path="README.md")
    assert "README.md" in criterion.label()


def test_criterion_description_must_not_be_empty() -> None:
    with pytest.raises(ValueError):
        AcceptanceCriterion(kind=CriterionKind.JUDGE, description="   ")


def test_failure_signature_is_sorted_and_ignores_details() -> None:
    report_a = GoalEvaluationReport(
        results=[
            CriterionResult(criterion_id="b", met=False, detail="one wording"),
            CriterionResult(criterion_id="a", met=False, detail="explains it this way"),
            CriterionResult(criterion_id="c", met=True),
        ]
    )
    report_b = GoalEvaluationReport(
        results=[
            CriterionResult(criterion_id="a", met=False, detail="different wording"),
            CriterionResult(criterion_id="c", met=True),
            CriterionResult(criterion_id="b", met=False, detail="another phrasing"),
        ]
    )
    assert report_a.failure_signature() == ("a", "b")
    assert report_a.failure_signature() == report_b.failure_signature()


def test_all_met_requires_results() -> None:
    assert not GoalEvaluationReport(results=[]).all_met
    assert GoalEvaluationReport(
        results=[CriterionResult(criterion_id="a", met=True)]
    ).all_met


def test_stagnation_signature_needs_a_report() -> None:
    record = GoalIterationRecord(index=1, prompt="do it")
    assert record.stagnation_signature() is None
    evaluated = GoalIterationRecord(
        index=2,
        prompt="do it",
        progress_marker="m1",
        report=GoalEvaluationReport(
            results=[CriterionResult(criterion_id="a", met=False)]
        ),
    )
    assert evaluated.stagnation_signature() == (("a",), "m1")


def test_goal_snapshot_reports_latest_criterion_state() -> None:
    criterion = _criterion()
    goal = Goal(objective="ship the feature", criteria=[criterion])
    goal.status = GoalStatus.ACTIVE
    goal.iterations.append(
        GoalIterationRecord(
            index=1,
            prompt="ship the feature",
            report=GoalEvaluationReport(
                results=[
                    CriterionResult(
                        criterion_id=criterion.criterion_id, met=False, detail="not yet"
                    )
                ]
            ),
        )
    )
    snapshot = goal.snapshot()
    assert snapshot["criteria_progress"] == "0/1"
    assert snapshot["criteria"][0]["met"] is False
    assert snapshot["criteria"][0]["detail"] == "not yet"


def test_terminal_statuses() -> None:
    goal = Goal(objective="anything")
    assert not goal.is_terminal
    goal.status = GoalStatus.BLOCKED
    assert not goal.is_terminal  # blocked goals can be resumed
    goal.status = GoalStatus.SATISFIED
    assert goal.is_terminal
