from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# A memory sits in the system prompt, at position zero, and stays there while a
# turn grows to hundreds of messages of tool output. Being present is not the
# same as being noticed: by the time the agent is deep in the work the fact it
# needs is far behind everything it has read since. This module handles the other
# half - putting the relevant memory back in front of the agent at the moment its
# subject actually comes up.
#
# Relevance is decided by shared distinctive terms, not by a model: recall must be
# cheap enough to run every round and predictable enough to reason about.

# Words too common to indicate that two texts are about the same thing. English
# and Portuguese both appear because memories are written in whichever language
# the user works in.
_STOPWORDS = frozenset(
    """
    about after again against always because been before being below between
    both could does doing done during each from have having here into itself
    just make makes making more most much only other over same should some
    such than that their them then there these they thing things this those
    through under until very want what when where which while with would your
    ainda antes aqui como onde quando quanto porque pelo pela pelos pelas para
    mais menos muito nao não outro outra pode podem sempre sobre depois entre
    esse essa este esta isso aquele aquela seja sendo tem temos ter todo toda
    todos todas você voces vocês
    """.split()
)

# Below this a token is too generic to carry a topic ("file", "add", "run").
_MIN_TERM_CHARS = 4
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def extract_terms(text: str) -> frozenset[str]:
    """Distinctive lowercase terms in ``text``.

    Identifiers and path segments survive intact (``build_system_prompt``,
    ``orchestration``) because those are exactly what makes a memory and a piece
    of work recognisably about the same thing; ordinary prose mostly does not.
    """

    terms = {
        token.lower()
        for token in _TOKEN_RE.findall(text or "")
        if len(token) >= _MIN_TERM_CHARS
    }
    # Split snake/camel identifiers too, so a memory about "build_system_prompt"
    # is still found by work that only mentions ``system_prompt``.
    for token in list(terms):
        for part in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", token):
            if len(part) >= _MIN_TERM_CHARS:
                terms.add(part.lower())
    return frozenset(terms - _STOPWORDS)


@dataclass(frozen=True, slots=True)
class RecallableMemory:
    content: str
    kind: str
    terms: frozenset[str]

    @classmethod
    def build(cls, content: str, kind: str) -> RecallableMemory:
        return cls(content=content.strip(), kind=kind, terms=extract_terms(content))


class MemoryRecall:
    """Brings a stored memory back when the work turns to what it is about.

    Deliberately conservative. A memory resurfaces only on several shared
    distinctive terms, only once per turn, and only a couple of times overall -
    a recall that fires on a weak match is worse than none, because it teaches
    the agent that these notes are noise.
    """

    def __init__(
        self,
        memories: Sequence[RecallableMemory],
        *,
        min_overlap: int = 2,
        max_per_turn: int = 2,
    ) -> None:
        self._memories = [memory for memory in memories if memory.terms]
        self._min_overlap = min_overlap
        self._max_per_turn = max_per_turn
        self._surfaced: set[str] = set()

    @classmethod
    def from_contents(
        cls, entries: Iterable[tuple[str, str]], **kwargs: object
    ) -> MemoryRecall:
        """Build from ``(content, kind)`` pairs."""

        return cls(
            [RecallableMemory.build(content, kind) for content, kind in entries],
            **kwargs,  # type: ignore[arg-type]
        )

    def consider(self, focus: str) -> str | None:
        """The memory worth repeating for this piece of work, if any."""

        if len(self._surfaced) >= self._max_per_turn:
            return None
        focus_terms = extract_terms(focus)
        if not focus_terms:
            return None
        best: RecallableMemory | None = None
        best_overlap = self._min_overlap - 1
        for memory in self._memories:
            if memory.content in self._surfaced:
                continue
            overlap = len(memory.terms & focus_terms)
            if overlap > best_overlap:
                best, best_overlap = memory, overlap
        if best is None:
            return None
        self._surfaced.add(best.content)
        return (
            "Something you recorded earlier applies to what you are doing now:\n"
            f"- {best.content}\n"
            "It was true when it was written; if it names a file, command, or "
            "flag, check that it still holds before you rely on it."
        )
