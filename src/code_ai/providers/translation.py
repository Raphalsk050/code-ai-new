from __future__ import annotations

import json
from typing import Any

from code_ai.providers.models import Message, ToolDefinition

# Stand-in user turn for a request that would otherwise carry none. Deliberately
# content-free: it exists to satisfy a template's structural requirement, not to
# tell the model anything it cannot already read in the messages above it.
_CONTINUATION_REQUEST = "Continue from the context above."


def normalize_chat_messages(
    messages: list[Message], *, tool_results_are_user_turns: bool = False
) -> list[Message]:
    """Return ``messages`` in the shape every chat template assumes.

    Local engines (llama.cpp, LM Studio, Ollama) render the model's own Jinja
    chat template, and the mainstream ones enforce two structural rules by
    raising — which the engine reports as a 400 for the whole request, with no
    partial result and nothing to retry:

    * system content belongs at the top, once — otherwise
      ``System message must be at the beginning.``
    * at least one user turn must exist — otherwise
      ``No user query found in messages.``

    So the rules are enforced here, at the single point where a request becomes
    wire format, rather than trusted to hold everywhere upstream. Extra system
    messages are folded into the leading one (keeping their order and text) and
    a bare continuation turn is appended when no user turn survives.

    This is a last line of defence, not a design: a shape bug upstream degrades
    into a slightly reworded prompt instead of an unrecoverable session. It is a
    no-op — returning the very same list — for well-formed input.

    ``tool_results_are_user_turns`` is for adapters whose own translation
    replays tool results as user turns (Ollama's does), where a tool message
    already satisfies the second rule.
    """
    if not messages:
        return messages
    user_roles = {"user", "tool"} if tool_results_are_user_turns else {"user"}
    has_user = any(message.role in user_roles for message in messages)
    system_out_of_place = any(message.role == "system" for message in messages[1:])
    if has_user and not system_out_of_place:
        return messages

    system_parts = [
        message.content for message in messages if message.role == "system" and message.content
    ]
    normalized: list[Message] = []
    if system_parts:
        normalized.append(Message(role="system", content="\n\n".join(system_parts)))
    normalized.extend(message for message in messages if message.role != "system")
    if not has_user:
        normalized.append(Message(role="user", content=_CONTINUATION_REQUEST))
    return normalized


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
