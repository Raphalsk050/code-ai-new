"""Tools the user switched off must be gone from every path the agent routes through."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from code_ai.bootstrap import build_application, build_tool_registry
from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.base import ToolCapability
from code_ai.tools.groups import group_named
from code_ai.tools.internal import LoadToolsTool


class ScriptedProvider:
    """Replays one step per request and keeps every request it was sent."""

    def __init__(self, steps: list[Callable[[ModelRequest], ModelResponse]]) -> None:
        self.steps = steps
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        index = min(len(self.requests), len(self.steps)) - 1
        response = self.steps[index](request)
        if response.text:
            yield ProviderEvent(kind="text_delta", text_delta=response.text)
        yield ProviderEvent(kind="completed", response=response)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


def answer(text: str = "done") -> Callable[[ModelRequest], ModelResponse]:
    return lambda _request: ModelResponse(text=text, finish_reason=FinishReason.STOP)


def call(name: str, **arguments) -> Callable[[ModelRequest], ModelResponse]:
    return lambda _request: ModelResponse(
        tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=arguments)],
        finish_reason=FinishReason.TOOL_CALLS,
    )


def tool_names(request: ModelRequest) -> set[str]:
    return {tool.name for tool in request.tools}


def app_for(tmp_path, provider, **settings):
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake", **settings}
    )
    return build_application(config=config, provider=provider)


# ---------------------------------------------------------------- registry


def test_a_disabled_tool_looks_exactly_like_an_unregistered_one() -> None:
    registry = build_tool_registry()
    registry.set_disabled({"read_file"})

    assert "read_file" not in registry.names()
    assert not registry.has("read_file")
    assert registry.get("read_file") is None
    assert "read_file" not in {tool.name for tool in registry.definitions()}
    with pytest.raises(ToolArgumentError, match="Unknown tool"):
        registry.capabilities("read_file")
    # Still listed for the switches, so it can be turned back on.
    assert "read_file" in registry.registered_names()


async def test_a_call_to_a_disabled_tool_fails_as_unknown() -> None:
    registry = build_tool_registry()
    registry.set_disabled({"read_file"})

    with pytest.raises(ToolArgumentError, match="Unknown tool: read_file"):
        await registry.execute("read_file", {"path": "x"}, context=None)


def test_a_sub_agent_registry_follows_switches_made_after_it_was_built() -> None:
    registry = build_tool_registry()
    subset = registry.select(frozenset({ToolCapability.LOCAL_READ}))
    assert subset.has("read_file")

    registry.set_disabled({"read_file"})
    assert not subset.has("read_file")

    registry.set_disabled(set())
    assert subset.has("read_file")


def test_every_tool_can_be_disabled() -> None:
    registry = build_tool_registry()
    registry.set_disabled(registry.registered_names())

    assert registry.names() == []
    assert registry.definitions() == []


def test_the_switches_survive_the_config_file() -> None:
    names = ["read_file", " ", "read_file", "web_search"]
    config = AppConfig.from_mapping({"disabled_tools": names})
    assert config.disabled_tools == ["read_file", "web_search"]
    assert AppConfig.from_mapping(config.to_dict()).disabled_tools == ["read_file", "web_search"]


# ---------------------------------------------------------------- tool groups


async def test_a_group_with_every_tool_disabled_is_not_offered_or_loadable() -> None:
    registry = build_tool_registry()
    android = group_named("android")
    registry.set_disabled(android.tools)
    load_tools = registry.get("load_tools")

    assert "android" not in load_tools.description
    assert "android" not in load_tools.input_schema["properties"]["group"]["description"]
    with pytest.raises(ToolArgumentError, match="Unknown tool group"):
        await load_tools.execute({"group": "android"}, context=None)


async def test_loading_a_group_names_only_its_enabled_tools() -> None:
    registry = build_tool_registry()
    registry.set_disabled({"browser_evaluate"})

    payload = await registry.get("load_tools").execute({"group": "browser"}, context=None)

    assert "browser_evaluate" not in payload["tools"]
    assert "browser_open" in payload["tools"]


def test_an_unwired_load_tools_still_offers_every_group() -> None:
    assert "android" in LoadToolsTool().description


# ---------------------------------------------------------------- the agent


async def test_a_disabled_tool_never_reaches_the_model_and_its_call_fails(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("secret\n", encoding="utf-8")
    provider = ScriptedProvider([call("read_file", path="note.txt"), answer()])
    app = app_for(tmp_path, provider, disabled_tools=["read_file"])

    await app.start()
    await app.submit_user_message("read note.txt")
    await app.close()

    assert all("read_file" not in tool_names(request) for request in provider.requests)
    results = [m for m in provider.requests[-1].messages if m.role == "tool"]
    assert results and "secret" not in results[-1].content


async def test_switching_a_tool_off_mid_turn_takes_it_out_of_the_next_step(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    app = None
    seen: list[set[str]] = []

    def first(request: ModelRequest) -> ModelResponse:
        seen.append(tool_names(request))
        # The user flips the switch while the model is still working.
        app.orchestrator.tool_registry.set_disabled({"list_files"})
        return call("read_file", path="note.txt")(request)

    def second(request: ModelRequest) -> ModelResponse:
        seen.append(tool_names(request))
        return answer()(request)

    provider = ScriptedProvider([first, second])
    app = app_for(tmp_path, provider)
    await app.start()
    await app.submit_user_message("read note.txt")
    await app.close()

    assert "list_files" in seen[0]
    assert "list_files" not in seen[1]


async def test_with_every_tool_disabled_the_turn_still_ends(tmp_path) -> None:
    provider = ScriptedProvider([answer("no tools here")])
    app = app_for(tmp_path, provider)
    registry = app.orchestrator.tool_registry
    registry.set_disabled(registry.registered_names())

    await app.start()
    result = await app.submit_user_message("hello")
    await app.close()

    assert all(request.tools == [] for request in provider.requests)
    assert "no tools here" in (result.text or "")


async def test_disabling_load_tools_keeps_the_groups_out_instead_of_flooding(tmp_path) -> None:
    provider = ScriptedProvider([answer()])
    app = app_for(tmp_path, provider, disabled_tools=["load_tools"])

    await app.start()
    await app.submit_user_message("hello")
    await app.close()

    offered = tool_names(provider.requests[0])
    assert "load_tools" not in offered
    assert "browser_open" not in offered
    assert "Tool groups:" not in provider.requests[0].messages[0].content


async def test_the_prompt_catalog_drops_a_group_once_its_tools_are_off(tmp_path) -> None:
    provider = ScriptedProvider([answer()])
    app = app_for(tmp_path, provider, disabled_tools=sorted(group_named("android").tools))

    await app.start()
    await app.submit_user_message("hello")
    await app.close()

    prompt = provider.requests[0].messages[0].content
    assert "- browser:" in prompt
    assert "- android:" not in prompt
