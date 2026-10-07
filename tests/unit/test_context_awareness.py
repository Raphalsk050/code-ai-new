"""The agent hears about its window when it matters, and results fit what is left."""

from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.context.awareness import ContextAwareness, cut_note, result_room
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
    ToolResult,
)

# Compaction starts at 80% of a 30k budget.
_WINDOW = 30_000
_LIMIT = 24_000


def _note(awareness: ContextAwareness, used: int) -> str:
    return awareness.note(used=used, limit=_LIMIT, window=_WINDOW)


def test_with_room_to_spare_the_agent_is_told_nothing() -> None:
    awareness = ContextAwareness()

    assert _note(awareness, 2_000) == ""
    assert _note(awareness, 16_000) == ""


def test_a_mark_is_told_once_and_with_what_to_do_about_it() -> None:
    awareness = ContextAwareness()

    first = _note(awareness, 17_000)

    assert first.startswith("Context: 57% of the window is in use.")
    assert "read files in slices" in first
    # Still past the same mark: said already.
    assert _note(awareness, 18_500) == ""
    assert _note(awareness, 21_000) == ""


def test_just_before_compaction_it_is_told_again_and_not_to_stop() -> None:
    awareness = ContextAwareness()
    _note(awareness, 17_000)

    last = _note(awareness, 22_500)

    assert "about to be compacted" in last
    assert "not a reason to stop" in last
    assert _note(awareness, 23_900) == ""


def test_a_jump_straight_past_both_marks_gets_the_stronger_word_only() -> None:
    awareness = ContextAwareness()

    assert "about to be compacted" in _note(awareness, 23_000)
    assert _note(awareness, 23_500) == ""


def test_a_window_that_emptied_can_be_told_about_again() -> None:
    awareness = ContextAwareness()
    _note(awareness, 23_000)

    # A compaction brought it down to a third.
    awareness.settle(used=8_000, limit=_LIMIT)

    assert _note(awareness, 17_000) != ""


def test_a_window_that_barely_moved_is_not_told_twice() -> None:
    awareness = ContextAwareness()
    _note(awareness, 17_000)

    # Old tool output was cleared, but it is still close to the mark.
    awareness.settle(used=15_000, limit=_LIMIT)

    assert _note(awareness, 17_500) == ""


def test_a_result_gets_the_configured_cap_while_there_is_room() -> None:
    assert result_room(used=2_000, limit=_LIMIT, ceiling=12_000) == 12_000


def test_a_result_shrinks_with_the_room_left_and_never_below_a_usable_size() -> None:
    tight = result_room(used=22_000, limit=_LIMIT, ceiling=12_000)

    assert 1_500 <= tight < 12_000
    assert result_room(used=23_990, limit=_LIMIT, ceiling=12_000) == 1_500
    # Past the limit there is no room at all: the floor, not zero and not less.
    assert result_room(used=40_000, limit=_LIMIT, ceiling=12_000) == 1_500


def test_the_calls_of_one_round_share_the_room() -> None:
    alone = result_room(used=20_000, limit=_LIMIT, ceiling=12_000)
    of_three = result_room(used=20_000, limit=_LIMIT, ceiling=12_000, calls=3)

    assert of_three < alone
    assert of_three * 3 <= alone + 3


def test_a_cap_below_the_floor_is_still_the_cap() -> None:
    assert result_room(used=23_990, limit=_LIMIT, ceiling=800) == 800


def test_a_cut_says_how_much_is_missing_and_how_to_get_it() -> None:
    note = cut_note(1_500, 9_000)

    assert "1500 of its 9000 characters" in note
    assert "line range" in note


# ---- through the agent's own loop ----


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


class _ReadThenAnswer:
    """Reads one file, then answers; keeps what it was sent each time."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        if request.tools:
            self.requests.append(request)
        if len(self.requests) == 1:
            calls = [ToolCall(id="r1", name="read_file", arguments={"path": "big.txt"})]
            response = ModelResponse(tool_calls=calls, finish_reason=FinishReason.TOOL_CALLS)
        else:
            response = ModelResponse(text="done", finish_reason=FinishReason.STOP)
        yield ProviderEvent(kind="completed", response=response)


async def test_a_mark_crossed_is_said_once_on_the_newest_tool_result(tmp_path) -> None:
    app = build_application(config=_config(tmp_path), provider=_ReadThenAnswer())
    orchestrator = app.orchestrator
    conversation = orchestrator.conversation
    conversation.add_tool_result(ToolResult(tool_call_id="t1", name="read_file", content="{}"))
    orchestrator._context_now = int(orchestrator._compaction_point() * 0.75)

    orchestrator._tell_about_the_window()
    orchestrator._tell_about_the_window()

    assert conversation.messages[-1].role == "tool"
    assert conversation.messages[-1].content.startswith("{}\n\n[runtime] Context: ")
    assert conversation.messages[-1].content.count("[runtime] Context:") == 1


async def test_with_room_to_spare_a_turn_reads_as_it_always_did(tmp_path) -> None:
    (tmp_path / "big.txt").write_text("line of text\n" * 450, encoding="utf-8")
    provider = _ReadThenAnswer()
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("read big.txt")
    await app.close()

    assert result.text == "done"
    seen = next(m for m in reversed(provider.requests[-1].messages) if m.role == "tool")
    assert "[runtime]" not in seen.content
    assert seen.content.count("line of text") == 450


async def test_in_a_tight_window_the_result_is_cut_and_the_agent_told_why(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "big.txt").write_text("line of text\n" * 450, encoding="utf-8")
    provider = _ReadThenAnswer()
    app = build_application(config=_config(tmp_path), provider=provider)
    orchestrator = app.orchestrator
    # Whatever the prompt weighs, compaction is 400 tokens away.
    monkeypatch.setattr(
        orchestrator, "_compaction_point", lambda: orchestrator._context_now + 400
    )

    await app.start()
    result = await app.submit_user_message("read big.txt")
    await app.close()

    assert result.text == "done"
    seen = next(m for m in reversed(provider.requests[-1].messages) if m.role == "tool")
    # Its beginning and its end, a fraction of the 450 lines.
    assert 0 < seen.content.count("line of text") < 150
    assert "[runtime] This result was cut to fit the context: 1500 of its" in seen.content
    assert "[runtime] Context: " in seen.content