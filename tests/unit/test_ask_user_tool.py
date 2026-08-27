from __future__ import annotations

import asyncio

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.interaction import AskUserTool
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path, sink: list | None = None) -> ToolContext:
    bus = AsyncEventBus(session_id="session")
    if sink is not None:
        bus.subscribe(sink.append)
    return ToolContext(
        config=AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)}),
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=bus,
        cancel_event=asyncio.Event(),
    )


def test_the_schema_stays_answerable_by_a_small_model() -> None:
    schema = AskUserTool.input_schema
    properties = schema["properties"]

    assert set(properties) == {
        "question",
        "header",
        "options",
        "multi_select",
        "allow_other",
        "why_required",
    }
    # Options are a flat list of strings, never a list of objects: the schemas
    # here stay atomic so small local models can fill them in.
    assert properties["options"]["items"] == {"type": "string"}
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(properties)


def test_the_description_tells_the_model_to_batch_independent_questions() -> None:
    assert "once per unknown in the same step" in AskUserTool.description


async def test_a_question_is_emitted_with_everything_a_card_needs(tmp_path) -> None:
    events: list = []
    context = make_context(tmp_path, events)

    result = await AskUserTool().execute(
        {
            "question": "Qual banco?",
            "header": "Banco",
            "options": ["Postgres :: consistência forte", "SQLite"],
            "multi_select": True,
        },
        context,
    )

    assert result["status"] == "blocked"
    assert result["header"] == "Banco"
    assert result["multi_select"] is True
    emitted = [e for e in events if e.event_type == "interaction.question.requested"]
    assert len(emitted) == 1
    assert emitted[0].payload["options"] == ["Postgres :: consistência forte", "SQLite"]


async def test_an_unstated_reason_still_says_why_the_user_was_interrupted(tmp_path) -> None:
    result = await AskUserTool().execute({"question": "Qual banco?"}, make_context(tmp_path))

    assert result["why_required"].strip()


async def test_a_question_with_no_prompt_is_rejected(tmp_path) -> None:
    with pytest.raises(ToolArgumentError):
        await AskUserTool().execute({"question": "   "}, make_context(tmp_path))


async def test_a_malformed_options_list_is_rejected_rather_than_dropped(tmp_path) -> None:
    # Silently showing a question with no cards would hide the model's mistake.
    with pytest.raises(ToolArgumentError):
        await AskUserTool().execute(
            {"question": "Qual banco?", "options": [{"label": "Postgres"}]},
            make_context(tmp_path),
        )


async def test_blank_options_are_tolerated_because_that_is_only_sloppiness(tmp_path) -> None:
    result = await AskUserTool().execute(
        {"question": "Qual banco?", "options": ["Postgres", "", "  "]},
        make_context(tmp_path),
    )

    assert result["options"] == ["Postgres"]
