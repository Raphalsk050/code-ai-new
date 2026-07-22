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
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

# Turns a failure-context blob into a concise, one-sentence lesson.
LessonGenerator = Callable[[str], Awaitable[str]]

_MAX_LESSON_CHARS = 400

# Reinforcement weighting for lesson ranking: each doubling of a lesson's
# recurrence count buys it one day of recency. A failure class hit 40 times
# stays ahead of a week of one-off lessons, while pure recency still breaks
# ties between equally-reinforced entries.
_COUNT_RECENCY_BONUS_S = 86400.0


def _lesson_score(entry: FailureMemory) -> float:
    return entry.last_seen + _COUNT_RECENCY_BONUS_S * math.log2(entry.count + 1)


@dataclass(slots=True)
class FailureMemory:
    """A single distilled lesson learned from a recurring failure."""

    signature: str
    trigger: str
    lesson: str
    count: int = 1
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    @property
    def id(self) -> str:
        """Stable short id (the on-disk filename stem), for display/curation."""
        return hashlib.sha256(self.signature.encode("utf-8")).hexdigest()[:16]

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
    def from_dict(cls, data: dict[str, object]) -> FailureMemory:
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
        pin_count: int = 5,
    ) -> None:
        self._dir = Path(directory)
        self._lesson_generator = lesson_generator
        self._max_entries = max_entries
        # A lesson reinforced at least this many times is chronic: it renders
        # even when newer one-off lessons fill the top slots.
        self._pin_count = pin_count

    # -- reads ---------------------------------------------------------------

    def lessons(self, *, limit: int | None = None) -> list[FailureMemory]:
        """Return stored lessons, strongest first.

        Ordering is recency weighted by reinforcement (see ``_lesson_score``),
        so a chronic failure class outranks a string of one-off lessons, and
        pruning evicts the weakest entries instead of merely the oldest.
        """

        entries: list[FailureMemory] = []
        if not self._dir.exists():
            return entries
        for path in self._dir.glob("*.json"):
            if path.name.startswith("_"):
                continue  # bookkeeping files (e.g. the maintenance marker)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt or partially written entry must not break recall.
                continue
            entry = FailureMemory.from_dict(data)
            if entry.lesson:
                entries.append(entry)
        entries.sort(key=_lesson_score, reverse=True)
        if limit is not None:
            return entries[:limit]
        return entries

    def render_for_prompt(self, *, limit: int = 8) -> str:
        """Render the strongest lessons as a prompt section, or ``""`` if none.

        The top ``limit`` by score always render; chronic lessons (count at or
        above ``pin_count``) are appended even when they fall outside the top
        slots, so a mistake the agent kept making cannot be crowded out of the
        prompt by a burst of newer one-off lessons.
        """

        entries = self.lessons()
        if not entries:
            return ""
        selected = entries[:limit]
        pinned = [e for e in entries[limit:] if e.count >= self._pin_count]
        lines = [
            f"- {entry.lesson.strip()}"
            for entry in [*selected, *pinned]
            if entry.lesson.strip()
        ]
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

    def remove(self, signature: str) -> bool:
        """Delete a lesson by signature. True when one was actually removed."""

        try:
            self._path_for(signature).unlink()
        except OSError:
            return False
        return True

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
        """Cap the store, evicting the weakest (lowest-scored) entries."""

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


# --------------------------------------------------------------------------- #
# General-purpose memory: facts the user stated and facts the agent decided are
# worth keeping. Distinct from FailureMemory (which is auto-captured from
# recurring failures); these are written explicitly via the ``remember`` tool.
# --------------------------------------------------------------------------- #

_MAX_MEMORY_CHARS = 600

# Bookkeeping file for maintenance passes (consolidation). Prefixed with "_" so
# the entry loaders skip it; see the ``startswith("_")`` guards above.
_MAINTENANCE_FILENAME = "_maintenance.json"

# Durable facts about the user / how to work — kept globally, valid everywhere.
GLOBAL_KINDS = frozenset({"user", "feedback"})
# Facts about the current project / external references — kept per-workspace.
PROJECT_KINDS = frozenset({"project", "reference"})
VALID_KINDS = GLOBAL_KINDS | PROJECT_KINDS


@dataclass(slots=True)
class Memory:
    """A single durable fact the agent chose to remember."""

    kind: str  # one of VALID_KINDS
    content: str
    source: str = "proactive"  # "user_stated" | "proactive"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def id(self) -> str:
        # Content-addressed so the same fact never duplicates.
        return hashlib.sha256(self.content.strip().encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "content": self.content,
            "source": self.source,
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Memory:
        return cls(
            kind=str(data.get("kind", "")),
            content=str(data.get("content", "")),
            source=str(data.get("source", "proactive")),
            created=float(data.get("created", time.time()) or 0.0),
            updated=float(data.get("updated", time.time()) or 0.0),
        )


class MemoryStore:
    """Reads and writes :class:`Memory` entries under a single directory.

    One JSON file per memory, named by a hash of its content, so writes are
    independent and identical facts collapse onto the same file (dedup).
    Mirrors :class:`FailureMemoryStore`'s on-disk conventions.
    """

    def __init__(self, directory: Path, *, max_entries: int = 200) -> None:
        self._dir = Path(directory)
        self._max_entries = max_entries

    # -- reads ---------------------------------------------------------------

    def all(self, *, limit: int | None = None) -> list[Memory]:
        """Return stored memories, most-recently-updated first."""

        entries: list[Memory] = []
        if not self._dir.exists():
            return entries
        for path in self._dir.glob("*.json"):
            if path.name.startswith("_"):
                continue  # bookkeeping files (e.g. the maintenance marker)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            entry = Memory.from_dict(data)
            if entry.content:
                entries.append(entry)
        entries.sort(key=lambda e: e.updated, reverse=True)
        if limit is not None:
            return entries[:limit]
        return entries

    # -- writes --------------------------------------------------------------

    def add(self, *, kind: str, content: str, source: str = "proactive") -> Memory:
        """Persist a fact, refreshing it in place if the same content already exists."""

        content = _clip_memory(content)
        entry = Memory(kind=kind, content=content, source=source)
        existing = self._load(entry.id)
        if existing is not None:
            # Same fact already known — just bump recency and keep the original.
            existing.updated = time.time()
            existing.kind = kind
            self._save(existing)
            return existing
        self._save(entry)
        self._prune()
        return entry

    def find_by_content(self, content: str) -> Memory | None:
        """Look up the stored memory whose content matches ``content`` exactly.

        Ids are content-addressed, so an exact-text match is a direct hash
        lookup — the affordance retire/replace flows use to reference a fact
        the way the model sees it (its text) instead of an internal id.
        """

        probe = Memory(kind="project", content=_clip_memory(content))
        return self._load(probe.id)

    def remove(self, memory_id: str) -> bool:
        """Delete a memory by id. True when an entry was actually removed."""

        try:
            self._path_for(memory_id).unlink()
        except OSError:
            return False
        return True

    def rewrite(
        self, memory_id: str, content: str, *, source: str | None = None
    ) -> Memory | None:
        """Replace a memory's text, keeping its kind and creation time.

        Ids are content-addressed, so a rewrite is remove-then-add under the
        hood; ``created`` is carried over so provenance survives the new id.
        Returns the new entry, or ``None`` when ``memory_id`` does not exist.
        """

        existing = self._load(memory_id)
        if existing is None:
            return None
        entry = Memory(
            kind=existing.kind,
            content=_clip_memory(content),
            source=source or existing.source,
            created=existing.created,
        )
        self.remove(memory_id)
        self._save(entry)
        return entry

    # -- maintenance bookkeeping ---------------------------------------------

    def new_entries_since_maintenance(self) -> int:
        """Net store growth since the last :meth:`mark_maintained` call.

        Drives the consolidation trigger: a store that never ran maintenance
        counts every entry as new, so the first pass fires once the store is
        big enough to be worth curating.
        """

        state = self._maintenance_state()
        baseline = int(state.get("entries_at_last_run", 0) or 0)
        return max(0, len(self.all()) - baseline)

    def mark_maintained(self) -> None:
        """Record the current store size as the new maintenance baseline."""

        self._dir.mkdir(parents=True, exist_ok=True)
        state = {"entries_at_last_run": len(self.all()), "last_run": time.time()}
        (self._dir / _MAINTENANCE_FILENAME).write_text(
            json.dumps(state), encoding="utf-8"
        )

    def _maintenance_state(self) -> dict[str, object]:
        try:
            data = json.loads(
                (self._dir / _MAINTENANCE_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- persistence ---------------------------------------------------------

    def _path_for(self, memory_id: str) -> Path:
        return self._dir / f"{memory_id}.json"

    def _load(self, memory_id: str) -> Memory | None:
        path = self._path_for(memory_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Memory.from_dict(data)

    def _save(self, entry: Memory) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path_for(entry.id).write_text(
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _prune(self) -> None:
        entries = self.all()
        if len(entries) <= self._max_entries:
            return
        for stale in entries[self._max_entries :]:
            try:
                self._path_for(stale.id).unlink()
            except OSError:
                continue


# Prompt section per memory kind, in render order. Identity comes first so
# "who the user is" leads the Memory block instead of being buried under work
# directives or project notes.
_SECTION_TITLES: tuple[tuple[str, str], ...] = (
    ("user", "Who the user is (persists across sessions):"),
    ("feedback", "How the user wants you to work (persists across sessions):"),
    ("project", "What you have learned about this project:"),
    ("reference", "External references for this project:"),
)

# Kinds rendered in full regardless of any limit: identity is small and
# critical, so it must never be dropped to save prompt space.
_ALWAYS_FULL_KINDS = frozenset({"user"})


class MemoryService:
    """Routes memories to the right scope and renders them for the prompt.

    ``user``/``feedback`` facts live in the global store (valid in any project);
    ``project``/``reference`` facts live in the per-workspace store. Rendering
    aggregates both so the model sees one coherent "Memory" section.
    """

    def __init__(self, *, global_store: MemoryStore, project_store: MemoryStore) -> None:
        self._global = global_store
        self._project = project_store

    def _store_for(self, kind: str) -> MemoryStore:
        return self._project if kind in PROJECT_KINDS else self._global

    def add(self, *, kind: str, content: str, source: str = "proactive") -> Memory:
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown memory kind: {kind!r}")
        return self._store_for(kind).add(kind=kind, content=content, source=source)

    def remove_by_content(self, content: str) -> bool:
        """Delete the memory matching ``content`` exactly, wherever it lives.

        The caller (reflection, the ``remember`` tool's ``replaces`` field)
        knows facts by their text, not by scope, so both stores are probed.
        Returns True when something was actually removed.
        """

        removed = False
        for _scope, store in self.scoped_stores():
            entry = store.find_by_content(content)
            if entry is not None:
                removed = store.remove(entry.id) or removed
        return removed

    def scoped_stores(self) -> tuple[tuple[str, MemoryStore], ...]:
        """(scope label, store) pairs, for maintenance passes and inspection."""

        return (("global", self._global), ("project", self._project))

    def render_for_prompt(self, *, limit_per_kind: int | None = None) -> str:
        """Render stored memories grouped by kind, most-recently-updated first.

        Each kind gets its own section so identity ("who the user is") never
        competes for prompt space with work directives or project notes — the
        bug where a flood of more-recent ``feedback`` memories crowded the
        user's name out of the prompt. By default nothing is truncated: the
        per-store retention cap is the only bound, so any fact kept on disk
        reliably reaches the model. ``limit_per_kind`` can cap non-identity
        sections if the prompt ever needs trimming; identity kinds in
        ``_ALWAYS_FULL_KINDS`` are always rendered in full.
        """

        entries = [*self._global.all(), *self._project.all()]
        if not entries:
            return ""
        entries.sort(key=lambda e: e.updated, reverse=True)

        lines_by_kind: dict[str, list[str]] = {}
        for entry in entries:
            content = entry.content.strip()
            if content:
                lines_by_kind.setdefault(entry.kind, []).append(f"- {content}")

        sections: list[str] = []
        for kind, title in _SECTION_TITLES:
            lines = lines_by_kind.get(kind)
            if not lines:
                continue
            if limit_per_kind is not None and kind not in _ALWAYS_FULL_KINDS:
                lines = lines[:limit_per_kind]
            sections.append(title + "\n" + "\n".join(lines))
        return "\n\n".join(sections)


def _clip_memory(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _MAX_MEMORY_CHARS:
        return text
    return text[: _MAX_MEMORY_CHARS - 1].rstrip() + "…"
