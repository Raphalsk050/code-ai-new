from __future__ import annotations

import asyncio

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.internal import (
    CompleteTaskTool,
    FinishDiscoveryTool,
    RequestExternalGapTool,
    SubmitPlanTool,
)
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


async def test_submit_plan_returns_cleaned_step_titles(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = SubmitPlanTool()

    assert tool.input_schema["required"] == ["steps"]
    assert tool.input_schema["properties"]["steps"]["type"] == "array"

    result = await tool.execute(
        {"steps": ["  Read ROADMAP.md ", "", "Implement the section"]}, context
    )

    assert result["steps"] == ["Read ROADMAP.md", "Implement the section"]


async def test_submit_plan_rejects_empty_steps(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = SubmitPlanTool()

    try:
        await tool.execute({"steps": []}, context)
    except ToolArgumentError:
        pass
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected ToolArgumentError for empty steps")


async def test_complete_task_defaults_to_success_with_summary_only(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = CompleteTaskTool()

    # strict-mode: every property is listed in required; only summary is a genuine
    # input, so outcome and the blocked/failed detail fields are nullable.
    assert set(tool.input_schema["required"]) == {
        "summary",
        "outcome",
        "remaining_issues",
        "limitations",
        "double_check_acknowledged",
    }
    assert set(tool.input_schema["properties"]) == {
        "summary",
        "outcome",
        "remaining_issues",
        "limitations",
        "double_check_acknowledged",
    }
    assert tool.input_schema["properties"]["outcome"]["type"] == ["string", "null"]
    assert tool.input_schema["properties"]["remaining_issues"]["type"] == ["array", "null"]
    assert tool.input_schema["properties"]["limitations"]["type"] == ["array", "null"]
    assert tool.input_schema["properties"]["double_check_acknowledged"]["type"] == [
        "boolean",
        "null",
    ]

    result = await tool.execute({"summary": "Done."}, context)

    assert result["outcome"] == "success"
    assert result["summary"] == "Done."
