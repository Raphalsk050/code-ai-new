"""Separate inline ``<think>`` reasoning from the visible answer.

Many local models (Qwen, DeepSeek-R1 derivatives, and others) emit their
chain-of-thought inside ``<think>...</think>`` tags in the *regular* content
stream instead of a dedicated ``reasoning_content`` field. When the serving
backend does not strip those tags, the reasoning — and the bare ``</think>``
marker — leak straight into the chat.

This module routes the text between the tags to the reasoning channel, strips
the tags, and returns the answer text. It tolerates tags split across streamed
delta boundaries, so it can run incrementally on a token stream.
"""

from __future__ import annotations

# Open/close tag pairs we recognise. ``<thinking>`` is a common variant.
_THINK_OPEN = ("<think>", "<thinking>")
_THINK_CLOSE = ("</think>", "</thinking>")


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
            markers = _THINK_CLOSE if self._in_think else _THINK_OPEN
            sink = reasoning if self._in_think else answer
            index = buffer.find("<")
            if index == -1:
                sink.append(buffer)
                break
            sink.append(buffer[:index])
            candidate = buffer[index:]
            status, marker = _tag_status(candidate, markers)
            if status == "full":
                self._in_think = not self._in_think
                buffer = candidate[len(marker):]
                continue
            if status == "prefix":
                # Could still complete into a tag once more text arrives.
                self._held = candidate
                break
            # A '<' that cannot begin the tag we are looking for: keep it and
            # carry on. This preserves stray angle brackets and unrelated markup
            # (e.g. a tool call) so downstream filters still see it intact.
            sink.append("<")
            buffer = candidate[1:]
        return "".join(answer), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        """Release held text at end of stream as ``(answer, reasoning)``.

        Leftover text is, by construction, an incomplete tag prefix. Inside a
        think block it is reasoning; otherwise a truncated open tag we drop so a
        partial ``<thin`` never leaks into the answer.
        """
        held = self._held
        self._held = ""
        if held and self._in_think:
            return "", held
        return "", ""


def _tag_status(candidate: str, markers: tuple[str, ...]) -> tuple[str, str]:
    """Classify text starting at '<' against ``markers``."""
    lowered = candidate.lower()
    for marker in markers:
        if lowered.startswith(marker):
            return "full", marker
    for marker in markers:
        if marker.startswith(lowered):
            return "prefix", marker
    return "no", ""


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
