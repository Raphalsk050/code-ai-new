"""Recover tool calls that weak models emit as plain text.

Small or poorly tool-tuned models frequently ignore the structured
function-calling channel and instead print the call into the assistant
*content*: as a ``<tool_call>{...}</tool_call>`` block, a fenced JSON snippet,
or a bare JSON object. When that happens the provider reports no structured
tool calls and the runtime would otherwise treat the call as the final answer
and show it in the chat.

This module extracts those embedded calls so the runtime can execute them
instead. To stay safe it only accepts a call whose ``name`` matches a tool that
was actually offered for the step, so genuine JSON answers are never mistaken
for tool calls.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator

from code_ai.providers.models import ToolCall

# Hermes/Qwen/Nous-style explicit markers, and generic fenced code blocks.
_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(
    r"```(?:json|tool_call|tool_calls|python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE
)
# Keys different models use for the call name and its arguments.
_NAME_KEYS = ("name", "tool", "tool_name", "function_name")
_ARGS_KEYS = ("arguments", "parameters", "args", "input", "params")


def recover_tool_calls_from_text(
    text: str, known_names: Iterable[str]
) -> tuple[list[ToolCall], str]:
    """Return tool calls embedded in ``text`` plus ``text`` with them removed.

    Only calls naming a tool in ``known_names`` are recovered. When nothing is
    recovered the original text is returned unchanged.
    """

    names = {name for name in known_names if name}
    if not text or not names:
        return [], text

    extracted: list[tuple[str, dict]] = []
    spans: list[tuple[int, int]] = []

    # Try the most explicit shapes first; fall back to bare JSON only if those
    # find nothing, so we do not double-count the same call.
    for pattern in (_TOOL_CALL_TAG, _FENCE):
        for match in pattern.finditer(text):
            calls = _coerce_calls(match.group(1), names)
            if calls:
                extracted.extend(calls)
                spans.append(match.span())
        if extracted:
            break

    if not extracted:
        for span, blob in _iter_json_blobs(text):
            calls = _coerce_calls(blob, names)
            if calls:
                extracted.extend(calls)
                spans.append(span)

    if not extracted:
        return [], text

    recovered = [
        ToolCall(id=f"recovered_{index}", name=name, arguments=arguments)
        for index, (name, arguments) in enumerate(extracted)
    ]
    return recovered, _strip_spans(text, spans).strip()


def _coerce_calls(blob: str, names: set[str]) -> list[tuple[str, dict]]:
    blob = blob.strip()
    if not blob:
        return []
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return []
    return _extract(data, names)


def _extract(data: object, names: set[str]) -> list[tuple[str, dict]]:
    if isinstance(data, list):
        calls: list[tuple[str, dict]] = []
        for item in data:
            calls.extend(_extract(item, names))
        return calls
    if not isinstance(data, dict):
        return []
    nested = data.get("tool_calls")
    if isinstance(nested, list):
        return _extract(nested, names)
    function = data.get("function")
    if isinstance(function, dict):
        data = function
    name = next((data[key] for key in _NAME_KEYS if isinstance(data.get(key), str)), None)
    if name not in names:
        return []
    raw_args = next((data[key] for key in _ARGS_KEYS if key in data), None)
    arguments = _coerce_arguments(raw_args)
    if arguments is None:
        return []
    return [(name, arguments)]


def _coerce_arguments(raw: object) -> dict | None:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _iter_json_blobs(text: str) -> Iterator[tuple[tuple[int, int], str]]:
    """Yield ``((start, end), substring)`` for each top-level {..}/[..] group."""
    closers = {"{": "}", "[": "]"}
    index = 0
    length = len(text)
    while index < length:
        opener = text[index]
        if opener in closers:
            end = _match_group(text, index, opener, closers[opener])
            if end is not None:
                yield (index, end), text[index:end]
                index = end
                continue
        index += 1


def _match_group(text: str, start: int, opener: str, closer: str) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return position + 1
    return None


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in sorted(set(spans)):
        if start < cursor:
            continue
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
