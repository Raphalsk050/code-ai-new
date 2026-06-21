from __future__ import annotations

from dataclasses import dataclass, field

from code_ai.providers.models import Message, ToolCall, ToolResult


@dataclass(slots=True)
class ConversationState:
    messages: list[Message] = field(default_factory=list)
    previous_response_id: str | None = None
    remote_state_supported: bool = True

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str, tool_calls: list[ToolCall] | None = None) -> None:
        calls = list(tool_calls or [])
        if not text and not calls:
            return
        # Keep the tool calls structured on the assistant message. Flattening them
        # into ``content`` makes providers replay them as plain text, which trains
        # the model to *describe* tool calls instead of invoking them.
        self.messages.append(Message(role="assistant", content=text, tool_calls=calls))

    def add_tool_result(self, result: ToolResult) -> None:
        self.messages.append(result.to_message())

    def reset_remote_state(self) -> None:
        self.previous_response_id = None

    def snapshot(self) -> list[Message]:
        return list(self.messages)
