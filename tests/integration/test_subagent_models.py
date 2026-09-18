"""A sub-agent can run on a different model than the session's.

Same endpoint, same key, only the model name changes - which is the shape of a
machine serving several models at once. The list the user curates is the whole
authority: anything outside it is refused with the reason, never run quietly on
the wrong model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError
from code_ai.core.subagents import default_profile_registry
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.agents import DispatchAgentTool


class RecordingProvider:
    """Answers everything with prose and remembers which model was asked."""

    def __init__(self) -> None:
        self.models: list[str] = []
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.models.append(request.model)
        self.calls += 1
        if self.calls == 1:
            response = ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="dispatch_agent",
                        arguments={
                            "tasks": [
                                {
                                    "agent_type": "explorer",
                                    "prompt": "Diga ola.",
                                    "model": "small-model",
                                }
                            ]
                        },
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            )
            yield ProviderEvent(kind="completed", response=response)
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="pronto", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


def app_for(tmp_path, provider, **settings):
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "main-model",
            "permission_mode": "bypass",
            **settings,
        }
    )
    return build_application(config=config, provider=provider)


# ------------------------------------------------------------------ the list


def test_the_session_model_leads_the_list(tmp_path) -> None:
    app = app_for(tmp_path, RecordingProvider(), subagent_models=["small-model", "main-model"])
    coordinator = app.orchestrator.tool_context_factory(None).subagent_coordinator

    # The session's own model first, and never twice.
    assert coordinator.allowed_models() == ["main-model", "small-model"]


def test_the_tool_offers_no_choice_until_a_model_is_added(tmp_path) -> None:
    app = app_for(tmp_path, RecordingProvider())
    schema = app.orchestrator.tool_registry.get("dispatch_agent").input_schema

    assert "model" not in schema["properties"]["tasks"]["items"]["properties"]


def test_the_tool_offers_the_models_once_one_is_added(tmp_path) -> None:
    app = app_for(tmp_path, RecordingProvider(), subagent_models=["small-model"])
    tool = app.orchestrator.tool_registry.get("dispatch_agent")

    field = tool.input_schema["properties"]["tasks"]["items"]["properties"]["model"]
    assert field["enum"] == ["main-model", "small-model"]
    assert "small-model" in tool.description


def test_a_model_added_mid_session_is_offered_without_a_restart(tmp_path) -> None:
    app = app_for(tmp_path, RecordingProvider())
    tool = app.orchestrator.tool_registry.get("dispatch_agent")
    assert "model" not in tool.input_schema["properties"]["tasks"]["items"]["properties"]

    # What the Doctor does when the user presses +.
    app.session.config.subagent_models = ["small-model"]

    field = tool.input_schema["properties"]["tasks"]["items"]["properties"]["model"]
    assert field["enum"] == ["main-model", "small-model"]


# ------------------------------------------------------------------ dispatch


async def test_the_sub_agent_really_runs_on_the_chosen_model(tmp_path) -> None:
    provider = RecordingProvider()
    app = app_for(tmp_path, provider, subagent_models=["small-model"])

    await app.start()
    await app.submit_user_message("investigue algo")
    await app.close()

    # The parent asked for its own model; the sub-agent asked for the other one.
    assert provider.models[0] == "main-model"
    assert "small-model" in provider.models


async def test_a_model_outside_the_list_is_refused_by_the_tool(tmp_path) -> None:
    app = app_for(tmp_path, RecordingProvider(), subagent_models=["small-model"])
    tool = app.orchestrator.tool_registry.get("dispatch_agent")
    context = app.orchestrator.tool_context_factory(None)

    with pytest.raises(ToolArgumentError, match="not available to sub-agents"):
        await tool.execute(
            {"tasks": [{"agent_type": "explorer", "prompt": "x", "model": "secret"}]},
            context=context,
        )


async def test_the_coordinator_refuses_an_unlisted_model_with_a_reason(tmp_path) -> None:
    from code_ai.core.subagents.coordinator import SubagentRequest

    app = app_for(tmp_path, RecordingProvider(), subagent_models=["small-model"])
    coordinator = app.orchestrator.tool_context_factory(None).subagent_coordinator

    reports = await coordinator.dispatch(
        [SubagentRequest(agent_type="explorer", prompt="x", model="not-listed")]
    )

    assert reports[0].status != "completed"
    assert "not-listed" in (reports[0].error or "")
    assert "/doctor subagents" in (reports[0].error or "")


async def test_no_model_named_means_the_session_model(tmp_path) -> None:
    from code_ai.core.subagents.coordinator import SubagentRequest

    provider = RecordingProvider()
    provider.calls = 5  # skip the dispatch script; answer prose straight away
    app = app_for(tmp_path, provider, subagent_models=["small-model"])
    coordinator = app.orchestrator.tool_context_factory(None).subagent_coordinator

    await coordinator.dispatch([SubagentRequest(agent_type="explorer", prompt="x")])

    assert provider.models == ["main-model"]


def test_an_unwired_tool_still_works(tmp_path) -> None:
    tool = DispatchAgentTool(default_profile_registry())

    assert "tasks" in tool.input_schema["properties"]
    assert "explorer" in tool.description
