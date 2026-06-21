from __future__ import annotations

import asyncio

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.internal import CompleteTaskTool, FinishDiscoveryTool, RequestExternalGapTool
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


async def test_finish_discovery_schema_and_execution_are_summary_only(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = FinishDiscoveryTool()

    assert set(tool.input_schema["properties"]) == {"summary"}
    assert tool.input_schema["properties"]["summary"]["type"] == "string"
    assert tool.input_schema["properties"]["summary"]["description"]

    result = await tool.execute({"summary": "Workspace inspected."}, context)

    assert result["summary"] == "Workspace inspected."
    assert result["external_knowledge_gaps"] == []


async def test_request_external_gap_returns_planner_gap_payload(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = RequestExternalGapTool()

    assert tool.input_schema["required"] == ["question", "reason"]

    result = await tool.execute(
        {
            "question": "Which package version documents this behavior?",
            "reason": "Local files identify the package but not the current version docs.",
        },
        context,
    )

    assert result["external_knowledge_gaps"][0]["question"].startswith("Which package")
    assert "current version" in result["external_knowledge_gaps"][0][
        "why_local_files_are_insufficient"
    ]


async def test_complete_task_defaults_to_success_with_summary_only(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = CompleteTaskTool()

    # strict-mode: both properties are required; the optional outcome is nullable.
    assert set(tool.input_schema["required"]) == {"summary", "outcome"}
    assert set(tool.input_schema["properties"]) == {"summary", "outcome"}
    assert tool.input_schema["properties"]["outcome"]["type"] == ["string", "null"]

    result = await tool.execute({"summary": "Done."}, context)

    assert result["outcome"] == "success"
    assert result["summary"] == "Done."
