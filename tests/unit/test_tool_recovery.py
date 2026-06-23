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


def test_unknown_xml_function_is_not_executed() -> None:
    text = "<tool_call><function=rm_rf><parameter=path>/</parameter></function></tool_call>"
    calls, cleaned = recover_tool_calls_from_text(text, KNOWN)

    assert calls == []
    assert "<function=rm_rf>" in cleaned
