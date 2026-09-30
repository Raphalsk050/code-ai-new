from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCount, TokenCounter
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
    "When the excerpt opens with an earlier compressed summary, the new summary "
    "replaces it: carry every fact in it forward, then add what came after. "
    "Keep file paths, identifiers, and error messages verbatim. Do not invent "
    "anything. Output only the summary."
)
_SUMMARY_HEADER = (
    "Compressed context summary. Requirements, decisions, file edits, and prior "
    "tool results retained:\n"
)
# Per-message cap when rendering the older excerpt for the summary request, so a
# single huge tool result cannot blow past the window before we even summarize.
# Head and tail both survive the cut: a test run or a traceback says what went
# wrong at its end. An earlier summary is never cut - it is everything already
# compressed, and cutting it lost most of it at the second compaction.
_EXCERPT_MESSAGE_CHARS = 2000
_EXCERPT_TAIL_CHARS = 800
# The share of the input budget the summary request's excerpt may fill; the
# oldest messages give way first, and the earlier summary never does.
_EXCERPT_BUDGET_SHARE = 0.6
# Output room a request always keeps. Past it the reserve is only held back
# when the prompt leaves room for it anyway: at 128k, holding the default 32k
# whatever the prompt made compaction fire with the window 58% full.
_OUTPUT_FLOOR = 8192
# Slack left between a counted prompt and the window, for what counting misses.
_WINDOW_SLACK = 0.02
# How far the server's count may pull ours, either way.
_SCALE_LIMITS = (0.5, 2.0)
# The user's standing request, restated when summarizing pushed every user turn
# out of the kept window. Capped so re-stating it can never undo the compression
# that just ran.
_CARRIED_REQUEST_HEADER = (
    "Standing user request (restated after context compression; work already in "
    "progress, do not start over):\n"
)
_CARRIED_REQUEST_CHARS = 2000


@dataclass(slots=True)
class CompressionResult:
    compressed: bool
    active_tokens: int
    estimated: bool
    previous_tokens: int = 0


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
        # Room for the thinking a reasoning model does first: at 1024 the
        # summary never arrived and the heuristic fallback ran instead.
        summary_max_tokens: int = 4096,
        # Read per summary, so a /config change reaches it.
        summary_effort: Callable[[], str | None] | None = None,
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
        self.summary_effort = summary_effort
        # The server's count over ours for the same request - its tokenizer and
        # chat template, which tiktoken only approximates - learned from the
        # prompt size it reports back (see observe).
        self._scale = 1.0
        self._calibrated = False

    @property
    def budget(self) -> int:
        """Input-token capacity once output room is set aside.

        Until the server's count is learned the whole reserve is held back, as
        it always was: a count off by a tokenizer could otherwise ask for more
        than the window holds. After, only the floor is, and each request's
        output room grows into whatever the prompt leaves (see output_room).
        """
        held = min(self.output_reserve, _OUTPUT_FLOOR) if self._calibrated else self.output_reserve
        return self.max_context_tokens - held

    def measure(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> tuple[int, TokenCount]:
        """A request's size as the server will count it, and the count here."""
        counted = self.counter.count_request(messages, tools)
        return round(counted.tokens * self._scale), counted

    def observe(self, counted: int, reported: int) -> None:
        """Learn from the prompt size the server reported for a request that
        was counted here as ``counted`` tokens."""
        if counted <= 0 or reported <= 0:
            return
        low, high = _SCALE_LIMITS
        ratio = min(high, max(low, reported / counted))
        # Smoothed after the first: one request heavy in something the two
        # tokenizers split differently should not swing the next.
        self._scale = self._scale + 0.5 * (ratio - self._scale) if self._calibrated else ratio
        self._calibrated = True

    def output_room(self, prompt_tokens: int) -> int:
        """max_tokens for a request of ``prompt_tokens``: the whole reserve
        whenever the window has room for it, never less than the floor."""
        if not self._calibrated:
            return self.output_reserve
        slack = int(self.max_context_tokens * _WINDOW_SLACK)
        room = self.max_context_tokens - prompt_tokens - slack
        return max(min(self.output_reserve, _OUTPUT_FLOOR), min(self.output_reserve, room))

    async def ensure_capacity(
        self,
        conversation: ConversationState,
        tools: list[ToolDefinition],
        *,
        force: bool = False,
    ) -> CompressionResult:
        """Summarize older context if needed (or always, when ``force=True``).

        ``force`` powers the manual ``/compact`` command: it skips the
        threshold check so the user's request always runs immediately, but
        still no-ops when there is no summarizable history (just the recent
        turns we never touch) so it never fabricates a pointless summary.
        """
        tokens, counted = self.measure(conversation.messages, tools)
        estimated = counted.estimated
        budget = self.budget
        if budget <= 0:
            raise ContextCapacityError("output_token_reserve leaves no input context capacity.")
        has_summarizable_history = len(conversation.messages) > _RECENT_TURNS_KEPT
        below_threshold = tokens <= int(budget * self.threshold)
        if not force and below_threshold:
            return CompressionResult(False, tokens, estimated, tokens)
        if force and not has_summarizable_history:
            return CompressionResult(False, tokens, estimated, tokens)

        await self.event_bus.emit(
            "context.compression.started",
            {"active_tokens": tokens, "estimated": estimated},
            source="context",
        )
        await self._compress_in_place(conversation)
        conversation.reset_remote_state()
        recounted, _ = self.measure(conversation.messages, tools)
        if recounted > int(budget * self.target) and recounted > budget:
            await self.event_bus.emit(
                "context.compression.failed",
                {"active_tokens": recounted, "max_context_tokens": self.max_context_tokens},
                source="context",
            )
            raise ContextCapacityError(
                "Mandatory request content does not fit in the context window."
            )
        await self.event_bus.emit(
            "context.compression.completed",
            {"active_tokens": recounted, "estimated": estimated},
            source="context",
        )
        return CompressionResult(True, recounted, estimated, tokens)

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

        # The summary re-enters as a *conversation* turn, never as a second
        # ``system`` message. Chat templates render system content only at the
        # top, and several — Qwen3's among them — hard-fail the request with
        # "System message must be at the beginning." when one shows up anywhere
        # else. Because compression rewrites the history in place, that 400 is
        # not a one-off: every later turn re-sends the same broken shape, so the
        # session never recovers. Carrying it as ``user`` also keeps at least one
        # user turn in the window, which the same templates require ("No user
        # query found in messages."), and means the summary now survives
        # conversation persistence (which drops system messages).
        summary = Message(role="user", content=_SUMMARY_HEADER + summary_text)
        rebuilt = system_messages[:1] + [summary]
        carried = self._carried_user_request(older, recent)
        if carried is not None:
            rebuilt.append(carried)
        conversation.messages = rebuilt + recent

    @staticmethod
    def _carried_user_request(older: list[Message], recent: list[Message]) -> Message | None:
        """Restate the latest user request when the kept window retains none.

        A tool-heavy turn can easily fill the whole recent window with assistant
        and tool messages, so summarizing the older portion drops every trace of
        the user's own words. The model is then steering on a summary alone,
        which is how an agent quietly drifts off the actual request mid-task.

        Only the most recent non-empty user turn is carried, capped, and text
        only: re-attaching an image payload here would inflate the very context
        we just compressed. Returns ``None`` — the common case — whenever the
        window already holds a user turn.
        """
        if any(message.role == "user" for message in recent):
            return None
        for message in reversed(older):
            if message.role != "user":
                continue
            text = (message.content or "").strip()
            if not text:
                continue
            return Message(
                role="user",
                content=_CARRIED_REQUEST_HEADER + text[:_CARRIED_REQUEST_CHARS],
            )
        return None

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
            reasoning_effort=self.summary_effort() if self.summary_effort else None,
        )
        response = await self.provider.complete(request)
        text = (response.text or "").strip()
        return text or None

    def _render_excerpt(self, messages: list[Message]) -> str:
        """The older messages as the summary request reads them: an earlier
        summary whole, the rest clipped head and tail - and when even that is
        more than the request has room for, the oldest of them left out."""
        carried = [_excerpt_line(message) for message in messages if _is_carried(message)]
        rest = [
            line
            for message in messages
            if not _is_carried(message) and (line := _excerpt_line(message))
        ]
        room = int(self.budget * _EXCERPT_BUDGET_SHARE)
        room -= sum(self.counter.count_text(line).tokens for line in carried)
        chosen: list[str] = []
        for line in reversed(rest):
            cost = self.counter.count_text(line).tokens
            if cost > room:
                break
            chosen.append(line)
            room -= cost
        left_out = len(rest) - len(chosen)
        gap = [f"[... {left_out} earlier messages left out to fit ...]"] if left_out else []
        return "\n".join([*carried, *gap, *reversed(chosen)])

    @staticmethod
    def _heuristic_summary(older: list[Message]) -> str:
        # An earlier summary goes on whole: it is all that is left of what it covers.
        earlier = [
            message.content.removeprefix(_SUMMARY_HEADER)
            for message in older
            if _is_carried(message)
        ]
        summary_lines = [
            f"- {message.role}: {message.content[:240].replace(chr(10), ' ')}"
            for message in older
            if message.content and not _is_carried(message)
        ]
        return "\n".join([*earlier, *summary_lines[-80:]])


def _is_carried(message: Message) -> bool:
    """An earlier summary, or the request restated with one: never clipped."""
    return message.role == "user" and (message.content or "").startswith(
        (_SUMMARY_HEADER, _CARRIED_REQUEST_HEADER)
    )


def _excerpt_line(message: Message) -> str:
    if message.role == "tool":
        head = f"[tool:{message.name or 'tool'}]"
    elif message.role == "assistant" and message.tool_calls:
        # With the arguments: they say which file was edited, what was run.
        calls = ", ".join(
            f"{call.name} {_clip(_arguments(call.arguments), 300, 100)}"
            for call in message.tool_calls
        )
        head = f"[assistant calls: {calls}]"
    else:
        head = f"[{message.role}]"
    content = (message.content or "").strip()
    if not content:
        return head if message.tool_calls else ""
    return f"{head} {content if _is_carried(message) else _clip(content)}"


def _clip(text: str, limit: int = _EXCERPT_MESSAGE_CHARS, tail: int = _EXCERPT_TAIL_CHARS) -> str:
    """``text`` cut to ``limit`` characters, keeping its end as well as its start."""
    if len(text) <= limit:
        return text
    gap = f"[... {len(text) - limit} characters left out ...]"
    return f"{text[: limit - tail]}\n{gap}\n{text[-tail:]}"


def _arguments(arguments: object) -> str:
    return json.dumps(arguments, ensure_ascii=False, default=str)
