from __future__ import annotations

from code_ai.providers.tool_recovery import recover_tool_calls_from_text

KNOWN = {"read_file", "write_file", "list_files"}


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
