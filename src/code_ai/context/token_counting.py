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
        payload = {
            "messages": [_countable_message(message) for message in messages],
            "tools": [tool.to_dict() for tool in tools],
        }
        counted = self.count_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        image_count = sum(len(message.images) for message in messages)
        return TokenCount(
            tokens=counted.tokens + image_count * _IMAGE_TOKEN_ESTIMATE,
            estimated=counted.estimated or image_count > 0,
            source=counted.source,
        )


def _countable_message(message: Message) -> dict:
    """Wire dict with image payloads excluded from the text token count."""
    record = message.to_dict()
    if message.images:
        record["content"] = message.content
    return record
