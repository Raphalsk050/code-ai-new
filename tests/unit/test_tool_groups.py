from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application, build_tool_registry
from code_ai.config.models import AppConfig
from code_ai.core.subagents.profiles import default_profile_registry
from code_ai.core.subagents.runtime import SubagentRuntime
from code_ai.prompts import build_system_prompt
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.groups import DEFERRED_TOOL_GROUPS, group_of
from code_ai.util.paths import WorkspacePolicy


def _config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "memory": {"reflection_enabled": False},
        }
    )


class _ScriptedProvider:
    """Answers each step from a script and records the tool names it was offered."""

    def __init__(self, script: list[list[ToolCall]]) -> None:
        self.script = script
        self.offered: list[set[str]] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.offered.append({tool.name for tool in request.tools})
        step = len(self.offered) - 1
        calls = self.script[step] if step < len(self.script) else []
        if calls:
            response = ModelResponse(tool_calls=calls, finish_reason=FinishReason.TOOL_CALLS)
        else:
            response = ModelResponse(text="done", finish_reason=FinishReason.STOP)
        yield ProviderEvent(kind="completed", response=response)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


def test_every_deferred_tool_is_a_registered_one() -> None:
    names = set(build_tool_registry().names())
    for group in DEFERRED_TOOL_GROUPS:
        assert group.tools <= names, group.name


async def test_deferred_groups_stay_out_of_the_request_by_default(tmp_path) -> None:
    provider = _ScriptedProvider([[]])
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    await app.submit_user_message("o que este projeto faz?")
    await app.close()

    offered = provider.offered[0]
    assert {"read_file", "search_index", "execute_command", "load_tools"} <= offered
    for group in DEFERRED_TOOL_GROUPS:
        assert not (group.tools & offered), group.name


async def test_load_tools_brings_the_group_in_on_the_next_step(tmp_path) -> None:
    provider = _ScriptedProvider(
        [[ToolCall(id="l1", name="load_tools", arguments={"group": "browser"})], []]
    )
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("abra o site e leia a pagina")
    await app.close()

    assert result.error is None
    assert "browser_open" not in provider.offered[0]
    assert group_of("browser_open").tools <= provider.offered[1]
    # Other groups stay deferred.
    assert "move_mouse" not in provider.offered[1]


async def test_calling_a_deferred_tool_by_name_loads_its_group(tmp_path) -> None:
    provider = _ScriptedProvider(
        [[ToolCall(id="a1", name="analyze_apk", arguments={"path": "missing.apk"})], []]
    )
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    await app.submit_user_message("analise o apk")
    await app.close()

    assert "analyze_logcat" not in provider.offered[0]
    # The failed call also triggers the failure-lesson meta-call (no tools);
    # the next real step is the last request.
    assert {"analyze_apk", "analyze_logcat"} <= provider.offered[-1]


async def test_a_loaded_group_stays_for_the_next_turn(tmp_path) -> None:
    provider = _ScriptedProvider(
        [[ToolCall(id="l1", name="load_tools", arguments={"group": "android"})], [], []]
    )
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    await app.submit_user_message("carregue as tools de android")
    await app.submit_user_message("e agora?")
    await app.close()

    assert "analyze_apk" in provider.offered[2]


def test_a_sub_agent_registry_without_load_tools_hides_nothing(tmp_path) -> None:
    config = _config(tmp_path)
    runtime = SubagentRuntime(
        config=config,
        provider=_ScriptedProvider([[]]),
        workspace=WorkspacePolicy.from_path(config.workspace),
        base_registry=build_tool_registry(),
        rules_text="",
    )
    built = runtime.build(default_profile_registry().get("explorer"))
    orchestrator = built.orchestrator

    assert not orchestrator.tool_registry.has("load_tools")
    assert orchestrator._allowed_tool_names() is None
    assert "browser_open" in orchestrator.tool_registry.names()


def test_the_prompt_lists_the_groups(tmp_path) -> None:
    prompt = build_system_prompt(workspace=tmp_path, language="en")
    assert "load_tools" in prompt
    for group in DEFERRED_TOOL_GROUPS:
        assert f"- {group.name}:" in prompt
