from __future__ import annotations

import json
from typing import Any

from code_ai.providers.models import Message, ToolDefinition


def messages_to_chat(messages: list[Message]) -> list[dict[str, Any]]:
    return [message.to_dict() for message in messages]


def tools_to_chat(
    tools: list[ToolDefinition], *, strict: bool = False
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for tool in tools:
        function: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        if strict:
            function["strict"] = True
        definitions.append({"type": "function", "function": function})
    return definitions


def tools_to_responses(
    tools: list[ToolDefinition], *, strict: bool = False
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for tool in tools:
        definition: dict[str, Any] = {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        if strict:
            definition["strict"] = True
        definitions.append(definition)
    return definitions


def parse_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if arguments in {None, ""}:
        return {}
    if isinstance(arguments, str):
        return _decode_arguments_object(arguments)
    raise ValueError("Tool arguments must be a JSON object or string.")


def _decode_arguments_object(raw: str) -> dict[str, Any]:
    """Decode a single JSON object from ``raw``, tolerating trailing extra data.

    Weak local models (e.g. qwen via ollama) sometimes emit a valid arguments
    object followed by duplicated or stray tokens, especially when the arguments
    are large. A bare ``json.loads`` then fails with ``Extra data: ...`` and,
    left unrecovered, takes down the whole session. ``raw_decode`` consumes only
    the first JSON value and ignores whatever trails it, so a recoverable call is
    not lost to a few stray characters. Genuinely broken input (e.g. a truncated
    object) still raises, and is handled as a degraded tool call by the provider.
    """
    text = raw.strip()
    if not text:
        return {}
    parsed, _end = json.JSONDecoder().raw_decode(text)
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must decode to an object.")
    return parsed


def object_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
