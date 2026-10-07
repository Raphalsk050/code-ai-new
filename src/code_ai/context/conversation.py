from __future__ import annotations

from dataclasses import dataclass, field, replace

from code_ai.providers.models import ImageContent, Message, ToolCall, ToolResult

# What the runtime puts in the user's role: none of it is something the user said.
_EDITOR_CONTEXT = "[Editor context]"
_NOT_THE_USER = (_EDITOR_CONTEXT, "[runtime]", "Compressed context summary.")


def _said_by_user(message: Message) -> bool:
    text = message.content or ""
    return message.role == "user" and bool(text.strip()) and not text.startswith(_NOT_THE_USER)


def _editor_context(message: Message) -> bool:
    return message.role == "user" and (message.content or "").startswith(_EDITOR_CONTEXT)


@dataclass(slots=True)
class ConversationState:
    messages: list[Message] = field(default_factory=list)
    previous_response_id: str | None = None
    remote_state_supported: bool = True

    def add_user(self, text: str, images: list[ImageContent] | None = None) -> None:
        self.messages.append(Message(role="user", content=text, images=list(images or [])))

    def add_assistant(
        self, text: str, tool_calls: list[ToolCall] | None = None, *, reasoning: str = ""
    ) -> None:
        calls = list(tool_calls or [])
        if not text and not calls:
            return
        # Keep the tool calls structured on the assistant message. Flattening them
        # into ``content`` makes providers replay them as plain text, which trains
        # the model to *describe* tool calls instead of invoking them.
        self.messages.append(
            Message(
                role="assistant",
                content=text,
                tool_calls=calls,
                reasoning=reasoning if calls else "",
            )
        )

    def drop_reasoning(self) -> None:
        """Forget earlier turns' thinking: templates drop it anyway, and it is
        only worth its tokens inside the turn that produced it."""
        for index, message in enumerate(self.messages):
            if message.reasoning:
                self.messages[index] = replace(message, reasoning="")

    def add_tool_result(self, result: ToolResult) -> None:
        self.messages.append(result.to_message())

    def last_said(self) -> str | None:
        """The newest thing the user said, or None when they have said nothing."""
        for message in reversed(self.messages):
            if _said_by_user(message):
                return message.content
        return None

    def drop_exchange(self, text: str, from_end: int = 0, count: int = 1) -> bool:
        """Forget something the user said, and everything it led to.

        Counted from the newest of the user's own messages, ``count`` of them
        starting ``from_end`` back, and ``text`` has to be what the first one
        says: the caller counts the messages it shows, and once older ones
        were folded into a summary the two counts stop agreeing. Then nothing
        is dropped, and False says so.
        """
        said = [index for index, message in enumerate(self.messages) if _said_by_user(message)]
        first_said = len(said) - from_end - max(1, count)
        if first_said < 0 or from_end < 0:
            return False
        if (self.messages[said[first_said]].content or "").strip() != text.strip():
            return False
        start = said[first_said]
        after = first_said + max(1, count)
        end = said[after] if after < len(said) else len(self.messages)
        # The editor context sent along with a message belongs to that message:
        # this one's goes with it, the next one's stays.
        while start > 0 and _editor_context(self.messages[start - 1]):
            start -= 1
        while end > start and end < len(self.messages) and _editor_context(self.messages[end - 1]):
            end -= 1
        del self.messages[start:end]
        # A server that held the thread by id holds the one before the cut.
        self.previous_response_id = None
        return True

    def reset_remote_state(self) -> None:
        self.previous_response_id = None

    def snapshot(self) -> list[Message]:
        return list(self.messages)
