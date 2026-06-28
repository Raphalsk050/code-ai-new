"""Regression tests built from documented real-world Qwen tool-call failures.

Each case reproduces the verbatim (or format-exact) output reported in a public
issue where a serving backend failed to parse a Qwen tool call and let it leak
into the content. The pipeline here mirrors the orchestrator's streaming text
handling (reasoning split -> tool-call suppression -> recovery) so the assertion
is end-to-end: the call is recovered and executed, and nothing leaks to chat.

Sources:
- vLLM #31871  https://github.com/vllm-project/vllm/issues/31871
- ollama #14493 https://github.com/ollama/ollama/issues/14493
- ollama #14745 https://github.com/ollama/ollama/issues/14745
- llama.cpp #20837 https://github.com/ggml-org/llama.cpp/issues/20837
- Qwen3.6 jinja fix https://allanchan339.github.io/bug-fixes/2026/05/02/Qwen36-27B-updated-jinja.html
"""

from __future__ import annotations

from code_ai.providers.reasoning import ReasoningTagFilter
from code_ai.providers.tool_recovery import (
    ToolCallStreamFilter,
    looks_like_attempted_tool_call,
    recover_tool_calls_from_text,
)


def _run(deltas: list[str], known: set[str]):
    """Replicate orchestrator _collect_model_response + tool-call recovery.

    Returns ``(recovered_calls, visible_chat_text)``.
    """
    reasoning_filter = ReasoningTagFilter()
    text_filter = ToolCallStreamFilter()
    answer_parts: list[str] = []
    reasoning_parts: list[str] = []
    visible: list[str] = []

    for delta in deltas:
        answer, thought = reasoning_filter.feed(delta)
        if thought:
            reasoning_parts.append(thought)
        if answer:
            answer_parts.append(answer)
            shown = text_filter.feed(answer)
            if shown:
                visible.append(shown)
    _, reasoning_tail = reasoning_filter.flush()
    if reasoning_tail:
        reasoning_parts.append(reasoning_tail)
    text_filter.flush()

    answer_text = "".join(answer_parts)
    reasoning_text = "".join(reasoning_parts)
    calls, _ = recover_tool_calls_from_text(answer_text, known)
    if not calls and looks_like_attempted_tool_call(reasoning_text):
        calls, _ = recover_tool_calls_from_text(reasoning_text, known)
    return calls, "".join(visible)


def _assert_weather(calls, visible) -> None:
    assert [c.name for c in calls] == ["get_weather"]
    assert calls[0].arguments == {"city": "Beijing"}
    assert "<tool_call>" not in visible
    assert "<function=" not in visible
    assert "</think>" not in visible


def test_vllm_31871_hermes_json_fragmented_stream() -> None:
    # vLLM #31871: streaming handler left the Hermes JSON call in `content`
    # with finish_reason=stop. Fragments exactly as reported.
    deltas = [
        "<tool_call>",
        "\n",
        '{"name": "get_weather", "arguments": {"city": "Beijing"}}',
        "\n",
        "</tool_call>",
    ]
    _assert_weather(*_run(deltas, {"get_weather"}))


def test_qwen3_coder_xml_format() -> None:
    # ollama #14493: Qwen 3.5/3.6 were trained on the Qwen3-Coder XML format
    # <function=..><parameter=..> rather than Hermes JSON.
    raw = (
        "<tool_call>\n<function=get_weather>\n"
        "<parameter=city>Beijing</parameter>\n</function>\n</tool_call>"
    )
    _assert_weather(*_run(list(raw), {"get_weather"}))


def test_qwen35_tool_call_inside_unclosed_thinking_block() -> None:
    # llama.cpp #20837 / ollama #14745: with thinking enabled the model prints
    # the call inside the think block (no </think>) and then "stops".
    raw = (
        "<think>\nThe user wants weather. I will call the tool.\n"
        "<tool_call>\n<function=get_weather>\n"
        "<parameter=city>Beijing</parameter>\n</function>\n</tool_call>"
    )
    _assert_weather(*_run(list(raw), {"get_weather"}))


def test_interleaved_call_before_think_close() -> None:
    # Qwen 3.6 interleaved shape: <tool_call> emitted before </think>.
    raw = (
        "<think>reasoning<tool_call>\n"
        '{"name":"get_weather","arguments":{"city":"Beijing"}}\n'
        "</tool_call></think>"
    )
    _assert_weather(*_run(list(raw), {"get_weather"}))
