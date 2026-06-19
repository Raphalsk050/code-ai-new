from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CANCELLED = "cancelled"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    exact: bool = False
    source: str = "estimate"

    @classmethod
    def from_counts(
        cls,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        exact: bool = False,
        source: str = "estimate",
    ) -> TokenUsage:
        return cls(
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            total_tokens=max(0, int(input_tokens) + int(output_tokens)),
            exact=exact,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False

    def to_message(self) -> Message:
        prefix = "ERROR: " if self.is_error else ""
        return Message(
            role="tool",
            content=prefix + self.content,
            tool_call_id=self.tool_call_id,
            name=self.name,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProviderCapabilities:
    streaming: bool = True
    tool_calling: bool = True
    provider_reported_usage: bool = False
    remote_conversation_state: bool = False
    native_tokenization: bool = False
    image_support: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelRequest:
    model: str
    messages: list[Message]
    tools: list[ToolDefinition] = field(default_factory=list)
    max_output_tokens: int | None = None
    previous_response_id: str | None = None
    use_remote_conversation_state: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage | None = None
    finish_reason: FinishReason = FinishReason.UNKNOWN
    response_id: str | None = None
    raw_provider_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "reasoning": self.reasoning,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "usage": self.usage.to_dict() if self.usage else None,
            "finish_reason": self.finish_reason.value,
            "response_id": self.response_id,
            "raw_provider_name": self.raw_provider_name,
        }


@dataclass(slots=True)
class ProviderEvent:
    kind: Literal["text_delta", "reasoning_delta", "tool_call", "completed", "usage", "warning"]
    text_delta: str = ""
    reasoning_delta: str = ""
    tool_call: ToolCall | None = None
    response: ModelResponse | None = None
    usage: TokenUsage | None = None
    warning: str | None = None
