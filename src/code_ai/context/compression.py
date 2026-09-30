from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCount, TokenCounter
from code_ai.core.errors import ContextCapacityError
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import FinishReason, Message, ModelRequest, ToolDefinition

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
    "{earlier}"
    "Keep file paths, identifiers, numbers and error messages verbatim, and every "
    "requirement the user stated, in their words where they are short. It is the "
    "only record of this part of the session: use up to about {words} words, and "
    "keep a fact rather than save the words. Do not invent anything. Output only "
    "the summary."
)
# Summaries grow by appending: a compaction writes up what it folds and leaves
# the earlier summary as it was. Asking the model to carry it forward rewrote it
# every time, and each rewrite wore the oldest facts - the original request
# among them - a little further away.
_EARLIER_KEPT = (
    "The excerpt opens with the summary of what came before it. That summary is "
    "kept as it is: do not repeat it, summarize only what follows it, and use it "
    "to make sense of that.\n"
)
# Past this share of the budget the summary itself is folded again, once.
_EARLIER_FOLDED = (
    "The excerpt opens with the summary of what came before it, and the new "
    "summary replaces it: carry every fact in it forward, then add what came after.\n"
)
_SUMMARY_BUDGET_SHARE = 0.15
_SUMMARY_HEADER = (
    "Compressed context summary. Requirements, decisions, file edits, and prior "
    "tool results retained:\n"
)
_LATER_HEADER = "\n\nLater in the session:\n"
# The summary's length: a share of the excerpt it covers, within bounds. Fixed at
# 4096 tokens, it had to hold whatever a compaction folded, at any window size.
_SUMMARY_SHARE = 0.1
_SUMMARY_MIN_TOKENS = 1024
_SUMMARY_MAX_TOKENS = 8000
# Output room for the thinking a reasoning model does before the summary, which
# comes out of the same max_tokens.
_THINKING_ROOM = 8192
# Per-message caps when rendering the older excerpt for the summary request, the
# largest that fits: short sessions go whole, long ones are cut until they fit.
# Head and tail both survive a cut, since a test run or a traceback says what
# went wrong at its end. The user's own messages are the requirements and are
# cut only past their own, far larger cap; an earlier summary is never cut.
_EXCERPT_CAPS = (16_000, 8_000, 4_000, 2_000)
_EXCERPT_USER_CHARS = 24_000
_EXCERPT_TAIL_SHARE = 0.4
# The share of the input budget the summary request's excerpt may fill.
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
_CARRIED_REQUEST_CHARS = 12_000


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
        changed = await self._compress_in_place(conversation)
        if changed:
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
        return CompressionResult(changed, recounted, estimated, tokens)

    async def _compress_in_place(self, conversation: ConversationState) -> bool:
        """Fold the older messages into the summary; False when there were
        none to fold."""
        messages = conversation.messages
        if len(messages) <= _RECENT_TURNS_KEPT:
            return False
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
            return False

        earlier = "\n\n".join(
            message.content.removeprefix(_SUMMARY_HEADER).strip()
            for message in older
            if _is_summary(message)
        )
        # A request restated at the last compaction is not news: its words went
        # into that summary, and it is restated again below if still needed.
        fresh = [message for message in older if not _is_carried(message)]
        earlier_tokens = self.counter.count_text(earlier).tokens if earlier else 0
        refold = earlier_tokens > self.budget * _SUMMARY_BUDGET_SHARE
        if not fresh and not refold:
            # Only the earlier summary is old enough to fold, and it has room.
            return False
        summary_text: str | None = None
        try:
            summary_text = await self._summarize(fresh, earlier, earlier_tokens, refold=refold)
        except Exception as exc:  # pragma: no cover - provider/runtime failures.
            await self.event_bus.emit(
                "context.compression.degraded",
                {"reason": str(exc)},
                source="context",
            )
            summary_text = None
        if not summary_text:
            summary_text = self._heuristic_summary(fresh)
            # Nothing folded the earlier summary in: it goes on whole.
            refold = False
        if earlier and not refold:
            summary_text = f"{earlier}{_LATER_HEADER}{summary_text}" if summary_text else earlier

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
        return True

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
            if message.role != "user" or _is_summary(message):
                continue
            # A request restated at an earlier compaction is restated again as
            # the user said it, not wrapped in a second header.
            text = (message.content or "").removeprefix(_CARRIED_REQUEST_HEADER).strip()
            if not text:
                continue
            return Message(
                role="user",
                content=_CARRIED_REQUEST_HEADER + text[:_CARRIED_REQUEST_CHARS],
            )
        return None

    async def _summarize(
        self, fresh: list[Message], earlier: str, earlier_tokens: int, *, refold: bool
    ) -> str | None:
        if self.provider is None:
            return None
        excerpt, covered = self._render_excerpt(fresh, earlier)
        if not covered and not refold:
            return None
        # Folded again, the earlier summary keeps half its length: it is already
        # dense, and a tenth of it would be a second summary of a summary.
        wanted_size = covered * _SUMMARY_SHARE + (earlier_tokens * 0.5 if refold else 0)
        size = min(
            max(_SUMMARY_MIN_TOKENS, round(wanted_size)),
            _SUMMARY_MAX_TOKENS,
            max(_SUMMARY_MIN_TOKENS, round(self.budget * 0.1)),
        )
        effort = self.summary_effort() if self.summary_effort else None
        # Unless the request switches thinking off, a reasoning model thinks
        # first, and that comes out of the same max_tokens.
        thinking = effort != "none"
        guide = (_EARLIER_FOLDED if refold else _EARLIER_KEPT) if earlier else ""
        instruction = _SUMMARY_INSTRUCTION.format(earlier=guide, words=round(size * 0.7))
        messages = [
            Message(role="system", content=instruction),
            Message(role="user", content=excerpt),
        ]
        wanted = max(self.summary_max_tokens, size + (_THINKING_ROOM if thinking else 0))
        # Never more than the window has left once the excerpt is in.
        prompt, _ = self.measure(messages, [])
        left = self.max_context_tokens - prompt - int(self.max_context_tokens * _WINDOW_SLACK)
        request = ModelRequest(
            model=self.model,
            messages=messages,
            tools=[],
            max_output_tokens=max(_SUMMARY_MIN_TOKENS, min(wanted, left)),
            use_remote_conversation_state=False,
            reasoning_effort=effort,
        )
        response = await self.provider.complete(request)
        text = (response.text or "").strip()
        if response.finish_reason is FinishReason.LENGTH:
            if thinking and not response.reasoning:
                # Cut off with nothing told apart from the thinking: the text may
                # be all thinking, and stored as a summary it would be the record.
                return None
            if text:
                text += "\n[summary cut short]"
        return text or None

    def _render_excerpt(self, fresh: list[Message], earlier: str) -> tuple[str, int]:
        """The messages being folded as the summary request reads them, and
        how many tokens of them it holds.

        Each is cut to the largest cap that lets them all fit. When even the
        smallest is not enough, the user's own words still go in - they are
        the requirements - and the rest follows newest first while there is
        room. An earlier summary opens the excerpt whole.
        """
        room = int(self.budget * _EXCERPT_BUDGET_SHARE)
        parts: list[str] = []
        if earlier:
            parts.append(f"[earlier summary] {earlier}")
            room -= self.counter.count_text(parts[0]).tokens
        entries: list[tuple[bool, str, int]] = []
        for cap in _EXCERPT_CAPS:
            entries = [
                (message.role == "user", line, self.counter.count_text(line).tokens)
                for message in fresh
                if (line := _excerpt_line(message, cap))
            ]
            if sum(cost for _, _, cost in entries) <= room:
                break
        kept = [False] * len(entries)
        for users_first in (True, False):
            for index in reversed(range(len(entries))):
                said, _, cost = entries[index]
                if kept[index] or said is not users_first:
                    continue
                if cost > room:
                    if users_first:
                        continue
                    break
                kept[index] = True
                room -= cost
        covered = 0
        skipped = 0
        for (_, line, cost), keep in zip(entries, kept, strict=True):
            if not keep:
                skipped += 1
                continue
            if skipped:
                parts.append(f"[... {skipped} earlier messages left out to fit ...]")
                skipped = 0
            parts.append(line)
            covered += cost
        if skipped:
            parts.append(f"[... {skipped} later messages left out to fit ...]")
        return "\n".join(parts), covered

    @staticmethod
    def _heuristic_summary(fresh: list[Message]) -> str:
        summary_lines = [
            f"- {message.role}: {message.content[:240].replace(chr(10), ' ')}"
            for message in fresh
            if message.content
        ]
        return "\n".join(summary_lines[-80:])


def _is_summary(message: Message) -> bool:
    return message.role == "user" and (message.content or "").startswith(_SUMMARY_HEADER)


def _is_carried(message: Message) -> bool:
    """An earlier summary, or the request restated with one: never clipped."""
    return message.role == "user" and (message.content or "").startswith(
        (_SUMMARY_HEADER, _CARRIED_REQUEST_HEADER)
    )


def _excerpt_line(message: Message, cap: int) -> str:
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
    if _is_carried(message):
        return f"{head} {content}"
    if message.role == "user":
        cap = max(cap, _EXCERPT_USER_CHARS)
    return f"{head} {_clip(content, cap, round(cap * _EXCERPT_TAIL_SHARE))}"


def _clip(text: str, limit: int, tail: int) -> str:
    """``text`` cut to ``limit`` characters, keeping its end as well as its start."""
    if len(text) <= limit:
        return text
    gap = f"[... {len(text) - limit} characters left out ...]"
    return f"{text[: limit - tail]}\n{gap}\n{text[-tail:]}"


def _arguments(arguments: object) -> str:
    return json.dumps(arguments, ensure_ascii=False, default=str)
