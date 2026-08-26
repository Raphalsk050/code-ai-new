"""The turn's time budget bounds time without progress, not time working."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.orchestration import WIND_DOWN_TIME_BUDGET
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)


def _config(tmp_path, *, turn_seconds: int) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "planner": {"enabled": False},
            "budgets": {
                "max_turn_seconds": turn_seconds,
                "max_turn_wall_time_s": turn_seconds,
            },
        }
    )


class _SlowProvider:
    """Each step takes longer than the whole turn budget.

    ``distinct`` decides whether the steps count as progress: distinct tool
    calls advance the task, an identical call repeated does not.
    """

    def __init__(self, steps: int, *, pause: float, distinct: bool) -> None:
        self.steps = steps
        self.pause = pause
        self.distinct = distinct
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        await asyncio.sleep(self.pause)
        if self.calls <= self.steps:
            # A different argument each round is what makes the round count as
            # progress; the identical call repeated is what does not.
            entries = 10 + self.calls if self.distinct else 10
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id=f"c{self.calls}",
                            name="list_files",
                            arguments={"path": ".", "max_entries": entries},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_progress_keeps_a_long_turn_alive(tmp_path) -> None:
    # Each step outlives the whole budget, so a wall clock would have cut this
    # turn off at step two - the failure the user hit: minutes per step on a
    # local model, and "I reached a runtime safety budget" after a handful.
    provider = _SlowProvider(3, pause=0.6, distinct=True)
    app = build_application(config=_config(tmp_path, turn_seconds=1), provider=provider)

    await app.start()
    result = await app.submit_user_message("keep going")
    await app.close()

    assert result.wind_down_reason != WIND_DOWN_TIME_BUDGET
    assert provider.calls >= 4, "the turn must survive past the original deadline"


async def test_a_turn_going_nowhere_still_winds_down(tmp_path) -> None:
    # The other half: repeating the same call is not progress, so it buys no
    # time back and the guard still fires.
    provider = _SlowProvider(40, pause=0.6, distinct=False)
    app = build_application(config=_config(tmp_path, turn_seconds=1), provider=provider)

    await app.start()
    result = await app.submit_user_message("spin")
    await app.close()

    assert result.wind_down_reason is not None
    assert provider.calls < 40, "a stalling turn must not run to the step budget"
