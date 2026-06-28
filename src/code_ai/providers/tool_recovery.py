"""Recover tool calls that weak models emit as plain text.

Small or poorly tool-tuned models frequently ignore the structured
function-calling channel and instead print the call into the assistant
*content*. We see several encodings in the wild:

* JSON inside a marker: ``<tool_call>{"name": ..., "arguments": {...}}</tool_call>``
* a fenced ```json``` snippet, or a bare top-level JSON object;
* the **Qwen / Hermes XML** shape, which has no JSON at all::

      <tool_call>
      <function=read_file>
      <parameter=path>main.py</parameter>
      </function>
      </tool_call>

* a Python-``repr`` dict using single quotes (``{'name': 'x', ...}``).

When any of these reach the chat the provider reports no structured tool calls
and the runtime would otherwise treat the markup as the final answer, leaking it
into the chat and ending the turn without running the tool.

This module extracts those embedded calls so the runtime can execute them
instead. To stay safe it only accepts a call whose ``name`` matches a tool that
was actually offered for the step, so genuine answers are never mistaken for
tool calls.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Iterator

from code_ai.providers.models import ToolCall

# Hermes/Qwen/Nous-style explicit markers, and generic fenced code blocks. The
# closing </tool_call> is optional: streamed output is sometimes cut off or the
# model simply omits it, and we still want to strip the orphan opening tag.
_TOOL_CALL_TAG = re.compile(
    r"<tool_call>\s*(.*?)\s*(?:</tool_call>|\Z)", re.DOTALL | re.IGNORECASE
)
_FENCE = re.compile(
    r"```(?:json|tool_call|tool_calls|python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE
)
# Qwen/Qwen-Coder XML function-call shape: <function=NAME> ... </function> with
# <parameter=KEY>VALUE</parameter> children. We match only the *opening* tags and
# slice values positionally (delimited by the next sibling opener, closing tag
# found with rfind). A regex like ``<parameter=k>(.*?)</parameter>`` truncates a
# value that itself contains ``</parameter>``/``</function>`` — which happens
# whenever the model edits code, XML, or templates. This mirrors Cline's parser,
# which uses lastIndexOf on the content slice for exactly this reason.
_FUNCTION_OPEN = re.compile(r"<function\s*=\s*([^>\s]+)\s*>", re.IGNORECASE)
_PARAMETER_OPEN = re.compile(r"<parameter\s*=\s*([^>\s]+)\s*>", re.IGNORECASE)
_FUNCTION_CLOSE = "</function>"
_PARAMETER_CLOSE = "</parameter>"
# Opening markers that begin an embedded tool call. Seeing any of these means
# the model is emitting a call into its content and the markup must be kept out
# of the visible chat. ``<tool_call>`` also covers fenced ```tool_call blocks.
_STREAM_MARKERS = ("<tool_call>", "<function=", "```tool_call")
# A leftover ``<tool_call>`` opener with nothing parseable still signals an
# attempt, so retry detection looks for the markers anywhere in the text.
_ATTEMPT_MARKER = re.compile(
    r"<tool_call>|<function\s*=|```tool_call", re.IGNORECASE
)

# Keys different models use for the call name and its arguments.
_NAME_KEYS = ("name", "tool", "tool_name", "function_name")
_ARGS_KEYS = ("arguments", "parameters", "args", "input", "params")


def looks_like_attempted_tool_call(text: str) -> bool:
    """True when ``text`` contains tool-call markup, parseable or not.

    Used after recovery fails to tell a malformed/truncated call attempt (worth
    a retry) apart from a genuine prose answer (which should be surfaced as-is).
    """
    return bool(text) and _ATTEMPT_MARKER.search(text) is not None


class ToolCallStreamFilter:
    """Keep embedded tool-call markup out of the visible token stream.

    Local models print tool calls into the assistant content instead of using
    the structured channel. We still need the full text to recover and execute
    the call, but it must never reach the chat. ``feed`` releases only the text
    that cannot be the beginning of a tool-call marker; the moment a marker is
    confirmed it suppresses everything that follows for the rest of the response.
    """

    __slots__ = ("_held", "_suppressed")

    def __init__(self) -> None:
        self._held = ""
        self._suppressed = False

    @property
    def suppressed(self) -> bool:
        """Whether a marker has been seen and the rest is being withheld."""
        return self._suppressed

    def feed(self, delta: str) -> str:
        """Return the portion of ``delta`` that is safe to show in the chat."""
        if self._suppressed:
            return ""
        buffer = self._held + delta
        self._held = ""
        emitted: list[str] = []
        while buffer:
            # A marker starts with '<' (XML/tag forms) or '`' (fenced form).
            starts = [pos for pos in (buffer.find("<"), buffer.find("`")) if pos != -1]
            if not starts:
                emitted.append(buffer)
                buffer = ""
                break
            index = min(starts)
            emitted.append(buffer[:index])
            candidate = buffer[index:]
            status = _marker_status(candidate)
            if status == "full":
                self._suppressed = True
                return "".join(emitted)
            if status == "prefix":
                # Could still grow into a marker; hold it until more text arrives.
                self._held = candidate
                return "".join(emitted)
            # A lone '<'/'`' that cannot begin a marker: emit it and keep scanning.
            emitted.append(candidate[0])
            buffer = candidate[1:]
        return "".join(emitted)

    def flush(self) -> str:
        """Release held text at end of stream, dropping incomplete markers.

        Any held text is, by construction, a marker prefix. If the stream ends
        mid-marker it is a truncated tool call and must not leak; the full text
        is still available to :func:`recover_tool_calls_from_text`.
        """
        self._held = ""
        return ""


def _marker_status(candidate: str) -> str:
    """Classify ``candidate`` (text starting at '<' or '`') against the markers."""
    lowered = candidate.lower()
    if any(lowered.startswith(marker) for marker in _STREAM_MARKERS):
        return "full"
    if any(marker.startswith(lowered) for marker in _STREAM_MARKERS):
        return "prefix"
    return "no"


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

    # Try the most explicit shapes first; fall back to looser ones only if those
    # find nothing, so we do not double-count the same call. Each <tool_call> or
    # fenced block is probed for both JSON and Qwen XML payloads.
    for pattern in (_TOOL_CALL_TAG, _FENCE):
        for match in pattern.finditer(text):
            calls = _coerce_calls(match.group(1), names)
            if calls:
                extracted.extend(calls)
                spans.append(match.span())
        if extracted:
            break

    # Bare Qwen/Hermes XML function blocks that were not wrapped in <tool_call>.
    if not extracted:
        for name, body, span in _iter_xml_functions(text):
            calls = _extract_xml_function(name, body, names)
            if calls:
                extracted.extend(calls)
                spans.append(span)

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
    # Qwen/Hermes XML shape carries no JSON, so probe it before parsing.
    xml_calls = _extract_xml_calls(blob, names)
    if xml_calls:
        return xml_calls
    data = _loads_relaxed(blob)
    if data is None:
        return []
    return _extract(data, names)


def _loads_relaxed(blob: str) -> object | None:
    """Parse a JSON object, tolerating Python-``repr`` dicts (single quotes)."""
    try:
        return json.loads(blob)
    except (ValueError, TypeError):
        pass
    try:
        # ast.literal_eval only evaluates literals, so this stays injection-safe.
        return ast.literal_eval(blob)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def _iter_xml_functions(blob: str) -> Iterator[tuple[str, str, tuple[int, int]]]:
    """Yield ``(name, body, (start, end))`` for each ``<function=..>`` block.

    Blocks are delimited positionally: a function body runs to the next
    ``<function=`` opener (or end of input), then a trailing ``</function>`` is
    removed with ``rfind`` so the close tag inside a parameter value does not cut
    the body short.
    """
    opens = list(_FUNCTION_OPEN.finditer(blob))
    for index, match in enumerate(opens):
        name = match.group(1).strip()
        body_start = match.end()
        next_start = opens[index + 1].start() if index + 1 < len(opens) else len(blob)
        body = blob[body_start:next_start]
        close = body.rfind(_FUNCTION_CLOSE)
        if close != -1:
            end = body_start + close + len(_FUNCTION_CLOSE)
            body = body[:close]
        else:
            end = next_start
        yield name, body, (match.start(), end)


def _extract_xml_calls(blob: str, names: set[str]) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    for name, body, _span in _iter_xml_functions(blob):
        calls.extend(_extract_xml_function(name, body, names))
    return calls


def _extract_xml_function(
    name: str, body: str, names: set[str]
) -> list[tuple[str, dict]]:
    name = name.strip()
    if name not in names:
        return []
    arguments: dict = {}
    opens = list(_PARAMETER_OPEN.finditer(body))
    for index, match in enumerate(opens):
        key = match.group(1).strip()
        if not key:
            continue
        value_start = match.end()
        next_start = opens[index + 1].start() if index + 1 < len(opens) else len(body)
        segment = body[value_start:next_start]
        # Strip the *last* </parameter> in the segment so a value containing the
        # close tag (code, XML, templates) is preserved rather than truncated.
        close = segment.rfind(_PARAMETER_CLOSE)
        if close != -1:
            segment = segment[:close]
        arguments[key] = _coerce_scalar(segment.strip())
    return [(name, arguments)]


def _coerce_scalar(raw: str) -> object:
    """Best-effort typing of an XML parameter value (number/bool/json/string)."""
    if not raw:
        return ""
    parsed = _loads_relaxed(raw)
    if parsed is not None and not isinstance(parsed, str):
        return parsed
    return raw


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
