from __future__ import annotations

from code_ai.config.models import PlannerConfig
from code_ai.core.planning import PlannerService
from code_ai.core.verification import (
    CommandKind,
    ProjectVerification,
    VerificationCommand,
)
from code_ai.events.bus import AsyncEventBus

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
