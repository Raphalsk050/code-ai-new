from __future__ import annotations

import json
import math
from dataclasses import dataclass

from code_ai.providers.models import Message, ToolDefinition


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
            "messages": [message.to_dict() for message in messages],
            "tools": [tool.to_dict() for tool in tools],
        }
        counted = self.count_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        if counted.estimated:
            return counted
        return TokenCount(tokens=counted.tokens, estimated=False, source=counted.source)
