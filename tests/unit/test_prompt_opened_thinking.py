"""A template that opens the think block in the prompt (Qwen3.6+): the reply
starts mid-thought and only closes the block, so without a reasoning parser on
the server the thinking arrives as plain content up to a bare </think>."""

from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)
from code_ai.providers.reasoning import (
    ReasoningTagFilter,
    settle_reasoning,
    split_reasoning_tags,
)
from code_ai.ui.terminal.view_models import TerminalViewModel

_THOUGHT = "O usuario quer um oi. Vou responder curto.\n"
_ANSWER = "Oi! Tudo certo?"
_REPLY = _THOUGHT + "</think>\n\n" + _ANSWER


def _pieces(text: str, size: int = 7) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _feed(flt: ReasoningTagFilter, pieces: list[str]) -> tuple[str, str, int]:
    """Stream ``pieces`` the way the orchestrator does, voiding the answer so
    far whenever the filter reroutes."""
    answer, reasoning, reroutes = "", "", 0
    for piece in pieces:
        a, r = flt.feed(piece)
        if flt.rerouted:
            reroutes += 1
            answer = ""
        answer += a
        reasoning += r
    a, r = flt.flush()
    return answer + a, reasoning + r, reroutes


# --- the filter ---------------------------------------------------------------


def test_a_close_nothing_opened_turns_what_came_before_into_reasoning() -> None:
    answer, reasoning, reroutes = _feed(ReasoningTagFilter(), _pieces(_REPLY))

    assert reasoning == _THOUGHT
    assert answer.strip() == _ANSWER
    assert reroutes == 1


def test_opened_routes_the_reasoning_from_the_first_token() -> None:
    answer, reasoning, reroutes = _feed(ReasoningTagFilter(opened=True), _pieces(_REPLY))

    assert reroutes == 0
    assert reasoning == _THOUGHT
    assert answer.strip() == _ANSWER


def test_a_call_made_mid_thought_still_reaches_the_answer_channel() -> None:
    # Qwen calls the tool before closing the block; the close comes after it.
    call = "<tool_call>\n<function=read_file>\n</function>\n</tool_call>"
    answer, reasoning = split_reasoning_tags("Vou ler o arquivo.\n" + call + "\n</think>")

    assert reasoning == "Vou ler o arquivo.\n"
    assert answer.strip() == call


def test_the_finished_text_splits_the_same_way() -> None:
    assert split_reasoning_tags(_REPLY) == ("\n\n" + _ANSWER, _THOUGHT)
    # Opened and never closed: all of it was thinking (cut short, or a call).
    assert split_reasoning_tags("ainda pensando", opened=True) == ("", "ainda pensando")
    assert split_reasoning_tags("so a resposta") == ("so a resposta", "")


def test_a_summary_from_complete_loses_the_thinking_before_it() -> None:
    response = settle_reasoning(ModelResponse(text="Pensando no que manter.\n</think>\n\n# Resumo"))

    assert response.text.strip() == "# Resumo"
    assert response.reasoning == "Pensando no que manter.\n"


# --- through the orchestrator -------------------------------------------------------


def _as_content(text: str) -> list[ProviderEvent]:
    """What vLLM streams for the reply without a reasoning parser."""
    events = [ProviderEvent(kind="text_delta", text_delta=piece) for piece in _pieces(text)]
    done = ModelResponse(text=text, finish_reason=FinishReason.STOP)
    return [*events, ProviderEvent(kind="completed", response=done)]


def _split_by_server(reasoning: str, answer: str) -> list[ProviderEvent]:
    """The same reply from a server that parses the reasoning out itself."""
    done = ModelResponse(text=answer, reasoning=reasoning, finish_reason=FinishReason.STOP)
    return [
        ProviderEvent(kind="reasoning_delta", reasoning_delta=reasoning),
        ProviderEvent(kind="text_delta", text_delta=answer),
        ProviderEvent(kind="completed", response=done),
    ]


class _Scripted:
    def __init__(self, *replies: list[ProviderEvent]) -> None:
        self.replies = list(replies)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        for event in self.replies.pop(0) if self.replies else _as_content(_REPLY):
            yield event

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="summary")

    async def close(self) -> None:
        return None


def _app(tmp_path, provider, **overrides):
    data = {
        "api_mode": "completions",
        "workspace": str(tmp_path),
        "model": "qwen3.8-27b",
        "permission_mode": "bypass",
        "planner": {"enabled": False},
        "memories_dir": str(tmp_path / "memories"),
        "memory": {"reflection_enabled": False},
        # Thinking is off by default; these replies think.
        "sampling": {"reasoning_effort": "medium"},
    }
    data.update(overrides)
    return build_application(config=AppConfig.from_mapping(data), provider=provider)


def _record(app) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    app.subscribe(lambda event: seen.append((event.event_type, str(event.payload.get("text", "")))))
    return seen


def _text(seen: list[tuple[str, str]], kind: str) -> str:
    return "".join(text for name, text in seen if name == kind)


def _count(seen: list[tuple[str, str]], kind: str) -> int:
    return sum(1 for name, _ in seen if name == kind)


async def test_the_thinking_reaches_the_panel_and_stays_out_of_the_answer(tmp_path) -> None:
    app = _app(tmp_path, _Scripted())
    seen = _record(app)
    await app.start()

    first = await app.submit_user_message("oi")

    # The first reply is caught at its </think>: what streamed as the answer
    # until then is taken back and sent as thinking.
    assert _count(seen, "model.reasoning.recovered") == 1
    assert _text(seen, "model.thinking.delta") == _THOUGHT
    assert first.text.strip() == _ANSWER

    seen.clear()
    second = await app.submit_user_message("de novo")
    await app.close()

    # From then on the thinking never touches the answer channel.
    assert _count(seen, "model.reasoning.recovered") == 0
    assert _THOUGHT.strip() not in _text(seen, "model.stream.delta")
    assert _text(seen, "model.thinking.delta") == _THOUGHT
    assert second.text.strip() == _ANSWER
    # The history keeps the answers, not the thinking in front of them.
    said = [m.content for m in app.orchestrator.conversation.messages if m.role == "assistant"]
    assert said and all("</think>" not in text and "Vou responder" not in text for text in said)


async def test_with_thinking_switched_off_the_reply_is_all_answer(tmp_path) -> None:
    # Effort "none" sends enable_thinking=false: the template closes the block
    # itself, whatever an earlier reply taught.
    app = _app(
        tmp_path,
        _Scripted(_as_content(_REPLY), _as_content("Resposta direta.")),
        sampling={"reasoning_effort": "none"},
    )
    seen = _record(app)
    await app.start()
    await app.submit_user_message("oi")
    seen.clear()

    second = await app.submit_user_message("de novo")
    await app.close()

    assert second.text.strip() == "Resposta direta."
    assert _text(seen, "model.stream.delta") == "Resposta direta."
    assert _text(seen, "model.thinking.delta") == ""


async def test_a_reply_that_never_closes_the_block_is_still_the_answer(tmp_path) -> None:
    app = _app(tmp_path, _Scripted(_as_content(_REPLY), _as_content("Resposta sem pensar.")))
    await app.start()
    await app.submit_user_message("oi")

    second = await app.submit_user_message("de novo")
    await app.close()

    assert second.text.strip() == "Resposta sem pensar."
    # And the next reply is read from its start again, not assumed opened.
    assert not app.orchestrator._thinking_opens_in_prompt(
        ModelRequest(model="qwen3.8-27b", messages=[])
    )


async def test_a_server_that_splits_the_reasoning_itself_is_believed(tmp_path) -> None:
    app = _app(
        tmp_path,
        _Scripted(_as_content(_REPLY), _split_by_server("pensei", "Resposta.")),
    )
    seen = _record(app)
    await app.start()
    await app.submit_user_message("oi")
    seen.clear()

    second = await app.submit_user_message("de novo")
    await app.close()

    assert second.text.strip() == "Resposta."
    assert _text(seen, "model.stream.delta") == "Resposta."
    assert _text(seen, "model.thinking.delta") == "pensei"


# --- the terminal ---------------------------------------------------------------


def _event(event_type: str, payload: dict, sequence: int) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event_type,
        session_id="s",
        sequence=sequence,
        payload=payload,
        source="test",
    )


def test_the_terminal_moves_the_taken_back_text_into_the_thinking_panel() -> None:
    view = TerminalViewModel()
    script = [
        ("user.message", {"text": "oi"}),
        ("model.stream.delta", {"text": "O usuario quer", "channel": "answer"}),
        ("model.stream.delta", {"text": " um oi.", "channel": "answer"}),
        ("model.reasoning.recovered", {}),
        ("model.thinking.delta", {"text": "O usuario quer um oi."}),
        ("model.stream.delta", {"text": "Oi!", "channel": "answer"}),
    ]
    for sequence, (name, payload) in enumerate(script, 1):
        view.apply(_event(name, payload, sequence))
    view.flush_stream()

    assert view.conversation == ["you> oi", "thinking> O usuario quer um oi.", "ai> Oi!"]
    # A line the screen had already drawn is unmounted, not left behind.
    assert view.take_rewind() == 1
