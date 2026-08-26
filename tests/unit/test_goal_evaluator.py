from __future__ import annotations

from code_ai.core.goal.evaluator import (
    CommandCriterionEvaluator,
    EvaluationContext,
    FileCriterionEvaluator,
    GoalEvaluator,
    JudgeCriterionEvaluator,
)
from code_ai.core.goal.models import (
    AcceptanceCriterion,
    CriterionKind,
    Goal,
)

_CONTEXT = EvaluationContext(last_answer="done", evidence_summary="[]")


def _command_criterion(command: str = "run-checks") -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind=CriterionKind.COMMAND, description="checks pass", command=command
    )


def _file_criterion(path: str, pattern: str = "") -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind=CriterionKind.FILE, description="file exists", path=path, pattern=pattern
    )


def _judge_criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        kind=CriterionKind.JUDGE, description="quality bar is met"
    )


async def test_command_evaluator_met_on_exit_zero() -> None:
    async def port(command: str) -> tuple[int | None, str]:
        return 0, "all good"

    result = await CommandCriterionEvaluator(port).evaluate(
        _command_criterion(), _CONTEXT
    )
    assert result.met
    assert "exit code 0" in result.detail


async def test_command_evaluator_not_met_carries_output() -> None:
    async def port(command: str) -> tuple[int | None, str]:
        return 1, "2 tests failed"

    result = await CommandCriterionEvaluator(port).evaluate(
        _command_criterion(), _CONTEXT
    )
    assert not result.met
    assert "2 tests failed" in result.detail


async def test_file_evaluator_missing_path(tmp_path) -> None:
    result = await FileCriterionEvaluator(tmp_path).evaluate(
        _file_criterion("missing.txt"), _CONTEXT
    )
    assert not result.met


async def test_file_evaluator_existing_path_and_pattern(tmp_path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("hello goal world", encoding="utf-8")
    evaluator = FileCriterionEvaluator(tmp_path)

    assert (await evaluator.evaluate(_file_criterion("notes.txt"), _CONTEXT)).met
    assert (
        await evaluator.evaluate(_file_criterion("notes.txt", "goal"), _CONTEXT)
    ).met
    missing_pattern = await evaluator.evaluate(
        _file_criterion("notes.txt", "absent"), _CONTEXT
    )
    assert not missing_pattern.met


async def test_judge_evaluator_parses_verdict() -> None:
    async def port(system: str, user: str) -> str:
        assert "quality bar" in user
        return '{"met": true, "justification": "evidence shows it"}'

    result = await JudgeCriterionEvaluator(port).evaluate(_judge_criterion(), _CONTEXT)
    assert result.met
    assert result.detail == "evidence shows it"


async def test_judge_evaluator_defaults_to_not_met_on_garbage() -> None:
    async def port(system: str, user: str) -> str:
        return "sure, looks good to me!"

    result = await JudgeCriterionEvaluator(port).evaluate(_judge_criterion(), _CONTEXT)
    assert not result.met
    assert "unparseable" in result.detail


async def test_judge_evaluator_handles_fenced_json() -> None:
    async def port(system: str, user: str) -> str:
        return '```json\n{"met": false, "justification": "tests are red"}\n```'

    result = await JudgeCriterionEvaluator(port).evaluate(_judge_criterion(), _CONTEXT)
    assert not result.met
    assert result.detail == "tests are red"


async def test_judge_evaluator_requires_boolean_met() -> None:
    async def port(system: str, user: str) -> str:
        return '{"met": "yes"}'

    result = await JudgeCriterionEvaluator(port).evaluate(_judge_criterion(), _CONTEXT)
    assert not result.met


async def test_judge_disabled_passes_with_caveat() -> None:
    async def port(system: str, user: str) -> str:  # pragma: no cover - never called
        raise AssertionError("disabled judge must not be invoked")

    result = await JudgeCriterionEvaluator(port, enabled=False).evaluate(
        _judge_criterion(), _CONTEXT
    )
    assert result.met
    assert "disabled" in result.detail


async def test_goal_evaluator_survives_evaluator_exception(tmp_path) -> None:
    async def broken_command_port(command: str) -> tuple[int | None, str]:
        raise RuntimeError("shell exploded")

    async def judge_port(system: str, user: str) -> str:
        return '{"met": true, "justification": "fine"}'

    evaluator = GoalEvaluator(
        command_port=broken_command_port,
        judge_port=judge_port,
        workspace=tmp_path,
        judge_enabled=True,
    )
    goal = Goal(
        objective="make checks pass",
        criteria=[_command_criterion(), _judge_criterion()],
    )
    report = await evaluator.evaluate(goal, _CONTEXT)
    assert len(report.results) == 2
    command_result, judge_result = report.results
    assert not command_result.met
    assert "shell exploded" in command_result.detail
    assert judge_result.met
    assert not report.all_met
