from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig, MemoryConfig
from code_ai.core.memory import MemoryService, MemoryStore
from code_ai.core.reflection import ReflectionService, TurnDigest
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)


def _service(tmp_path, generator, **config_overrides) -> ReflectionService:
    memory = MemoryService(
        global_store=MemoryStore(tmp_path / "global"),
        project_store=MemoryStore(tmp_path / "project"),
    )
    config = MemoryConfig(**config_overrides) if config_overrides else MemoryConfig()
    return ReflectionService(memory=memory, generator=generator, config=config)


def _digest() -> TurnDigest:
    return TurnDigest(
        user_text="Fix the failing build.",
        final_text="Fixed; make release now passes.",
        actions=("read_file({\"path\": \"Makefile\"}) -> ok",),
        evidence="",
        outcome="success",
    )


async def test_reflection_saves_and_reports(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        # The digest and current memories must both reach the meta-call.
        assert "Fix the failing build." in prompt
        assert "Stored memories:" in prompt
        return (
            '{"save": [{"kind": "project", "content": "Build with make release."}],'
            ' "retire": []}'
        )

    service = _service(tmp_path, generator)
    report = await service.reflect_on_turn(_digest())

    assert report.saved == ("Build with make release.",)
    assert report.retired == ()
    assert "Build with make release." in service._memory.render_for_prompt()


async def test_reflection_retires_superseded_fact(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        return (
            '{"save": [{"kind": "project", "content": "The stack is FastAPI."}],'
            ' "retire": ["The stack is Flask."]}'
        )

    service = _service(tmp_path, generator)
    service._memory.add(kind="project", content="The stack is Flask.")

    report = await service.reflect_on_turn(_digest())

    assert report.saved == ("The stack is FastAPI.",)
    assert report.retired == ("The stack is Flask.",)
    rendered = service._memory.render_for_prompt()
    assert "FastAPI" in rendered
    assert "Flask" not in rendered


async def test_reflection_never_retires_what_it_just_saved(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        return (
            '{"save": [{"kind": "feedback", "content": "Run pytest -q."}],'
            ' "retire": ["Run pytest -q."]}'
        )

    service = _service(tmp_path, generator)
    report = await service.reflect_on_turn(_digest())

    assert report.saved == ("Run pytest -q.",)
    assert report.retired == ()
    assert "Run pytest -q." in service._memory.render_for_prompt()


async def test_reflection_tolerates_fences_and_prose(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        return (
            "Here is what I decided:\n```json\n"
            '{"save": [{"kind": "user", "content": "The user prefers pt-BR."}], "retire": []}'
            "\n```\nDone."
        )

    service = _service(tmp_path, generator)
    report = await service.reflect_on_turn(_digest())

    assert report.saved == ("The user prefers pt-BR.",)


async def test_reflection_drops_invalid_items_and_caps_saves(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        saves = [{"kind": "project", "content": f"Fact {i}."} for i in range(6)]
        saves.insert(0, {"kind": "bogus", "content": "Bad kind."})
        saves.insert(1, {"kind": "project", "content": ""})
        import json

        return json.dumps({"save": saves, "retire": []})

    service = _service(tmp_path, generator)
    report = await service.reflect_on_turn(_digest())

    # Invalid entries are dropped; valid ones are capped at 3 per pass.
    assert len(report.saved) == 3
    assert "Bad kind." not in report.saved


async def test_reflection_survives_garbage_and_generator_errors(tmp_path) -> None:
    async def garbage(prompt: str) -> str:
        return "no json here at all"

    report = await _service(tmp_path, garbage).reflect_on_turn(_digest())
    assert not report.changed

    async def broken(prompt: str) -> str:
        raise RuntimeError("meta-call died")

    report = await _service(tmp_path, broken).reflect_on_turn(_digest())
    assert not report.changed


async def test_should_reflect_gating(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        return "{}"

    service = _service(tmp_path, generator, reflection_min_tool_calls=3)
    assert service.should_reflect(tool_calls_executed=2) is False
    assert service.should_reflect(tool_calls_executed=3) is True

    disabled = _service(tmp_path, generator, reflection_enabled=False)
    assert disabled.should_reflect(tool_calls_executed=10) is False


# --------------------------------------------------------------------------- #
# End-to-end: a real turn triggers reflection, which lands in the next prompt.
# --------------------------------------------------------------------------- #


class _ScriptedProvider:
    """stream() answers the turn; complete() answers the reflection meta-call."""

    def __init__(self) -> None:
        self.reflection_prompts: list[str] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Done: the build is fixed.", finish_reason=FinishReason.STOP
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.reflection_prompts.append(request.messages[-1].content)
        return ModelResponse(
            text=(
                '{"save": [{"kind": "project", "content": "Build with make release."}],'
                ' "retire": []}'
            ),
            finish_reason=FinishReason.STOP,
        )

    async def close(self) -> None:
        return None


def _app_config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "planner": {"enabled": False},
            "memory": {"reflection_min_tool_calls": 1},
        }
    )


async def test_turn_end_reflection_reaches_next_prompt(tmp_path) -> None:
    provider = _ScriptedProvider()
    app = build_application(config=_app_config(tmp_path), provider=provider)
    orchestrator = app.orchestrator

    result = await orchestrator.run_turn("Fix the build.")
    assert result.error is None

    # A text-only turn executes zero tool calls: below the threshold, no
    # reflection may be scheduled.
    assert orchestrator._learning_task is None
    assert provider.reflection_prompts == []

    # Simulate a substantive turn by lowering the bar to zero.
    orchestrator.reflection._config.reflection_min_tool_calls = 0
    result = await orchestrator.run_turn("Fix the build again.")
    assert result.error is None
    await orchestrator.drain_learning()

    assert len(provider.reflection_prompts) == 1
    assert "Fix the build again." in provider.reflection_prompts[0]
    # The distilled fact reaches the system prompt for the next turn.
    assert "Build with make release." in orchestrator.conversation.messages[0].content


async def test_cancelled_or_failed_turns_do_not_reflect(tmp_path) -> None:
    provider = _ScriptedProvider()
    app = build_application(config=_app_config(tmp_path), provider=provider)
    orchestrator = app.orchestrator
    orchestrator.reflection._config.reflection_min_tool_calls = 0

    from code_ai.core.orchestration import TurnResult, _TurnState

    state = _TurnState(cancel_event=None, deadline=0.0, user_text="x")
    cancelled = TurnResult(text="", response=None, cancelled=True)
    orchestrator._maybe_schedule_reflection(state, cancelled)
    assert orchestrator._learning_task is None

    failed = TurnResult(text="", response=None, error="provider exploded")
    orchestrator._maybe_schedule_reflection(state, failed)
    assert orchestrator._learning_task is None
