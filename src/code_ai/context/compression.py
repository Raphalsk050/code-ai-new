from __future__ import annotations

from dataclasses import dataclass

from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.core.errors import ContextCapacityError
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import Message, ToolDefinition


@dataclass(slots=True)
class CompressionResult:
    compressed: bool
    active_tokens: int
    estimated: bool


class ContextCompressor:
    """Keeps mandatory and recent context while summarizing older local state."""

    def __init__(
        self,
        *,
        counter: TokenCounter,
        max_context_tokens: int,
        threshold: float,
        target: float,
        output_reserve: int,
        event_bus: AsyncEventBus,
    ) -> None:
        self.counter = counter
        self.max_context_tokens = max_context_tokens
        self.threshold = threshold
        self.target = target
        self.output_reserve = output_reserve
        self.event_bus = event_bus

    async def ensure_capacity(
        self,
        conversation: ConversationState,
        tools: list[ToolDefinition],
    ) -> CompressionResult:
        counted = self.counter.count_request(conversation.messages, tools)
        budget = self.max_context_tokens - self.output_reserve
        if budget <= 0:
            raise ContextCapacityError("output_token_reserve leaves no input context capacity.")
        if counted.tokens <= int(budget * self.threshold):
            return CompressionResult(False, counted.tokens, counted.estimated)

        await self.event_bus.emit(
            "context.compression.started",
            {"active_tokens": counted.tokens, "estimated": counted.estimated},
            source="context",
        )
        self._compress_in_place(conversation)
        conversation.reset_remote_state()
        recounted = self.counter.count_request(conversation.messages, tools)
        if recounted.tokens > int(budget * self.target) and recounted.tokens > budget:
            await self.event_bus.emit(
                "context.compression.failed",
                {"active_tokens": recounted.tokens, "max_context_tokens": self.max_context_tokens},
                source="context",
            )
            raise ContextCapacityError(
                "Mandatory request content does not fit in the context window."
            )
        await self.event_bus.emit(
            "context.compression.completed",
            {"active_tokens": recounted.tokens, "estimated": recounted.estimated},
            source="context",
        )
        return CompressionResult(True, recounted.tokens, recounted.estimated)

    def _compress_in_place(self, conversation: ConversationState) -> None:
        messages = conversation.messages
        if len(messages) <= 8:
            return
        system_messages = [message for message in messages if message.role == "system"]
        non_system = [message for message in messages if message.role != "system"]
        recent = non_system[-8:]
        older = non_system[:-8]
        summary_lines = [
            f"- {message.role}: {message.content[:240].replace(chr(10), ' ')}"
            for message in older
            if message.content
        ]
        summary = Message(
            role="system",
            content=(
                "Compressed context summary. Requirements, decisions, and prior tool results "
                "retained:\n"
                + "\n".join(summary_lines[-80:])
            ),
        )
        conversation.messages = system_messages[:1] + [summary] + recent
