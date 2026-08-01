from __future__ import annotations

import asyncio
import shlex
import sys
from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import CancellationError, ProviderError, TransientProviderError
from code_ai.core.memory import FailureMemoryStore
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
        # Keep learned failure memories out of the user's real config dir.
        "memories_dir": str(tmp_path / "memories"),
        # These tests script exact provider call sequences; the post-turn
        # reflection meta-call would add calls the scripts do not expect.
        "memory": {"reflection_enabled": False},
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


async def test_recovery_falls_back_to_registry_when_no_tools_offered(tmp_path) -> None:
    # A turn misclassified as chat can reach the model with an empty tool list.
    # If the model still prints a call as text, recovery must fall back to the
    # full registry so the markup is stripped instead of leaking into the chat.
    app = build_application(config=_config(tmp_path), provider=_BaseProvider())
    orchestrator = app.orchestrator
    await app.start()
    recovered_events: list[dict] = []
    app.subscribe(
        lambda event: recovered_events.append(event.payload)
        if event.event_type == "tool.calls.recovered"
        else None
    )

    leaked = (
        "Sure, I'll handle it.\n<tool_call>\n<function=read_file>\n"
        "<parameter=path>note.txt</parameter>\n</function>\n</tool_call>"
    )
    response = ModelResponse(text=leaked, finish_reason=FinishReason.STOP)

    # An empty tool_definitions is exactly the misclassified-turn case.
    await orchestrator._recover_text_tool_calls(response, tool_definitions=[])
    await app.close()

    assert [call.name for call in response.tool_calls] == ["read_file"]
    assert "<tool_call>" not in response.text
    assert "<function=" not in response.text
    assert response.text.strip() == "Sure, I'll handle it."
    assert recovered_events and recovered_events[0]["names"] == ["read_file"]


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


class ReasoningMentionsToolProvider(_BaseProvider):
    """A real model: structured channel unused, reasoning is natural language.

    Its reasoning quotes a JSON blob that names a tool but it is NOT calling
    anything — it answers directly. This must not be misread as a tool call.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        reasoning = (
            'I considered {"name": "read_file", "arguments": {"path": "a.py"}} '
            "but the user only wants an explanation, so I will answer directly."
        )
        yield ProviderEvent(kind="reasoning_delta", reasoning_delta=reasoning)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="read_file opens a file and returns its contents.",
                reasoning=reasoning,
                finish_reason=FinishReason.STOP,
            ),
        )


async def test_natural_language_reasoning_is_not_misread_as_tool_call(tmp_path) -> None:
    provider = ReasoningMentionsToolProvider()
    app = build_application(config=_config(tmp_path, planner={"enabled": False}), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("what does read_file do?")
    await app.close()

    # No explicit tool-call markup in the reasoning, so nothing is recovered and
    # the model only ran once: its direct answer is surfaced as-is.
    assert "tool.calls.recovered" not in events
    assert provider.calls == 1
    assert result.text == "read_file opens a file and returns its contents."


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
    # Generator-less memory store: recording the malformed-call lesson must not
    # fire a model meta-call through the scripted provider and skew the count.
    app = build_application(
        config=_config(tmp_path, planner={"enabled": False}),
        provider=provider,
        failure_memory=FailureMemoryStore(tmp_path / "mem"),
    )
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


class AnswersInProseProvider(_BaseProvider):
    """Always answers in prose, never emitting a structured tool call."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="The adder function returns the sum of its two arguments.",
                finish_reason=FinishReason.STOP,
            ),
        )


async def test_explanation_question_is_answered_directly_without_nudging(tmp_path) -> None:
    # "explain what the adder function does" used to trip the "add" mutation
    # marker by substring; word-boundary matching plus the explanation-start
    # veto now classify it as a question, so the prose answer is accepted on
    # the very first round - no corrective nudge, no evidence demands.
    provider = AnswersInProseProvider()
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("explain what the adder function does")
    await app.close()

    assert result.error is None
    assert result.text == "The adder function returns the sum of its two arguments."
    assert provider.calls == 1


async def test_misclassified_mutation_is_answered_not_forced_into_tools(tmp_path) -> None:
    # A genuinely mutation-classified request answered only in prose must be
    # nudged toward tools at most once, then have *its* answer surfaced — not
    # be spiralled into a system correction delivered to the user as the reply.
    provider = AnswersInProseProvider()
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("update the adder function docs")
    await app.close()

    assert result.error is None
    assert result.text == "The adder function returns the sum of its two arguments."
    # One nudge round, then the model's own answer is accepted (not an endless loop).
    assert provider.calls == 2


class WritesThenAnswersProvider(_BaseProvider):
    """Changes a file, then ends the turn in prose without ever verifying.

    The dominant real shape: weak models never call ``complete_task``, so the
    evidence gate behind it never runs and the turn simply stops.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="w1",
                            name="write_file",
                            arguments={"path": "mod.py", "content": "VALUE = 1\n"},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Done, mod.py now has VALUE.", finish_reason=FinishReason.STOP
            ),
        )


async def test_unverified_change_ending_in_prose_is_nudged_once(tmp_path) -> None:
    # A pyproject makes pytest detectable, so verification is something the
    # agent could actually run here.
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    provider = WritesThenAnswersProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("crie mod.py com a constante VALUE")
    await app.close()

    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert "planning.verification_debt.nudged" in events
    # One nudge, then the model's own answer stands: the checkpoint never traps
    # the turn, exactly like the completion gate's fail-open release.
    assert provider.calls == 3
    assert result.text == "Done, mod.py now has VALUE."
    assert events.count("planning.verification_debt.nudged") == 1


async def test_read_only_prose_answer_is_not_nudged_for_verification(tmp_path) -> None:
    # The regression half of the checkpoint: it keys off observed change, so a
    # question that changed nothing must cost no extra round-trip.
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    provider = AnswersInProseProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("explain what the adder function does")
    await app.close()

    assert "planning.verification_debt.nudged" not in events
    assert provider.calls == 1
    assert result.text == "The adder function returns the sum of its two arguments."


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


class ChecklistThenProseProvider(_BaseProvider):
    """Submits a checklist, declares every step done, then answers in prose.

    Mirrors the field failure: the final complete_plan_step cannot advance (the
    last step only settles with the whole plan), the model believes it is done
    and ends the turn with a prose answer instead of complete_task.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            calls = [
                ToolCall(
                    id="c1",
                    name="submit_plan",
                    arguments={
                        "steps": ["Inspect the workspace", "Present the summary"]
                    },
                )
            ]
        elif self.calls == 2:
            calls = [
                ToolCall(
                    id="c2",
                    name="complete_plan_step",
                    arguments={"completed_step": "Inspect the workspace"},
                )
            ]
        elif self.calls == 3:
            calls = [
                ToolCall(
                    id="c3",
                    name="complete_plan_step",
                    arguments={"completed_step": "Present the summary"},
                )
            ]
        else:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    text="Here is the architecture summary.",
                    finish_reason=FinishReason.STOP,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                tool_calls=calls, finish_reason=FinishReason.TOOL_CALLS
            ),
        )


async def test_prose_finish_settles_fully_declared_checklist(tmp_path) -> None:
    # Field regression: an analysis turn whose model completed every checklist
    # step via complete_plan_step and then answered in prose left the sidebar
    # frozen at N-1/N with the last step spinning, because only complete_task
    # used to settle the plan.
    provider = ChecklistThenProseProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list = []
    app.subscribe(lambda event: events.append(event))

    await app.start()
    result = await app.submit_user_message(
        "analyze the repository architecture and present a summary"
    )
    await app.close()

    assert result.error is None
    assert result.text == "Here is the architecture summary."

    # The final complete_plan_step result is honest about not advancing.
    step_results = [
        event.payload.get("result")
        for event in events
        if event.event_type == "tool.call.completed"
        and event.payload.get("name") == "complete_plan_step"
    ]
    assert len(step_results) == 2
    assert "status" not in step_results[0]
    assert step_results[1]["status"] == "final_step_still_running"

    # The prose ending settles the checklist so the sidebar shows it complete.
    completed = [e for e in events if e.event_type == "planning.plan.completed"]
    assert completed
    assert completed[-1].payload["status"] == "COMPLETED"
    assert completed[-1].payload["progress"] == "2/2"


class AnnounceThenHangProvider(_BaseProvider):
    """Announces a write, starts streaming its call, then stalls mid-call.

    The shape a real model produces on a large file: prose first, then the
    arguments dribbling in. The stall stands in for whatever cuts a stream off
    part-way - the step budget expiring, the endpoint dropping the connection.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        arguments = '{"path": "app.py", "content": "x = 1\\n"}'
        if self.calls == 1:
            yield ProviderEvent(kind="text_delta", text_delta="I'll implement app.py now.")
            yield ProviderEvent(
                kind="tool_call_delta",
                tool_call_name="write_file",
                tool_call_arguments=arguments[:20],
                tool_call_index=0,
            )
            await asyncio.sleep(30)
            return
        if self.calls == 2:
            yield ProviderEvent(
                kind="tool_call_delta",
                tool_call_name="write_file",
                tool_call_arguments=arguments,
                tool_call_index=0,
            )
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="write_file",
                            arguments={"path": "app.py", "content": "x = 1\n"},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="app.py is written.", finish_reason=FinishReason.STOP),
        )


async def test_tool_call_cut_off_mid_stream_is_reissued(tmp_path) -> None:
    # A step that dies mid-call leaves only the announcement behind. Surfacing
    # that as the answer is how the agent used to end a turn in waiting_user
    # having written nothing, so the call must be asked for again instead.
    config = _config(
        tmp_path,
        planner={"enabled": False},
        budgets={"max_model_call_s": 1, "max_model_step_seconds": 1},
    )
    provider = AnnounceThenHangProvider()
    app = build_application(config=config, provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("implement app.py")
    await app.close()

    assert "tool.call.interrupted" in events
    assert provider.calls == 3
    # The write actually happened, and the announcement is not the answer.
    assert (tmp_path / "app.py").read_text() == "x = 1\n"
    assert result.text == "app.py is written."


class LostCallThenCleanProvider(_BaseProvider):
    """Streams a tool call, then completes without it.

    This is what a provider does when it cannot parse the arguments it just
    received: it drops the call with a warning. The response that reaches the
    orchestrator is indistinguishable from a plain prose answer unless the
    streamed call is accounted for.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(kind="text_delta", text_delta="Writing the file now.")
            yield ProviderEvent(
                kind="tool_call_delta",
                tool_call_name="write_file",
                tool_call_arguments='{"path": "app.py", "content": "x = 1',
                tool_call_index=0,
            )
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    text="Writing the file now.", finish_reason=FinishReason.TOOL_CALLS
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="All done.", finish_reason=FinishReason.STOP),
        )


async def test_dropped_tool_call_is_reissued(tmp_path) -> None:
    provider = LostCallThenCleanProvider()
    app = build_application(
        config=_config(tmp_path, planner={"enabled": False}), provider=provider
    )
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("write app.py")
    await app.close()

    assert "tool.call.interrupted" in events
    assert provider.calls == 2
    assert result.text == "All done."


class AlwaysLosesCallProvider(_BaseProvider):
    """Never manages to deliver the call it keeps starting."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent(kind="text_delta", text_delta="Writing it now.")
        yield ProviderEvent(
            kind="tool_call_delta",
            tool_call_name="write_file",
            tool_call_arguments='{"path": "app.py"',
            tool_call_index=0,
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Writing it now.", finish_reason=FinishReason.TOOL_CALLS
            ),
        )


async def test_repeatedly_lost_tool_call_stops_re_prompting(tmp_path) -> None:
    # Bounded: a stream that keeps breaking must still end the turn with a
    # best-effort reply rather than re-prompting forever.
    provider = AlwaysLosesCallProvider()
    app = build_application(
        config=_config(tmp_path, planner={"enabled": False}), provider=provider
    )
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("write app.py")
    await app.close()

    assert events.count("tool.call.interrupted") == 2
    assert provider.calls == 3
    assert result.text == "Writing it now."


class ProseOnlyProvider(_BaseProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(kind="text_delta", text_delta="No tool needed here.")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="No tool needed here.", finish_reason=FinishReason.STOP
            ),
        )


async def test_plain_prose_answer_is_not_treated_as_interrupted(tmp_path) -> None:
    # Nothing streamed a call, so the prose is the answer and must be delivered
    # on the first step - the guard must not re-prompt every chat reply.
    provider = ProseOnlyProvider()
    app = build_application(
        config=_config(tmp_path, planner={"enabled": False}), provider=provider
    )
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("hello")
    await app.close()

    assert "tool.call.interrupted" not in events
    assert result.text == "No tool needed here."
