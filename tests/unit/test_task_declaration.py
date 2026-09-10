"""The model decides whether a task changes the workspace, not the classifier.

The keyword classifier still guesses from the first message, but the guess is
only a hint: every demand the runtime makes - the prose nudge, the file-change
completion evidence, the mutation rules in the task context - keys off the
model's own ``changes_workspace`` declaration in ``submit_plan``, or off what
the model actually did. These tests pin that the two never argue over a label
the model did not choose.
"""

from __future__ import annotations

from typing import Any

import pytest

from code_ai.config.models import PlannerConfig
from code_ai.core.errors import ToolArgumentError
from code_ai.core.planning import PlannerService, PlanningPhase
from code_ai.core.planning.models import (
    CRITERION_APPLY_VIA_TOOLS,
    CRITERION_VERIFY_AFTER_MUTATION,
    PlanStepKind,
    TaskIntent,
)
from code_ai.core.verification import (
    CommandKind,
    ProjectVerification,
    VerificationCommand,
)
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.internal.submit_plan import SubmitPlanTool


def _capture(bus: AsyncEventBus) -> list:
    events: list = []
    bus.subscribe(events.append)
    return events


def _project_with_tests() -> ProjectVerification:
    return ProjectVerification(
        commands=(
            VerificationCommand(
                kind=CommandKind.TEST,
                argv=("pytest", "-q"),
                description="run tests",
                source="pyproject.toml",
            ),
        ),
        ecosystems=("python",),
    )


def _planner(tmp_path, bus: AsyncEventBus | None = None) -> PlannerService:
    return PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=bus or AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
        verification_detector=lambda _ws: _project_with_tests(),
    )


async def _list_workspace(service: PlannerService) -> None:
    await service.record_tool_result(
        tool_call_id="ls-1",
        tool_name="list_files",
        payload={"path": ".", "entries": ["main.py"]},
        success=True,
        host_initiated=True,
    )


async def _write_file(service: PlannerService, path: str) -> None:
    await service.record_tool_result(
        tool_call_id=f"w-{len(service.ledger.records)}",
        tool_name="write_file",
        payload={"path": path, "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )


# --------------------------------------------------------------------------- #
# Declaring a change on a request the classifier read as a question
# --------------------------------------------------------------------------- #


async def test_declaring_a_change_rebuilds_the_skeleton_around_it(tmp_path) -> None:
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = _planner(tmp_path, bus)
    await service.begin_turn(
        "como funciona a base de codigo desse projeto?", provider_supports_tools=True
    )
    assert service.profile.requires_workspace_mutation is False
    await _list_workspace(service)

    await service.submit_agent_plan(["Add the missing docstring"], changes_workspace=True)

    profile = service.profile
    assert profile.requires_workspace_mutation is True
    assert profile.requires_verification is True
    assert profile.intent == TaskIntent.IMPLEMENTATION
    assert CRITERION_APPLY_VIA_TOOLS in profile.acceptance_criteria
    assert CRITERION_VERIFY_AFTER_MUTATION in profile.acceptance_criteria
    kinds = [step.kind for step in service.plan.steps]
    assert PlanStepKind.IMPLEMENT in kinds and PlanStepKind.VERIFY in kinds
    # The listing already on the ledger settles inspection: the model is not
    # sent back to look at the workspace it has already seen.
    assert service.current_step.kind == PlanStepKind.IMPLEMENT
    assert service.phase == PlanningPhase.EXECUTE
    declared = [e for e in events if e.event_type == "planning.task.declared"]
    assert declared and declared[-1].payload == {
        "changes_workspace": True,
        "surface_guess": False,
    }


async def test_a_declared_change_is_held_to_its_word(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("o que faz o modulo de parsing?", provider_supports_tools=True)
    await _list_workspace(service)  # the host's automatic listing at turn start
    await service.submit_agent_plan(["Fix the parser"], changes_workspace=True)

    assert service.requires_tool_for_progress() is True
    block = service.task_context_block(recommended_tool_names={"write_file"})
    assert "changes the workspace (your declaration)" in block
    assert "changes_workspace=false" in block  # the way out is named, not hidden
    decision = await service.evaluate_completion({"summary": "done"})
    assert decision.accepted is False
    assert any("file-change" in item for item in decision.missing_requirements)


async def test_declaring_after_a_write_lands_on_verification(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("me explique o parser", provider_supports_tools=True)
    await _write_file(service, "src/parser.py")

    await service.submit_agent_plan(["Verify"], changes_workspace=True)

    assert service.current_step.kind == PlanStepKind.VERIFY
    assert service.phase == PlanningPhase.VERIFY


# --------------------------------------------------------------------------- #
# Declaring an answer on a request the classifier read as a change
# --------------------------------------------------------------------------- #


async def test_declaring_an_answer_drops_every_mutation_demand(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("implemente o modulo de estoque", provider_supports_tools=True)
    assert service.profile.requires_workspace_mutation is True

    await service.submit_agent_plan(["Read the domain", "Answer"], changes_workspace=False)

    profile = service.profile
    assert profile.requires_workspace_mutation is False
    assert profile.intent == TaskIntent.LOCAL_INSPECTION
    assert CRITERION_APPLY_VIA_TOOLS not in profile.acceptance_criteria
    assert CRITERION_VERIFY_AFTER_MUTATION not in profile.acceptance_criteria
    assert [step.kind for step in service.plan.steps] == [
        PlanStepKind.INSPECT_LOCAL,
        PlanStepKind.COMPLETE,
    ]
    assert service.requires_tool_for_progress() is False
    block = service.task_context_block(recommended_tool_names={"read_file"})
    assert "READ-ONLY TASK" in block and "your declaration" in block
    decision = await service.evaluate_completion({"summary": "resposta"})
    assert decision.accepted is True


async def test_a_write_on_a_declared_answer_is_followed_not_fought(tmp_path) -> None:
    # The model said "answer", then changed a file anyway. No rejection: the
    # evidence upgrades the task and the only new expectation is verification.
    service = _planner(tmp_path)
    await service.begin_turn("o que faz o parser?", provider_supports_tools=True)
    await service.submit_agent_plan(["Read", "Answer"], changes_workspace=False)

    assert service.precondition_gap("write_file", {"path": "src/new.py"}) is None
    await _write_file(service, "src/new.py")

    assert service._effective_profile().requires_workspace_mutation is True
    block = service.task_context_block(recommended_tool_names={"write_file"})
    assert "observed: the workspace already changed" in block
    assert "READ-ONLY TASK" not in block
    # Still the model's decision on prose: no tool demand from a declaration it
    # never made, only the verification debt for the change it did make.
    assert service.requires_tool_for_progress() is False
    assert await service.note_final_answer_verification_debt() is not None


# --------------------------------------------------------------------------- #
# No declaration: the keyword guess is a hint and nothing more
# --------------------------------------------------------------------------- #


async def test_an_undeclared_mutation_guess_demands_nothing(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("atualize a documentacao do adder", provider_supports_tools=True)
    assert service.profile.requires_workspace_mutation is True

    assert service.requires_tool_for_progress() is False
    assert service._effective_profile().requires_workspace_mutation is False
    block = service.task_context_block(recommended_tool_names={"read_file"})
    assert "not declared yet - you decide" in block
    assert "looks like a workspace change" in block
    assert "do not claim completion from prose" not in block


async def test_a_plan_without_a_declaration_keeps_the_task_undeclared(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("crie src/example.py", provider_supports_tools=True)

    await service.submit_agent_plan(["Write it"])

    assert service.declared_mutation is None
    assert service.requires_tool_for_progress() is False


async def test_the_declaration_survives_a_continuation_turn(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("o que faz o parser?", provider_supports_tools=True)
    await _list_workspace(service)
    await service.submit_agent_plan(["Fix the parser"], changes_workspace=True)

    await service.begin_turn("continue", provider_supports_tools=True)

    assert service.declared_mutation is True
    assert service.requires_tool_for_progress() is True


async def test_a_new_request_starts_undeclared(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("o que faz o parser?", provider_supports_tools=True)
    await service.submit_agent_plan(["Fix the parser"], changes_workspace=True)

    await service.begin_turn("e o que faz o lexer?", provider_supports_tools=True)

    assert service.declared_mutation is None


async def test_a_revised_declaration_wins(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("o que faz o parser?", provider_supports_tools=True)
    await _list_workspace(service)
    await service.submit_agent_plan(["Fix the parser"], changes_workspace=True)
    assert service.requires_tool_for_progress() is True

    await service.submit_agent_plan(["Explain the parser"], changes_workspace=False)

    assert service.requires_tool_for_progress() is False
    assert service.profile.requires_workspace_mutation is False


# --------------------------------------------------------------------------- #
# The tool itself
# --------------------------------------------------------------------------- #


# submit_plan never touches its context: it only validates and echoes the
# declaration, so no ToolContext needs to be assembled here.
_NO_CONTEXT: Any = None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("true", True), ("no", False)],
)
async def test_submit_plan_passes_the_declaration_through(raw, expected) -> None:
    result = await SubmitPlanTool().execute(
        {"steps": ["Read"], "changes_workspace": raw}, _NO_CONTEXT
    )
    assert result["changes_workspace"] is expected


async def test_submit_plan_without_a_declaration_reports_none() -> None:
    result = await SubmitPlanTool().execute({"steps": ["Read"]}, _NO_CONTEXT)
    assert "changes_workspace" not in result


async def test_submit_plan_rejects_a_garbled_declaration() -> None:
    with pytest.raises(ToolArgumentError):
        await SubmitPlanTool().execute(
            {"steps": ["Read"], "changes_workspace": "maybe"}, _NO_CONTEXT
        )
