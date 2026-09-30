from __future__ import annotations

import json
import math
from dataclasses import dataclass

from code_ai.providers.models import Message, ToolDefinition

# Flat per-image cost estimate. Vision models spend a bounded number of visual
# tokens per image (roughly 1-2k for a typical screenshot, regardless of file
# size), so counting the base64 payload as prompt text would be off by orders
# of magnitude: a 4MB screenshot is ~5MB of base64, "worth" ~600k text tokens,
# which instantly (and wrongly) overflows the context budget.
_IMAGE_TOKEN_ESTIMATE = 1500
# What a chat template wraps around one message and one tool call, measured on
# Qwen3.8's: <|im_start|>role, the empty <think></think> every assistant turn
# keeps, <tool_response> around a result, <tool_call><function=...> around a
# call. The server's own count corrects the rest (ContextCompressor.observe).
_MESSAGE_OVERHEAD = 9
_TOOL_CALL_OVERHEAD = 18


@dataclass(frozen=True, slots=True)
class TokenCount:
    tokens: int
    estimated: bool
    source: str

    def display(self) -> str:
        prefix = "~" if self.estimated else ""
        return f"{prefix}{self.tokens}"


class TokenCounter:
    """Counts active request size, marking fallback estimates explicitly."""

    def __init__(self, *, model: str) -> None:
        self.model = model
        self._encoding = None
        try:
            import tiktoken  # type: ignore

            try:
                self._encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                self._encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._encoding = None

    def count_text(self, text: str) -> TokenCount:
        if self._encoding is not None:
            return TokenCount(
                tokens=len(self._encoding.encode(text)), estimated=False, source="tiktoken"
            )
        return TokenCount(
            tokens=max(1, math.ceil(len(text) / 4 * 1.15)), estimated=True, source="fallback"
        )

    def count_request(self, messages: list[Message], tools: list[ToolDefinition]) -> TokenCount:
        """The request as the model reads it: each message's text, each call's
        arguments and each tool's schema - not the JSON the wire carries them
        in. Counting the wire form escaped every newline and quote, and every
        accented letter as \\uXXXX: Portuguese text came out 70% over, and
        compaction fired that much early."""
        parts: list[str] = []
        calls = 0
        for message in messages:
            parts.append(message.content or "")
            if message.reasoning:
                parts.append(message.reasoning)
            for call in message.tool_calls:
                calls += 1
                parts.append(call.name)
                parts.append(_compact_json(call.arguments))
        # Spaced, the way chat templates print a schema into the prompt.
        parts.extend(json.dumps(tool.to_dict(), ensure_ascii=False, default=str) for tool in tools)
        counted = self.count_text("\n".join(parts))
        image_count = sum(len(message.images) for message in messages)
        overhead = len(messages) * _MESSAGE_OVERHEAD + calls * _TOOL_CALL_OVERHEAD
        return TokenCount(
            tokens=counted.tokens + overhead + image_count * _IMAGE_TOKEN_ESTIMATE,
            estimated=counted.estimated or image_count > 0,
            source=counted.source,
        )


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
