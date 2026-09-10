"""Turn budgets bound time without progress, and end in an answer either way.

Two rules live here. A turn that is genuinely working must not be cut off by a
wall clock. And a turn that *does* hit a ceiling is the runtime's problem to
solve, not news to report: the program lands it through the model, so the user
reads an answer about their request rather than a line about a safety budget.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.orchestration import (
    WIND_DOWN_STEP_BUDGET,
    WIND_DOWN_TIME_BUDGET,
)
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


LANDED = "Li o config.toml e a porta padrão é 8080. Falta conferir o timeout."


class _NeverStopsProvider:
    """Calls a tool on every step until the tools are taken away.

    Models the real shape of a turn that runs out of budget: the model is mid
    -stride, its last words are a dangling preamble, and it only answers once
    the runtime tells it the tool phase is over.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.tools_offered: list[int] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        self.tools_offered.append(len(request.tools or []))
        if request.tools:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    text="Agora vou olhar o próximo arquivo.",
                    tool_calls=[
                        ToolCall(
                            id=f"c{self.calls}",
                            name="list_files",
                            arguments={"path": ".", "max_entries": 10 + self.calls},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(kind="text_delta", text_delta=LANDED)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=LANDED, finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


def _step_capped_config(tmp_path, *, steps: int) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "planner": {"enabled": False},
            "memory": {"reflection_enabled": False},
            "budgets": {"max_model_steps": steps, "max_stall_rounds": 99},
        }
    )


async def test_a_spent_budget_ends_in_the_models_answer(tmp_path) -> None:
    # The user must never be handed the runtime's bookkeeping. Hitting the step
    # ceiling used to end the turn on a dangling "now let me look at the next
    # file", or on a canned line about a "runtime safety budget"; now the
    # program asks for the answer the work supports and shows that instead.
    provider = _NeverStopsProvider()
    app = build_application(config=_step_capped_config(tmp_path, steps=3), provider=provider)

    await app.start()
    result = await app.submit_user_message("me diga a porta padrão do projeto")
    await app.close()

    assert result.text == LANDED
    assert "budget" not in result.text.lower()
    assert result.error is None


async def test_the_landing_request_offers_no_tools(tmp_path) -> None:
    # Withholding the tools is what makes the landing terminal: the model
    # cannot open new work on a turn that is already over.
    provider = _NeverStopsProvider()
    app = build_application(config=_step_capped_config(tmp_path, steps=3), provider=provider)

    await app.start()
    await app.submit_user_message("me diga a porta padrão do projeto")
    await app.close()

    assert all(count > 0 for count in provider.tools_offered[:3])
    assert provider.tools_offered[-1] == 0, "the closing request must carry no tools"
    assert provider.calls == 4, "exactly one landing call on top of the three steps"


async def test_the_reason_still_reaches_the_program(tmp_path) -> None:
    # Handled quietly for the user, still legible to the runtime: the sub-agent
    # coordinator and the goal runner both key off the wind-down reason, and the
    # event carries it for telemetry.
    provider = _NeverStopsProvider()
    app = build_application(config=_step_capped_config(tmp_path, steps=3), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("me diga a porta padrão do projeto")
    await app.close()

    assert result.wind_down_reason == WIND_DOWN_STEP_BUDGET
    assert "turn.budget_exhausted" in events


class _SilentAfterToolsProvider(_NeverStopsProvider):
    """Answers with nothing at all when asked to land the turn."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        if request.tools:
            async for event in super().stream(request):
                yield event
            return
        self.calls += 1
        self.tools_offered.append(0)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="", finish_reason=FinishReason.STOP),
        )


async def test_a_silent_landing_still_says_something_without_jargon(tmp_path) -> None:
    provider = _SilentAfterToolsProvider()
    app = build_application(config=_step_capped_config(tmp_path, steps=3), provider=provider)

    await app.start()
    result = await app.submit_user_message("me diga a porta padrão do projeto")
    await app.close()

    assert result.text.strip()
    assert "budget" not in result.text.lower()
    assert "runtime" not in result.text.lower()
