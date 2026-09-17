"""Experimental on-demand tools: off by default, one tool at a time when on."""

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
    ToolCall,
)

_CONTROL_TOOLS = {
    "complete_plan_step",
    "complete_task",
    "finish_discovery",
    "request_external_gap",
    "submit_plan",
}


def _config(tmp_path, *, on_demand: bool) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "memory": {"reflection_enabled": False},
            "experimental": {"on_demand_tools": on_demand},
        }
    )


class _ScriptedProvider:
    """Answers each step from a script; keeps the tools and prompt of every request."""

    def __init__(self, script: list[list[ToolCall]]) -> None:
        self.script = script
        self.offered: list[set[str]] = []
        self.prompts: list[str] = []
        self.results: list[list[str]] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.offered.append({tool.name for tool in request.tools})
        self.prompts.append(request.messages[0].content)
        self.results.append([m.content for m in request.messages if m.role == "tool"])
        step = len(self.offered) - 1
        calls = self.script[step] if step < len(self.script) else []
        if calls:
            response = ModelResponse(tool_calls=calls, finish_reason=FinishReason.TOOL_CALLS)
        else:
            response = ModelResponse(text="done", finish_reason=FinishReason.STOP)
        yield ProviderEvent(kind="completed", response=response)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


def _load(name: str) -> ToolCall:
    return ToolCall(id=f"l_{name}", name="load_tool", arguments={"name": name})


async def _run(tmp_path, script, *, on_demand: bool, messages=("go",)):
    provider = _ScriptedProvider(script)
    app = build_application(config=_config(tmp_path, on_demand=on_demand), provider=provider)
    await app.start()
    for message in messages:
        await app.submit_user_message(message)
    await app.close()
    return app, provider


async def test_off_by_default_nothing_changes(tmp_path) -> None:
    defaults = AppConfig.from_mapping({"workspace": str(tmp_path)})
    assert defaults.experimental.on_demand_tools is False
    _, provider = await _run(tmp_path, [[]], on_demand=False)

    assert {"read_file", "load_tools"} <= provider.offered[0]
    assert "load_tool" not in provider.offered[0]
    assert "load_tool with" not in provider.prompts[0]
    assert "Tool groups:" in provider.prompts[0]


async def test_off_a_call_to_load_tool_fails_and_loads_nothing(tmp_path) -> None:
    _, provider = await _run(tmp_path, [[_load("browser_open")], []], on_demand=False)

    assert "browser_open" not in provider.offered[1]
    assert "Unknown tool" in provider.results[1][-1]


async def test_on_the_request_starts_with_only_the_control_tools(tmp_path) -> None:
    _, provider = await _run(tmp_path, [[]], on_demand=True)

    assert provider.offered[0] == _CONTROL_TOOLS | {"load_tool"}
    prompt = provider.prompts[0]
    assert "call load_tool with its exact name" in prompt
    assert "- read_file:" in prompt
    assert "- browser_open:" in prompt
    assert "Tool groups:" not in prompt


async def test_on_load_tool_brings_in_only_that_tool_for_the_session(tmp_path) -> None:
    _, provider = await _run(
        tmp_path,
        [[_load("read_file")], [], []],
        on_demand=True,
        messages=("leia o arquivo", "e agora?"),
    )

    assert "read_file" not in provider.offered[0]
    assert provider.offered[1] == _CONTROL_TOOLS | {"load_tool", "read_file"}
    assert "read_file" in provider.offered[2]


async def test_on_a_direct_call_loads_just_that_tool(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    call = ToolCall(id="r1", name="read_file", arguments={"path": "note.txt"})
    _, provider = await _run(tmp_path, [[call], []], on_demand=True)

    assert "read_file" in provider.offered[1]
    assert "write_file" not in provider.offered[1]


async def test_on_load_tools_does_not_exist(tmp_path) -> None:
    call = ToolCall(id="g1", name="load_tools", arguments={"group": "browser"})
    _, provider = await _run(tmp_path, [[call], []], on_demand=True)

    assert "load_tools" not in provider.offered[0]
    assert "browser_open" not in provider.offered[1]
    assert "Unknown tool" in provider.results[1][-1]


async def test_a_disabled_tool_is_neither_listed_nor_loadable(tmp_path) -> None:
    provider = _ScriptedProvider([[_load("read_file")], []])
    config = _config(tmp_path, on_demand=True)
    config.disabled_tools = ["read_file"]
    app = build_application(config=config, provider=provider)
    await app.start()
    await app.submit_user_message("go")
    await app.close()

    assert "- read_file:" not in provider.prompts[0]
    assert "read_file" not in provider.offered[1]


async def test_switching_it_on_takes_effect_at_the_next_message(tmp_path) -> None:
    provider = _ScriptedProvider([[], []])
    app = build_application(config=_config(tmp_path, on_demand=False), provider=provider)
    await app.start()
    await app.submit_user_message("primeira")
    app.session.config.experimental.on_demand_tools = True
    await app.submit_user_message("segunda")
    app.session.config.experimental.on_demand_tools = False
    await app.submit_user_message("terceira")
    await app.close()

    assert "read_file" in provider.offered[0]
    assert "read_file" not in provider.offered[1]
    assert "call load_tool with its exact name" in provider.prompts[1]
    assert "read_file" in provider.offered[2]
    assert "Tool groups:" in provider.prompts[2]


async def test_without_an_enabled_load_tool_it_falls_back_to_groups(tmp_path) -> None:
    provider = _ScriptedProvider([[]])
    config = _config(tmp_path, on_demand=True)
    config.disabled_tools = ["load_tool"]
    app = build_application(config=config, provider=provider)
    await app.start()
    await app.submit_user_message("go")
    await app.close()

    assert {"read_file", "load_tools"} <= provider.offered[0]
    assert "Tool groups:" in provider.prompts[0]
