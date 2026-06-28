"""Separate inline ``<think>`` reasoning from the visible answer.

Many local models (Qwen, DeepSeek-R1 derivatives, and others) emit their
chain-of-thought inside ``<think>...</think>`` tags in the *regular* content
stream instead of a dedicated ``reasoning_content`` field. When the serving
backend does not strip those tags, the reasoning — and the bare ``</think>``
marker — leak straight into the chat.

This module routes the text between the tags to the reasoning channel, strips
the tags, and returns the answer text. It tolerates tags split across streamed
delta boundaries, so it can run incrementally on a token stream.

Self-healing for interleaved tool calls
---------------------------------------
Qwen 3.x routinely emits a tool call *before* it closes ``</think>`` — the call
markup appears while the model is still nominally "thinking". Left alone the
call would be buried in the reasoning channel and never executed. Following the
documented chat-template fix (inject ``</think>`` right before the first
``<tool_call>``), this filter treats a tool-call marker seen inside a think
block as an implicit close: the reasoning ends and the call markup flows out on
the answer channel, where the normal tool-call recovery picks it up.

References:
- https://allanchan339.github.io/bug-fixes/2026/05/02/Qwen36-27B-updated-jinja.html
- https://github.com/vllm-project/vllm/issues/31871
"""

from __future__ import annotations

# Open/close tag pairs we recognise. ``<thinking>`` is a common variant.
_THINK_OPEN = ("<think>", "<thinking>")
_THINK_CLOSE = ("</think>", "</thinking>")
# Tool-call openers that imply the think block is over even without a closing
# tag. ``<tool_call>`` also covers the fenced ```tool_call form.
_TOOL_OPEN = ("<tool_call>", "<function=", "```tool_call")


class ReasoningTagFilter:
    """Stateful splitter for inline ``<think>`` reasoning in a token stream."""

    __slots__ = ("_in_think", "_held")

    def __init__(self) -> None:
        self._in_think = False
        self._held = ""

    @property
    def in_think(self) -> bool:
        """Whether the stream is currently inside a ``<think>`` block."""
        return self._in_think

    def feed(self, delta: str) -> tuple[str, str]:
        """Consume ``delta``; return ``(answer_text, reasoning_text)``."""
        buffer = self._held + delta
        self._held = ""
        answer: list[str] = []
        reasoning: list[str] = []
        while buffer:
            sink = reasoning if self._in_think else answer
            starts = [pos for pos in (buffer.find("<"), buffer.find("`")) if pos != -1]
            if not starts:
                sink.append(buffer)
                break
            index = min(starts)
            sink.append(buffer[:index])
            candidate = buffer[index:]
            action, marker = self._classify(candidate)
            if action == "enter":
                self._in_think = True
                buffer = candidate[len(marker):]
            elif action == "close":
                # A real </think> (or a stray one seen outside a block): drop it.
                self._in_think = False
                buffer = candidate[len(marker):]
            elif action == "heal":
                # Tool call inside a think block: end reasoning here but keep the
                # markup so it is re-scanned and emitted on the answer channel.
                self._in_think = False
                buffer = candidate
            elif action == "hold":
                self._held = candidate
                break
            else:  # "literal": a '<'/'`' that begins no marker we care about.
                sink.append(candidate[0])
                buffer = candidate[1:]
        return "".join(answer), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        """Release held text at end of stream as ``(answer, reasoning)``.

        Leftover text is, by construction, an incomplete marker prefix. Inside a
        think block it is reasoning; otherwise a truncated tag we drop so a
        partial ``<thin`` or ``</thi`` never leaks into the answer.
        """
        held = self._held
        self._held = ""
        if held and self._in_think:
            return "", held
        return "", ""

    def _classify(self, candidate: str) -> tuple[str, str]:
        """Classify text starting at '<'/'`' into an action and the marker hit."""
        lowered = candidate.lower()
        if self._in_think:
            for marker in _THINK_CLOSE:
                if lowered.startswith(marker):
                    return "close", marker
            for marker in _TOOL_OPEN:
                if lowered.startswith(marker):
                    return "heal", marker
            if _is_prefix(lowered, (*_THINK_CLOSE, *_TOOL_OPEN)):
                return "hold", ""
            return "literal", ""
        for marker in _THINK_OPEN:
            if lowered.startswith(marker):
                return "enter", marker
        # A stray close tag outside any block (e.g. left over after a healed tool
        # call) must be swallowed rather than shown.
        for marker in _THINK_CLOSE:
            if lowered.startswith(marker):
                return "close", marker
        if _is_prefix(lowered, (*_THINK_OPEN, *_THINK_CLOSE)):
            return "hold", ""
        return "literal", ""


def _is_prefix(candidate: str, markers: tuple[str, ...]) -> bool:
    """True when ``candidate`` could still grow into one of ``markers``."""
    return any(marker.startswith(candidate) for marker in markers)


def split_reasoning_tags(text: str) -> tuple[str, str]:
    """Split a complete ``text`` into ``(answer, reasoning)``.

    A cheap no-op when the text holds no ``<think>`` markup, so it is safe to run
    on every finished response regardless of provider.
    """
    if not text or "<think" not in text.lower():
        return text, ""
    flt = ReasoningTagFilter()
    answer, reasoning = flt.feed(text)
    answer_tail, reasoning_tail = flt.flush()
    return answer + answer_tail, reasoning + reasoning_tail
