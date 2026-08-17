from __future__ import annotations

import json
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
class ImageContent:
    """An image attached to a message, carried as base64 so it can travel
    through JSON persistence and every provider wire format unchanged."""

    data: str
    media_type: str = "image/png"

    def to_data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.data}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    images: list[ImageContent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.images:
            # OpenAI Chat Completions multipart shape: the text plus one
            # image_url part per attachment, as a data URL. Vision-capable
            # endpoints read the pixels; the placeholder text (e.g.
            # "[Image #1]") keeps the message meaningful everywhere else.
            parts: list[dict[str, Any]] = []
            if self.content:
                parts.append({"type": "text", "text": self.content})
            parts.extend(
                {"type": "image_url", "image_url": {"url": image.to_data_url()}}
                for image in self.images
            )
            data["content"] = parts
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        if self.tool_calls:
            # OpenAI Chat Completions shape: structured calls on the assistant
            # message, never serialized into ``content`` (which would teach the
            # model to emit tool calls as prose instead of invoking them).
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, default=str),
                    },
                }
                for call in self.tool_calls
            ]
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
    # Images accepted in a single request; 0 means the endpoint never named a
    # cap. Servers do not advertise this, so it stays 0 until one refuses a
    # request for carrying too many and states its own limit.
    max_images_per_request: int = 0

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
    kind: Literal[
        "text_delta",
        "reasoning_delta",
        "tool_call",
        "tool_call_delta",
        "completed",
        "usage",
        "warning",
    ]
    text_delta: str = ""
    reasoning_delta: str = ""
    tool_call: ToolCall | None = None
    response: ModelResponse | None = None
    usage: TokenUsage | None = None
    warning: str | None = None
    # Populated only on ``tool_call_delta`` events, which fire while a tool
    # call's arguments are still streaming in. They carry the partial call so
    # the UI can show live progress (e.g. a file being written) instead of
    # freezing until the whole call has arrived. ``tool_call_arguments`` is the
    # raw arguments text accumulated *so far* (not yet valid JSON).
    tool_call_name: str = ""
    tool_call_arguments: str = ""
    tool_call_index: int = 0
