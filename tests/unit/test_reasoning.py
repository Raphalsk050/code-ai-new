from __future__ import annotations

from code_ai.providers.reasoning import ReasoningTagFilter, split_reasoning_tags


def _stream(deltas: list[str]) -> tuple[str, str]:
    """Feed ``deltas`` through the filter; return ``(answer, reasoning)``."""
    flt = ReasoningTagFilter()
    answer, reasoning = "", ""
    for delta in deltas:
        a, r = flt.feed(delta)
        answer += a
        reasoning += r
    a, r = flt.flush()
    return answer + a, reasoning + r


def test_splits_inline_think_block() -> None:
    answer, reasoning = _stream(["<think>let me reason</think>The answer."])

    assert answer == "The answer."
    assert reasoning == "let me reason"


def test_handles_tags_split_across_deltas() -> None:
    answer, reasoning = _stream(["Pre ", "<thi", "nk>hidden", " plan</thi", "nk>Visible"])

    assert answer == "Pre Visible"
    assert reasoning == "hidden plan"


def test_plain_answer_with_angle_brackets_is_untouched() -> None:
    answer, reasoning = _stream(["if a < b and c > d then run"])

    assert answer == "if a < b and c > d then run"
    assert reasoning == ""


def test_supports_thinking_tag_variant() -> None:
    answer, reasoning = _stream(["<thinking>hmm</thinking>done"])

    assert answer == "done"
    assert reasoning == "hmm"


def test_unclosed_think_routes_everything_to_reasoning() -> None:
    answer, reasoning = _stream(["<think>still going when the stream ends"])

    assert answer == ""
    assert reasoning == "still going when the stream ends"


def test_truncated_open_tag_at_end_is_not_leaked() -> None:
    answer, reasoning = _stream(["the answer <thi"])

    assert answer == "the answer "
    assert reasoning == ""


def test_reasoning_filter_preserves_tool_call_markup() -> None:
    # Think first, then a tool call: reasoning is peeled off but the call markup
    # must survive intact for the downstream tool-call filter/recovery.
    answer, reasoning = _stream(["<think>plan</think><tool_call>x</tool_call>"])

    assert answer == "<tool_call>x</tool_call>"
    assert reasoning == "plan"


def test_in_think_flag_tracks_state() -> None:
    flt = ReasoningTagFilter()
    flt.feed("<think>partial")
    assert flt.in_think is True
    flt.feed("</think>done")
    assert flt.in_think is False


def test_split_reasoning_tags_whole_text() -> None:
    answer, reasoning = split_reasoning_tags("<think>abc</think>final")

    assert answer == "final"
    assert reasoning == "abc"


def test_split_reasoning_tags_is_noop_without_markup() -> None:
    answer, reasoning = split_reasoning_tags("plain answer, nothing to strip")

    assert answer == "plain answer, nothing to strip"
    assert reasoning == ""
