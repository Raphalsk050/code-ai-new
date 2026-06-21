from __future__ import annotations

from dataclasses import dataclass

from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.core.errors import ContextCapacityError
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import Message, ModelRequest, ToolDefinition

# How many of the most recent turns are always kept verbatim (never summarized).
_RECENT_TURNS_KEPT = 8

# Instruction handed to the model when it summarizes the older portion of the
# conversation. The headings keep the summary structured so nothing important
# (requirements, decisions, file edits, command results) is silently dropped.
_SUMMARY_INSTRUCTION = (
    "You are compressing a long coding-agent conversation so it fits back in the "
    "context window WITHOUT losing anything important. Summarize the excerpt below "
    "densely and factually under these headings:\n"
    "- Task & requirements: the user's goals and explicit constraints.\n"
    "- Decisions & rationale: choices made and why.\n"
    "- Files & code changes: files created or edited and what changed (keep paths).\n"
    "- Commands & tool results: important commands run and their key outcomes/errors.\n"
    "- Current state: what is done, what works, what is still broken.\n"
    "- Next steps: what remains to be done.\n"
    "Keep file paths, identifiers, and error messages verbatim. Do not invent "
    "anything. Output only the summary."
)
_SUMMARY_HEADER = (
    "Compressed context summary. Requirements, decisions, file edits, and prior "
    "tool results retained:\n"
)
# Per-message cap when rendering the older excerpt for the summary request, so a
# single huge tool result cannot blow past the window before we even summarize.
_EXCERPT_MESSAGE_CHARS = 2000


@dataclass(slots=True)
class CompressionResult:
    compressed: bool
    active_tokens: int
    estimated: bool


class ContextCompressor:
    """Keeps mandatory and recent context while summarizing older local state.

    The older portion is summarized by the model itself (a single non-streaming
    call) so the retained context stays faithful to what was actually done. If
    that call fails for any reason, a deterministic heuristic summary is used as
    a fallback so a turn is never blocked by compression.
    """

    def __init__(
        self,
        *,
        counter: TokenCounter,
        max_context_tokens: int,
        threshold: float,
        target: float,
        output_reserve: int,
        event_bus: AsyncEventBus,
        provider: ModelProvider | None = None,
        model: str = "",
        summary_max_tokens: int = 1024,
    ) -> None:
        self.counter = counter
        self.max_context_tokens = max_context_tokens
        self.threshold = threshold
        self.target = target
        self.output_reserve = output_reserve
        self.event_bus = event_bus
        self.provider = provider
        self.model = model
        self.summary_max_tokens = summary_max_tokens

    @property
    def budget(self) -> int:
        """Input-token capacity once the output reserve is set aside."""
        return self.max_context_tokens - self.output_reserve

    async def ensure_capacity(
        self,
        conversation: ConversationState,
        tools: list[ToolDefinition],
    ) -> CompressionResult:
        counted = self.counter.count_request(conversation.messages, tools)
        budget = self.budget
        if budget <= 0:
            raise ContextCapacityError("output_token_reserve leaves no input context capacity.")
        if counted.tokens <= int(budget * self.threshold):
            return CompressionResult(False, counted.tokens, counted.estimated)

        await self.event_bus.emit(
            "context.compression.started",
            {"active_tokens": counted.tokens, "estimated": counted.estimated},
            source="context",
        )
        await self._compress_in_place(conversation)
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

    async def _compress_in_place(self, conversation: ConversationState) -> None:
        messages = conversation.messages
        if len(messages) <= _RECENT_TURNS_KEPT:
            return
        system_messages = [message for message in messages if message.role == "system"]
        non_system = [message for message in messages if message.role != "system"]

        # Keep the last N turns verbatim, but never let the recent window start on
        # an orphan tool result (a tool message whose assistant tool-call got
        # summarized away) — some providers reject that. Push the boundary forward
        # so those leading tool messages fold into the summarized older portion.
        split = max(0, len(non_system) - _RECENT_TURNS_KEPT)
        while split < len(non_system) and non_system[split].role == "tool":
            split += 1
        older = non_system[:split]
        recent = non_system[split:]
        if not older:
            return

        summary_text: str | None = None
        try:
            summary_text = await self._summarize(older)
        except Exception as exc:  # pragma: no cover - provider/runtime failures.
            await self.event_bus.emit(
                "context.compression.degraded",
                {"reason": str(exc)},
                source="context",
            )
            summary_text = None
        if not summary_text:
            summary_text = self._heuristic_summary(older)

        summary = Message(role="system", content=_SUMMARY_HEADER + summary_text)
        conversation.messages = system_messages[:1] + [summary] + recent

    async def _summarize(self, older: list[Message]) -> str | None:
        if self.provider is None:
            return None
        excerpt = self._render_excerpt(older)
        if not excerpt.strip():
            return None
        request = ModelRequest(
            model=self.model,
            messages=[
                Message(role="system", content=_SUMMARY_INSTRUCTION),
                Message(role="user", content=excerpt),
            ],
            tools=[],
            max_output_tokens=self.summary_max_tokens,
            use_remote_conversation_state=False,
        )
        response = await self.provider.complete(request)
        text = (response.text or "").strip()
        return text or None

    @staticmethod
    def _render_excerpt(messages: list[Message]) -> str:
        lines: list[str] = []
        for message in messages:
            if message.role == "tool":
                head = f"[tool:{message.name or 'tool'}]"
            elif message.role == "assistant" and message.tool_calls:
                names = ", ".join(call.name for call in message.tool_calls)
                head = f"[assistant calls: {names}]"
            else:
                head = f"[{message.role}]"
            content = (message.content or "").strip()
            if content:
                lines.append(f"{head} {content[:_EXCERPT_MESSAGE_CHARS]}")
            elif message.tool_calls:
                lines.append(head)
        return "\n".join(lines)

    @staticmethod
    def _heuristic_summary(older: list[Message]) -> str:
        summary_lines = [
            f"- {message.role}: {message.content[:240].replace(chr(10), ' ')}"
            for message in older
            if message.content
        ]
        return "\n".join(summary_lines[-80:])
