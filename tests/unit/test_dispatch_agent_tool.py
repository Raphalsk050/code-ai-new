from __future__ import annotations

import pytest

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.core.subagents.coordinator import SubagentRequest
from code_ai.core.subagents.profiles import default_profile_registry
from code_ai.core.subagents.report import SubagentReport, SubagentStatus
from code_ai.tools.agents import DispatchAgentTool
from code_ai.tools.base import ToolCapability, ToolContext


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[list[SubagentRequest], int]] = []

    async def dispatch(self, requests, *, cancel_event=None, depth=0):
        self.calls.append((requests, depth))
        return [
            SubagentReport(
                agent_id=f"a{i}",
                agent_type=req.agent_type,
                task=req.prompt,
                status=SubagentStatus.COMPLETED,
                summary=f"handled {req.prompt}",
            )
            for i, req in enumerate(requests)
        ]

    def available_types_description(self) -> str:
        return ""


def _context(dispatcher, *, depth=0) -> ToolContext:
    return ToolContext(
        config=None,
        workspace=None,
        event_bus=None,
        subagent_coordinator=dispatcher,
        subagent_depth=depth,
    )


def _tool() -> DispatchAgentTool:
    return DispatchAgentTool(default_profile_registry())


def test_tool_advertises_delegate_capability_and_types() -> None:
    tool = _tool()
    assert tool.capabilities == frozenset({ToolCapability.DELEGATE})
    schema_types = tool.input_schema["properties"]["tasks"]["items"]["properties"][
        "agent_type"
    ]["enum"]
    assert set(schema_types) == {"explorer", "coder", "reviewer"}
    assert "explorer" in tool.description


async def test_dispatch_forwards_requests_and_returns_reports() -> None:
    dispatcher = _RecordingDispatcher()
    tool = _tool()
    result = await tool.execute(
        {
            "tasks": [
                {"agent_type": "explorer", "prompt": "map the config loader"},
                {"agent_type": "coder", "prompt": "add a flag"},
            ]
        },
        _context(dispatcher, depth=0),
    )

    assert result["dispatched"] == 2
    assert result["reports"][0]["agent_type"] == "explorer"
    assert result["reports"][1]["summary"] == "handled add a flag"
    # Requests were parsed into typed objects and the caller depth forwarded.
    sent, depth = dispatcher.calls[0]
    assert [r.agent_type for r in sent] == ["explorer", "coder"]
    assert depth == 0


async def test_missing_coordinator_raises() -> None:
    tool = _tool()
    with pytest.raises(ToolExecutionError, match="not available"):
        await tool.execute(
            {"tasks": [{"agent_type": "explorer", "prompt": "x"}]},
            _context(None),
        )


async def test_invalid_arguments_are_rejected() -> None:
    tool = _tool()
    dispatcher = _RecordingDispatcher()
    with pytest.raises(ToolArgumentError):
        await tool.execute({"tasks": []}, _context(dispatcher))
    with pytest.raises(ToolArgumentError):
        await tool.execute({"tasks": [{"agent_type": "explorer"}]}, _context(dispatcher))
    with pytest.raises(ToolArgumentError):
        await tool.execute({"tasks": [{"prompt": "no type"}]}, _context(dispatcher))
