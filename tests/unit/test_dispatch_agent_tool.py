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


class _StubBudgets:
    max_tool_output_chars = 12000


class _StubConfig:
    budgets = _StubBudgets()


def _context(dispatcher, *, depth=0) -> ToolContext:
    return ToolContext(
        config=_StubConfig(),
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
    # The summary is fenced so the parent reads it as reported data rather than
    # as another instruction in its own conversation.
    assert "handled add a flag" in result["reports"][1]["summary"]
    assert result["reports"][1]["summary"].startswith("<subagent_report>")
    assert result["note"]
    # Requests were parsed into typed objects and the caller depth forwarded.
    sent, depth = dispatcher.calls[0]
    assert [r.agent_type for r in sent] == ["explorer", "coder"]
    assert depth == 0


async def test_report_payload_is_bounded_per_report() -> None:
    """A large fan-out must degrade into shorter summaries, not a mangled blob."""

    class _VerboseDispatcher(_RecordingDispatcher):
        async def dispatch(self, requests, *, cancel_event=None, depth=0):
            return [
                SubagentReport(
                    agent_id=f"a{i}",
                    agent_type=req.agent_type,
                    task="t" * 5000,
                    status=SubagentStatus.COMPLETED,
                    summary="s" * 50000,
                )
                for i, req in enumerate(requests)
            ]

    tool = _tool()
    tasks = [{"agent_type": "explorer", "prompt": f"q{i}"} for i in range(6)]
    result = await tool.execute({"tasks": tasks}, _context(_VerboseDispatcher()))

    budget = _StubBudgets.max_tool_output_chars
    for report in result["reports"]:
        assert len(report["task"]) < 400  # short echo, not the full prompt
        assert len(report["summary"]) <= budget // 6 + 100
    # Every report keeps a useful minimum even when the split is tight.
    assert all(len(r["summary"]) >= 900 for r in result["reports"])


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


async def test_expected_outcome_travels_inside_the_brief() -> None:
    dispatcher = _RecordingDispatcher()
    tool = _tool()
    await tool.execute(
        {
            "tasks": [
                {
                    "agent_type": "coder",
                    "prompt": "add a /health endpoint in src/api.py",
                    "expected_outcome": "GET /health returns 200 and tests pass",
                },
                {"agent_type": "explorer", "prompt": "map the router"},
            ]
        },
        _context(dispatcher),
    )

    sent, _ = dispatcher.calls[0]
    assert "Expected outcome" in sent[0].prompt
    assert "GET /health returns 200" in sent[0].prompt
    # Tasks without the field keep their prompt untouched.
    assert sent[1].prompt == "map the router"
