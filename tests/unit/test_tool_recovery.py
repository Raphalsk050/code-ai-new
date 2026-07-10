from __future__ import annotations

from code_ai.providers.tool_recovery import (
    ToolCallStreamFilter,
    looks_like_attempted_tool_call,
    recover_tool_calls_from_text,
)

KNOWN = {"read_file", "write_file", "list_files"}


def _stream(deltas: list[str]) -> tuple[str, bool]:
    """Feed ``deltas`` through the filter; return (visible_text, suppressed)."""
    flt = ToolCallStreamFilter()
    visible = "".join(flt.feed(delta) for delta in deltas)
    visible += flt.flush()
    return visible, flt.suppressed


def test_recovers_hermes_style_tool_call_tag() -> None:
    text = (
        'Let me check.\n'
        '<tool_call>{"name": "read_file", "arguments": {"path": "a.txt"}}</tool_call>'
    )
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "a.txt"}
    assert "<tool_call>" not in cleaned
    assert cleaned == "Let me check."


def test_recovers_fenced_json_with_parameters_key() -> None:
    text = '```json\n{"name": "write_file", "parameters": {"path": "a.py", "content": "x"}}\n```'
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert calls[0].arguments == {"path": "a.py", "content": "x"}
    assert cleaned == ""


def test_recovers_bare_json_object() -> None:
    calls, _ = recover_tool_calls_from_text('{"name": "list_files", "arguments": {}}', KNOWN)

    assert len(calls) == 1
    assert calls[0].name == "list_files"
    assert calls[0].arguments == {}


def test_recovers_arguments_supplied_as_json_string() -> None:
    text = '<tool_call>{"name": "read_file", "arguments": "{\\"path\\": \\"b.txt\\"}"}</tool_call>'
    calls, _ = recover_tool_calls_from_text(text, KNOWN)

    assert calls[0].arguments == {"path": "b.txt"}


def test_recovers_multiple_tool_calls() -> None:
    text = (
        '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>'
        '<tool_call>{"name": "read_file", "arguments": {"path": "b"}}</tool_call>'
    )
    calls, _ = recover_tool_calls_from_text(text, KNOWN)

    assert [call.arguments["path"] for call in calls] == ["a", "b"]


def test_ignores_unknown_tool_name() -> None:
    calls, cleaned = recover_tool_calls_from_text(
        '{"name": "rm_rf", "arguments": {"path": "/"}}', KNOWN
    )

    assert calls == []
    assert cleaned == '{"name": "rm_rf", "arguments": {"path": "/"}}'


def test_ignores_plain_json_answer_that_is_not_a_call() -> None:
    # A legitimate JSON answer must not be mistaken for a tool call.
    text = '{"answer": 42, "unit": "meaning"}'
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert calls == []
    assert cleaned == text


def test_no_recovery_without_known_names() -> None:
    calls, cleaned = recover_tool_calls_from_text(
        '<tool_call>{"name": "read_file", "arguments": {}}</tool_call>', set()
    )

    assert calls == []
    assert "<tool_call>" in cleaned


def test_plain_prose_is_left_untouched() -> None:
    text = "Here is a summary of the file with no tool call at all."
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert calls == []
    assert cleaned == text


def test_recovers_qwen_xml_function_call() -> None:
    # Qwen3 / Qwen-Coder emit tool calls as XML with no JSON payload.
    text = (
        "Let me read it.\n"
        "<tool_call>\n"
        "<function=read_file>\n"
        "<parameter=path>main.py</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "main.py"}
    assert cleaned == "Let me read it."


def test_qwen_xml_parameters_are_typed() -> None:
    # Numeric / boolean / JSON parameter values should not stay strings.
    text = (
        "<tool_call><function=read_file>"
        "<parameter=path>a.py</parameter>"
        "<parameter=start>3</parameter>"
        "<parameter=recurse>true</parameter>"
        "</function></tool_call>"
    )
    calls, _ = recover_tool_calls_from_text(text, KNOWN)

    assert calls[0].arguments == {"path": "a.py", "start": 3, "recurse": True}


def test_recovers_bare_xml_function_without_tool_call_wrapper() -> None:
    text = "<function=list_files>\n<parameter=path>.</parameter>\n</function>"
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert calls[0].name == "list_files"
    assert calls[0].arguments == {"path": "."}
    assert cleaned == ""


def test_recovers_xml_when_closing_tags_are_missing() -> None:
    # Truncated stream: opening tags only, no </parameter>/</function>/</tool_call>.
    text = "<tool_call>\n<function=read_file>\n<parameter=path>main.py</parameter>"
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert calls[0].arguments == {"path": "main.py"}
    assert "<tool_call>" not in cleaned


def test_recovers_python_repr_dict_with_single_quotes() -> None:
    text = "<tool_call>{'name': 'read_file', 'arguments': {'path': 'main.py'}}</tool_call>"
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "main.py"}
    assert "<tool_call>" not in cleaned


def test_strips_orphan_open_tag_after_recovery() -> None:
    # A dangling <tool_call> opener must not survive in the cleaned prose.
    text = '<tool_call>\n{"name": "read_file", "arguments": {"path": "main.py"}}'
    _, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert "<tool_call>" not in cleaned


def test_xml_parameter_value_containing_closing_tags_is_preserved() -> None:
    # Editing code/XML/templates puts literal </parameter> and </function> inside
    # a value. Positional parsing (Cline-style lastIndexOf) must not truncate it.
    text = (
        "<tool_call><function=edit_code>"
        "<parameter=path>a.py</parameter>"
        "<parameter=new_text>x = 1\n"
        "# literal </parameter> and </function> in the value\n"
        "y = 2</parameter>"
        "</function></tool_call>"
    )
    calls, _ = recover_tool_calls_from_text(text, {"edit_code"})

    assert len(calls) == 1
    new_text = calls[0].arguments["new_text"]
    assert "</parameter> and </function> in the value" in new_text
    assert new_text.endswith("y = 2")
    assert calls[0].arguments["path"] == "a.py"


def test_xml_multiple_params_with_angle_brackets_in_values() -> None:
    text = (
        "<tool_call><function=edit_code>"
        "<parameter=old_text>if a < b and c > d:</parameter>"
        "<parameter=new_text>if a <= b and c >= d:</parameter>"
        "</function></tool_call>"
    )
    calls, _ = recover_tool_calls_from_text(text, {"edit_code"})

    assert calls[0].arguments == {
        "old_text": "if a < b and c > d:",
        "new_text": "if a <= b and c >= d:",
    }


def test_unknown_xml_function_is_not_executed() -> None:
    text = "<tool_call><function=rm_rf><parameter=path>/</parameter></function></tool_call>"
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert calls == []
    assert "<function=rm_rf>" in cleaned


# --------------------------------------------------------------------------- #
# Streaming suppression
# --------------------------------------------------------------------------- #


def test_stream_filter_suppresses_pure_tool_call() -> None:
    text = "<tool_call>\n<function=edit_code>\n<parameter=path>a.py</parameter>\n</function>"
    visible, suppressed = _stream(list(text))

    assert visible == ""
    assert suppressed is True


def test_stream_filter_emits_prose_before_a_call() -> None:
    visible, suppressed = _stream(
        ["I will ", "edit it.\n<tool", "_call>\n<func", "tion=edit_code>x</tool_call>"]
    )

    assert visible == "I will edit it.\n"
    assert suppressed is True


def test_stream_filter_leaves_plain_prose_with_angle_brackets() -> None:
    # A genuine answer containing < and > must stream through untouched.
    visible, suppressed = _stream(["if a ", "< b and c ", "> d then stop"])

    assert visible == "if a < b and c > d then stop"
    assert suppressed is False


def test_stream_filter_recovers_from_false_marker_start() -> None:
    # "<to" looks like the start of <tool_call> but resolves to ordinary prose.
    visible, suppressed = _stream(["see <to", "ggle> the flag"])

    assert visible == "see <toggle> the flag"
    assert suppressed is False


def test_stream_filter_drops_truncated_marker_at_end() -> None:
    # Stream cut off mid-marker must not leak the partial "<tool_ca".
    visible, suppressed = _stream(["here goes <tool_ca"])

    assert visible == "here goes "
    assert suppressed is False


def test_stream_filter_suppresses_fenced_tool_call() -> None:
    visible, suppressed = _stream(["```tool_call\n{\"name\": \"read_file\"}\n```"])

    assert visible == ""
    assert suppressed is True


# --------------------------------------------------------------------------- #
# Attempt detection (drives retries)
# --------------------------------------------------------------------------- #


def test_attempt_detector_flags_markup() -> None:
    assert looks_like_attempted_tool_call("blah <tool_call> ...")
    assert looks_like_attempted_tool_call("<function=read_file>")
    assert looks_like_attempted_tool_call("```tool_call\n{}\n```")


def test_attempt_detector_ignores_plain_prose() -> None:
    assert not looks_like_attempted_tool_call("Here is the answer to your question.")
    assert not looks_like_attempted_tool_call("")


def test_stream_filter_releases_short_trailing_hold_at_flush() -> None:
    # Observed with a real model: the answer ended with an inline-code span
    # ("Elemento `<autor>`") right before a structured tool call. The closing
    # backtick was held as a potential ```tool_call prefix and flush() dropped
    # it, visibly truncating the committed answer.
    flt = ToolCallStreamFilter()
    visible = flt.feed("Elemento `<autor>") + flt.feed("`")
    visible += flt.flush()
    assert visible == "Elemento `<autor>`"


def test_stream_filter_still_drops_truncated_marker_at_flush() -> None:
    flt = ToolCallStreamFilter()
    visible = flt.feed("Answer text <tool_ca")
    visible += flt.flush()
    assert visible == "Answer text "
