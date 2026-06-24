"""Persistent failure memory so the agent gets smarter across sessions.

Every recurring failure class (token-budget overflow, malformed tool calls,
recurring tool errors, stalls) is distilled into a one-sentence *lesson* and
stored on disk under the config dir (``~/.code-ai/memories``). Lessons are
injected back into the system prompt on later turns, so the model stops
repeating the same mistakes.

Design notes:

* Lessons are **model-generated** for context-awareness, with a deterministic
  template fallback when the meta-call is unavailable, errors, or comes back
  empty. The generator is injected as an async callable so the store stays
  decoupled from the provider stack and is trivially unit-testable.
* Entries are **deduplicated by signature** *before* any model call, so a
  failure that keeps recurring only costs one meta-call ever — subsequent hits
  just bump a counter. This bounds the cost of the feature.
* Each signature is one JSON file, named by a hash of the signature, so writes
  are independent and corruption of one entry never sinks the rest.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

# Turns a failure-context blob into a concise, one-sentence lesson.
LessonGenerator = Callable[[str], Awaitable[str]]

_MAX_LESSON_CHARS = 400


@dataclass(slots=True)
class FailureMemory:
    """A single distilled lesson learned from a recurring failure."""

    signature: str
    trigger: str
    lesson: str
    count: int = 1
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return {
            "signature": self.signature,
            "trigger": self.trigger,
            "lesson": self.lesson,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "FailureMemory":
        return cls(
            signature=str(data.get("signature", "")),
            trigger=str(data.get("trigger", "")),
            lesson=str(data.get("lesson", "")),
            count=int(data.get("count", 1) or 1),
            first_seen=float(data.get("first_seen", time.time()) or 0.0),
            last_seen=float(data.get("last_seen", time.time()) or 0.0),
        )


class FailureMemoryStore:
    """Reads and writes :class:`FailureMemory` entries under a directory."""

    def __init__(
        self,
        directory: Path,
        *,
        lesson_generator: LessonGenerator | None = None,
        max_entries: int = 200,
    ) -> None:
        self._dir = Path(directory)
        self._lesson_generator = lesson_generator
        self._max_entries = max_entries

    # -- reads ---------------------------------------------------------------

    def lessons(self, *, limit: int | None = None) -> list[FailureMemory]:
        """Return stored lessons, most-recently-seen first."""

        entries: list[FailureMemory] = []
        if not self._dir.exists():
            return entries
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt or partially written entry must not break recall.
                continue
            entry = FailureMemory.from_dict(data)
            if entry.lesson:
                entries.append(entry)
        entries.sort(key=lambda e: e.last_seen, reverse=True)
        if limit is not None:
            return entries[:limit]
        return entries

    def render_for_prompt(self, *, limit: int = 8) -> str:
        """Render recent lessons as a prompt section, or ``""`` if none."""

        entries = self.lessons(limit=limit)
        if not entries:
            return ""
        lines = [f"- {entry.lesson.strip()}" for entry in entries if entry.lesson.strip()]
        if not lines:
            return ""
        return (
            "Lessons learned from past failures (do not repeat these mistakes):\n"
            + "\n".join(lines)
        )

    # -- writes --------------------------------------------------------------

    async def record(
        self,
        *,
        trigger: str,
        context: str,
        fallback_lesson: str,
        signature: str | None = None,
    ) -> FailureMemory:
        """Record a failure, distilling a lesson on first sight of its signature.

        ``signature`` defaults to ``trigger`` so all failures of one class
        collapse into a single lesson; pass a finer key (e.g. ``"tool_error:read_file"``)
        to keep distinct cases separate.
        """

        sig = signature or trigger
        existing = self._load(sig)
        if existing is not None:
            # Already learned this lesson — just reinforce it. No model call.
            existing.count += 1
            existing.last_seen = time.time()
            self._save(existing)
            return existing

        lesson = await self._distill_lesson(context, fallback_lesson)
        entry = FailureMemory(signature=sig, trigger=trigger, lesson=lesson)
        self._save(entry)
        self._prune()
        return entry

    async def _distill_lesson(self, context: str, fallback_lesson: str) -> str:
        if self._lesson_generator is None:
            return _clip(fallback_lesson)
        try:
            generated = await self._lesson_generator(context)
        except Exception:
            # Meta-call failed (e.g. itself truncated/timed out) — never let the
            # learning path break the turn that triggered it.
            return _clip(fallback_lesson)
        generated = (generated or "").strip()
        return _clip(generated or fallback_lesson)

    # -- persistence ---------------------------------------------------------

    def _path_for(self, signature: str) -> Path:
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        return self._dir / f"{digest}.json"

    def _load(self, signature: str) -> FailureMemory | None:
        path = self._path_for(signature)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return FailureMemory.from_dict(data)

    def _save(self, entry: FailureMemory) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(entry.signature)
        path.write_text(
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _prune(self) -> None:
        """Cap the store, evicting the least-recently-seen entries."""

        entries = self.lessons()
        if len(entries) <= self._max_entries:
            return
        for stale in entries[self._max_entries :]:
            try:
                self._path_for(stale.signature).unlink()
            except OSError:
                continue


def _clip(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _MAX_LESSON_CHARS:
        return text
    return text[: _MAX_LESSON_CHARS - 1].rstrip() + "…"
