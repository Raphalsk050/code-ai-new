from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)


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


class _StaticProvider:
    """Returns a fixed one-sentence answer — enough to back the lesson generator."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Validate command arguments first.", finish_reason=FinishReason.STOP
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_recorded_failure_is_injected_into_system_prompt(tmp_path) -> None:
    app = build_application(config=_config(tmp_path), provider=_StaticProvider())
    orchestrator = app.orchestrator

    # Nothing learned yet → no rendered lessons block in the startup prompt.
    # (The static instruction text mentions lessons; the rendered block is
    # uniquely marked by its "do not repeat these mistakes" header.)
    assert "do not repeat these mistakes" not in orchestrator.conversation.messages[0].content

    await orchestrator._record_failure(
        trigger="tool_error",
        signature="tool_error:execute_command",
        context="the command failed",
        fallback_lesson="Validate command arguments before running.",
    )

    # The just-learned lesson must now be visible in the system prompt, in the
    # same session — this is the regression the fix targets.
    system_prompt = orchestrator.conversation.messages[0].content
    assert "do not repeat these mistakes" in system_prompt
    assert "Validate command arguments" in system_prompt


async def test_saved_memory_is_injected_on_refresh(tmp_path) -> None:
    app = build_application(config=_config(tmp_path), provider=_StaticProvider())
    orchestrator = app.orchestrator

    orchestrator.memory.add(kind="feedback", content="Always run pytest -q.")
    orchestrator._refresh_system_prompt()

    system_prompt = orchestrator.conversation.messages[0].content
    assert "How the user wants you to work" in system_prompt
    assert "Always run pytest -q." in system_prompt


async def test_configured_render_caps_bound_the_prompt(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "memory": {"render_limit_per_kind": 2},
        }
    )
    app = build_application(config=config, provider=_StaticProvider())
    orchestrator = app.orchestrator

    orchestrator.memory.add(kind="user", content="The user is named Rafael.")
    for i in range(5):
        orchestrator.memory.add(kind="feedback", content=f"Work directive {i}.")
    orchestrator._refresh_system_prompt()

    system_prompt = orchestrator.conversation.messages[0].content
    # Non-identity kinds are capped by config; identity always renders in full.
    assert system_prompt.count("Work directive") == 2
    assert "Rafael" in system_prompt
