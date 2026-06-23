from __future__ import annotations

import asyncio
import shlex
import sys
from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import CancellationError, ProviderError, TransientProviderError
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.base import ToolCapability
from code_ai.tools.registry import ToolRegistry


def _config(tmp_path, **overrides) -> AppConfig:
    # These tests exercise tool execution directly, not the permission flow, so
    # they run in bypass mode (no interactive approver is attached).
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        "permission_mode": "bypass",
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class _BaseProvider:
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


class FlakyThenOkProvider(_BaseProvider):
    def __init__(self) -> None:
        self.attempts = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.attempts += 1
        if self.attempts == 1:
            raise TransientProviderError("temporary blip")
        yield ProviderEvent(kind="text_delta", text_delta="hello")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="hello", finish_reason=FinishReason.STOP),
        )


async def test_transient_provider_error_is_retried(tmp_path) -> None:
    provider = FlakyThenOkProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("Olá")
    await app.close()

    assert result.text == "hello"
    assert result.error is None
    assert provider.attempts == 2
    assert "model.request.retrying" in events


class PartialThenHangProvider(_BaseProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(kind="text_delta", text_delta="partial answer")
        await asyncio.sleep(30)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="never reached", finish_reason=FinishReason.STOP),
        )


async def test_model_timeout_salvages_streamed_text(tmp_path) -> None:
    # Tight model timeout so the hang trips the guard quickly.
    config = _config(tmp_path, budgets={"max_model_call_s": 1, "max_model_step_seconds": 1})
    provider = PartialThenHangProvider()
    app = build_application(config=config, provider=provider)

    await app.start()
    result = await app.submit_user_message("Olá")
    await app.close()

    # The turn must not crash; whatever streamed is salvaged.
    assert result.cancelled is False
    assert "partial answer" in result.text


class AlwaysFailsProvider(_BaseProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        raise ProviderError("backend is down")
        yield  # pragma: no cover - marks this as an async generator


async def test_provider_error_degrades_gracefully(tmp_path) -> None:
    provider = AlwaysFailsProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    # Must return a TurnResult carrying the error rather than raising.
    result = await app.submit_user_message("Olá")
    await app.close()

    assert result.error is not None
    assert "error" in events


class AlwaysReadsProvider(_BaseProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                tool_calls=[
                    ToolCall(id="r1", name="read_file", arguments={"path": "note.txt"})
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        )


async def test_model_step_budget_winds_down_gracefully(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    config = _config(tmp_path, budgets={"max_model_steps": 1})
    provider = AlwaysReadsProvider()
    app = build_application(config=config, provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("read the note")
    await app.close()

    assert result.cancelled is False
    assert result.error is None
    assert "turn.budget_exhausted" in events


class TextToolCallThenAnswerProvider(_BaseProvider):
    """First reply prints the tool call as text (no structured tool_calls)."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            blob = '<tool_call>{"name": "read_file", "arguments": {"path": "note.txt"}}</tool_call>'
            yield ProviderEvent(kind="text_delta", text_delta=blob)
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(text=blob, finish_reason=FinishReason.STOP),
            )
            return
        self.tool_feedback = "".join(
            m.content for m in request.messages if m.role == "tool"
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="here is the note", finish_reason=FinishReason.STOP),
        )


async def test_text_emitted_tool_call_is_recovered_and_executed(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("secret note\n", encoding="utf-8")
    provider = TextToolCallThenAnswerProvider()
    app = build_application(config=_config(tmp_path, planner={"enabled": False}), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("read note.txt")
    await app.close()

    # The call printed as text was promoted to a real tool call and executed,
    # rather than being shown to the user as the final answer.
    assert "tool.calls.recovered" in events
    assert "secret note" in provider.tool_feedback
    assert result.text == "here is the note"


class StreamedXmlToolCallProvider(_BaseProvider):
    """Streams a Qwen-style XML tool call as text deltas, then a clean answer."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_feedback = ""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            blob = (
                "Let me read it.\n<tool_call>\n<function=read_file>\n"
                "<parameter=path>note.txt</parameter>\n</function>\n</tool_call>"
            )
            # Stream it in fragments so markers straddle delta boundaries.
            for piece in (blob[i : i + 7] for i in range(0, len(blob), 7)):
                yield ProviderEvent(kind="text_delta", text_delta=piece)
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(text=blob, finish_reason=FinishReason.STOP),
            )
            return
        self.tool_feedback = "".join(m.content for m in request.messages if m.role == "tool")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="here is the note", finish_reason=FinishReason.STOP),
        )


async def test_streamed_xml_tool_call_never_leaks_into_chat(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("secret note\n", encoding="utf-8")
    provider = StreamedXmlToolCallProvider()
    app = build_application(config=_config(tmp_path, planner={"enabled": False}), provider=provider)
    deltas: list[str] = []

    def _capture(event) -> None:
        if event.event_type == "model.stream.delta":
            deltas.append(str(event.payload.get("text", "")))

    app.subscribe(_capture)

    await app.start()
    result = await app.submit_user_message("read note.txt")
    await app.close()

    streamed = "".join(deltas)
    # The visible stream must carry the prose but none of the call markup.
    assert "Let me read it." in streamed
    assert "<tool_call>" not in streamed
    assert "<function=" not in streamed
    assert "<parameter=" not in streamed
    # The call still executed and the turn finished with the real answer.
    assert "secret note" in provider.tool_feedback
    assert result.text == "here is the note"


class InlineThinkProvider(_BaseProvider):
    """Streams <think> reasoning inline in the content, like a local Qwen."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        blob = "<think>\nThe user said hi, I should greet back.\n</think>\nHello there!"
        for piece in (blob[i : i + 5] for i in range(0, len(blob), 5)):
            yield ProviderEvent(kind="text_delta", text_delta=piece)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=blob, finish_reason=FinishReason.STOP),
        )


async def test_inline_think_block_never_leaks_into_chat(tmp_path) -> None:
    provider = InlineThinkProvider()
    app = build_application(config=_config(tmp_path, planner={"enabled": False}), provider=provider)
    answer_deltas: list[str] = []
    thinking_deltas: list[str] = []

    def _capture(event) -> None:
        if event.event_type == "model.stream.delta":
            answer_deltas.append(str(event.payload.get("text", "")))
        elif event.event_type == "model.thinking.delta":
            thinking_deltas.append(str(event.payload.get("text", "")))

    app.subscribe(_capture)

    await app.start()
    result = await app.submit_user_message("hi")
    await app.close()

    answer_stream = "".join(answer_deltas)
    # The reasoning and its tags stay out of the visible answer channel.
    assert "<think>" not in answer_stream
    assert "</think>" not in answer_stream
    assert "I should greet back" not in answer_stream
    # Reasoning was routed to the thinking channel instead.
    assert "I should greet back" in "".join(thinking_deltas)
    # The final answer is clean prose, no tags.
    assert result.text.strip() == "Hello there!"
    assert "<think>" not in result.text


class ThinkWrappedToolCallProvider(_BaseProvider):
    """Emits a tool call inside an unterminated <think> block, then answers."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_feedback = ""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            # No closing </think>, so the call markup lands in the reasoning.
            blob = (
                "<think>\nI'll read it.\n<tool_call>\n<function=read_file>\n"
                "<parameter=path>note.txt</parameter>\n</function>\n</tool_call>"
            )
            yield ProviderEvent(kind="text_delta", text_delta=blob)
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(text=blob, finish_reason=FinishReason.STOP),
            )
            return
        self.tool_feedback = "".join(m.content for m in request.messages if m.role == "tool")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="here is the note", finish_reason=FinishReason.STOP),
        )


async def test_tool_call_inside_think_block_is_recovered_and_executed(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("secret note\n", encoding="utf-8")
    provider = ThinkWrappedToolCallProvider()
    app = build_application(config=_config(tmp_path, planner={"enabled": False}), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("read note.txt")
    await app.close()

    # The call hidden in the reasoning channel must still run, not stall the turn.
    assert "tool.calls.recovered" in events
    assert "secret note" in provider.tool_feedback
    assert result.text == "here is the note"


class MalformedThenCleanProvider(_BaseProvider):
    """First reply is an unparseable tool call; the retry produces a real answer."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            # Names a tool that does not exist, so recovery cannot promote it.
            blob = "<tool_call>\n<function=frobnicate>\n<parameter=x>1</parameter>\n</function>"
            yield ProviderEvent(kind="text_delta", text_delta=blob)
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(text=blob, finish_reason=FinishReason.STOP),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done now", finish_reason=FinishReason.STOP),
        )


async def test_malformed_tool_call_is_retried_not_surfaced(tmp_path) -> None:
    provider = MalformedThenCleanProvider()
    app = build_application(config=_config(tmp_path, planner={"enabled": False}), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("do the thing")
    await app.close()

    # The unparseable call triggered a re-prompt rather than leaking as the
    # answer, and the second attempt's clean reply is what the user receives.
    assert "tool.call.malformed" in events
    assert provider.calls == 2
    assert result.text == "done now"
    assert "frobnicate" not in result.text


class TwoFileMutationProvider(_BaseProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield self._tool("w1", "write_file", {"path": "a.py", "content": "A = 1\n"})
            return
        if self.calls == 2:
            # Second write happens after the planner has already moved to VERIFY.
            yield self._tool("w2", "write_file", {"path": "b.py", "content": "B = 2\n"})
            return
        if self.calls == 3:
            yield self._tool(
                "v1",
                "execute_command",
                {"command": f"{shlex.quote(sys.executable)} -c pass", "timeout": 10},
            )
            return
        yield self._tool(
            "c1",
            "complete_task",
            {
                "summary": "Created a.py and b.py.",
            },
        )

    @staticmethod
    def _tool(call_id: str, name: str, arguments: dict) -> ProviderEvent:
        return ProviderEvent(
            kind="completed",
            response=ModelResponse(
                tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        )


async def test_multi_file_change_not_blocked_by_phase(tmp_path) -> None:
    config = _config(tmp_path, planner={"double_check_completion": False})
    provider = TwoFileMutationProvider()
    app = build_application(config=config, provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message(
        "Create a.py and b.py with simple constants and verify them."
    )
    await app.close()

    # Both writes land even though the second one happens during VERIFY: advisory
    # policy no longer hard-blocks LOCAL_WRITE by phase.
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "B = 2\n"
    assert "planning.policy.denied" not in events
    assert "Created a.py and b.py." in result.text


class ParallelReadsProvider(_BaseProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.saw_both = False

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(id="r1", name="read_file", arguments={"path": "a.txt"}),
                        ToolCall(id="r2", name="read_file", arguments={"path": "b.txt"}),
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        tool_text = "".join(m.content for m in request.messages if m.role == "tool")
        self.saw_both = "AAA" in tool_text and "BBB" in tool_text
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )


async def test_parallel_read_only_tools_execute_in_one_batch(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("AAA\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("BBB\n", encoding="utf-8")
    provider = ParallelReadsProvider()
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("read a.txt and b.txt")
    await app.close()

    assert result.text == "done"
    # Both reads from the single model response were executed and fed back.
    assert provider.saw_both is True


class SleepyTool:
    name = "sleepy"
    description = "A tool that blocks until cooperatively cancelled."
    capabilities = frozenset({ToolCapability.PROCESS})
    input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

    def __init__(self) -> None:
        self.observed_cancel = False

    async def execute(self, arguments: dict, context) -> dict:
        for _ in range(400):
            if context.cancel_event is not None and context.cancel_event.is_set():
                self.observed_cancel = True
                raise CancellationError("cooperative stop")
            await asyncio.sleep(0.05)
        return {"ok": True}


class CallSleepyThenAnswerProvider(_BaseProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[ToolCall(id="s1", name="sleepy", arguments={})],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="after timeout", finish_reason=FinishReason.STOP),
        )


async def test_per_tool_timeout_cooperatively_cancels_without_killing_turn(tmp_path) -> None:
    # Planner disabled to isolate the tool-guard behaviour; tight tool wall budget.
    config = _config(
        tmp_path,
        planner={"enabled": False},
        budgets={"max_tool_wall_time_s": 1},
    )
    provider = CallSleepyThenAnswerProvider()
    sleepy = SleepyTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(sleepy)
    app.orchestrator.tool_registry = registry
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("run the sleepy tool")
    await app.close()

    # The tool was cancelled cooperatively (it raised on the child cancel event),
    # the timeout surfaced as a tool error, and the turn still finished.
    assert sleepy.observed_cancel is True
    assert "tool.call.failed" in events
    assert result.cancelled is False
    assert result.text == "after timeout"
