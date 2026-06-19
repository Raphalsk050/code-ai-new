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
        if text or not tool_calls:
            self.messages.append(Message(role="assistant", content=text))
        if tool_calls:
            call_lines = [f"{call.id}:{call.name}({call.arguments})" for call in tool_calls]
            self.messages.append(
                Message(role="assistant", content="Tool calls requested:\n" + "\n".join(call_lines))
            )

    def add_tool_result(self, result: ToolResult) -> None:
        self.messages.append(result.to_message())

    def reset_remote_state(self) -> None:
        self.previous_response_id = None

    def snapshot(self) -> list[Message]:
        return list(self.messages)
