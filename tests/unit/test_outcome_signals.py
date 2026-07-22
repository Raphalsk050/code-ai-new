from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig, PlannerConfig
from code_ai.core.planning import PlannerService
from code_ai.core.planning.service import CompletionDecision
from code_ai.core.verification import (
    CommandKind,
    ProjectVerification,
    VerificationCommand,
)
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)

# --------------------------------------------------------------------------- #
# Detected verification commands become a durable project memory
# --------------------------------------------------------------------------- #


def _detected() -> ProjectVerification:
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


def _planner(tmp_path, detector, memo) -> PlannerService:
    return PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
        verification_detector=detector,
        verification_memo=memo,
    )


def test_verification_memo_fires_once_per_session(tmp_path) -> None:
    memos: list[ProjectVerification] = []
    service = _planner(tmp_path, lambda _ws: _detected(), memos.append)

    first = service.project_verification()
    second = service.project_verification()

    assert first is second  # detection is cached
    assert len(memos) == 1  # so the memo fires exactly once
    summary = memos[0].memory_summary()
    assert "pytest -q" in summary
    assert "python" in summary


def test_verification_memo_skips_empty_detection(tmp_path) -> None:
    memos: list[ProjectVerification] = []
    service = _planner(tmp_path, lambda _ws: ProjectVerification(), memos.append)

    assert service.project_verification().has_any is False
    assert memos == []


def test_failing_memo_never_taints_detection(tmp_path) -> None:
    def broken_memo(_verification: ProjectVerification) -> None:
        raise RuntimeError("memory store offline")

    service = _planner(tmp_path, lambda _ws: _detected(), broken_memo)
    assert service.project_verification().has_any is True


def test_memory_summary_is_deterministic_for_dedup() -> None:
    assert _detected().memory_summary() == _detected().memory_summary()
    assert ProjectVerification().memory_summary() == ""


# --------------------------------------------------------------------------- #
# A completion rejected for real evidence gaps records a lesson
# --------------------------------------------------------------------------- #


class _StaticProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Gather evidence before claiming completion.",
                finish_reason=FinishReason.STOP,
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


def _config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
        }
    )


async def test_completion_rejection_records_a_lesson(tmp_path) -> None:
    app = build_application(config=_config(tmp_path), provider=_StaticProvider())
    orchestrator = app.orchestrator

    async def rejected(_payload: dict[str, object]) -> CompletionDecision:
        return CompletionDecision(
            accepted=False,
            outcome="success",
            missing_requirements=("verification missing",),
        )

    orchestrator.planner.evaluate_completion = rejected  # type: ignore[method-assign]
    orchestrator.planner.double_check_pending = False

    call = ToolCall(id="c1", name="complete_task", arguments={"summary": "done"})
    outcome = await orchestrator._completion_rejection(call, {"summary": "done"})

    assert outcome is not None
    assert outcome.result.is_error is True
    lessons = orchestrator.failure_memory.lessons()
    assert [entry.trigger for entry in lessons] == ["completion_rejected"]
    # The lesson is already visible in the refreshed system prompt.
    assert "do not repeat these mistakes" in orchestrator.conversation.messages[0].content


async def test_double_check_rejection_is_pacing_not_failure(tmp_path) -> None:
    app = build_application(config=_config(tmp_path), provider=_StaticProvider())
    orchestrator = app.orchestrator

    async def rejected(_payload: dict[str, object]) -> CompletionDecision:
        orchestrator.planner.double_check_pending = True
        return CompletionDecision(
            accepted=False,
            outcome="success",
            missing_requirements=("Confirm each acceptance criterion.",),
        )

    orchestrator.planner.evaluate_completion = rejected  # type: ignore[method-assign]

    call = ToolCall(id="c1", name="complete_task", arguments={"summary": "done"})
    outcome = await orchestrator._completion_rejection(call, {"summary": "done"})

    assert outcome is not None
    assert orchestrator.failure_memory.lessons() == []
