from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.goal.models import GoalStatus
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)


class GoalScriptedProvider:
    """Fake provider for the /goal loop.

    ``complete`` (one-off calls) answers the criteria-derivation request with a
    deterministic FILE criterion. ``stream`` (turn calls) simulates the agent's
    work: the first iteration produces nothing, the second "creates" the target
    file — so the goal must take exactly two iterations to satisfy.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.turn_calls = 0
        self.oneoff_calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.turn_calls += 1
        if self.turn_calls >= 2:
            (self.workspace / "done.txt").write_text("READY\n", encoding="utf-8")
        text = f"iteração {self.turn_calls} concluída"
        yield ProviderEvent(kind="text_delta", text_delta=text)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=text, finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.oneoff_calls += 1
        return ModelResponse(
            text=(
                '[{"kind": "FILE", "description": "done.txt contém READY", '
                '"path": "done.txt", "pattern": "READY"}]'
            )
        )

    async def close(self) -> None:
        return None


async def test_goal_loop_runs_until_criteria_met(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            # The planner's per-turn behaviour is covered elsewhere; disabling it
            # keeps each scripted iteration to a single model call.
            "planner": {"enabled": False},
        }
    )
    provider = GoalScriptedProvider(tmp_path)
    app = build_application(config=config, provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))
    await app.start()

    snapshot = await app.define_goal("crie done.txt contendo READY")
    assert snapshot["status"] == GoalStatus.DRAFT.value
    assert snapshot["started"] is False
    assert provider.oneoff_calls == 1  # criteria were derived by a one-off call

    await app.start_goal()
    goal = await app._goal_task
    await app.close()

    assert goal.status == GoalStatus.SATISFIED
    assert len(goal.iterations) == 2
    assert not goal.iterations[0].report.all_met
    assert goal.iterations[1].report.all_met
    assert (tmp_path / "done.txt").read_text(encoding="utf-8").strip() == "READY"
    for expected in (
        "goal.defined",
        "goal.criteria.proposed",
        "goal.activated",
        "goal.iteration.started",
        "goal.criterion.evaluated",
        "goal.iteration.completed",
        "goal.satisfied",
    ):
        assert expected in events, expected


async def test_goal_stop_settles_a_draft_goal(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "planner": {"enabled": False},
        }
    )
    provider = GoalScriptedProvider(tmp_path)
    app = build_application(config=config, provider=provider)
    await app.start()

    await app.define_goal("objetivo que não será iniciado")
    snapshot = await app.stop_goal()
    await app.close()

    assert snapshot["status"] == GoalStatus.STOPPED.value
    assert provider.turn_calls == 0  # nothing ever ran
