"""Post-turn reflection: the agent distills its own durable memories.

The ``remember`` tool only captures a fact when the model spontaneously decides
to call it mid-task — which models focused on the work rarely do. This module
closes that gap with one bounded meta-call after a substantive turn: it reviews
a compact digest of what just happened (the user's message, the actions taken,
the outcome, planner evidence) next to the memories already stored, and returns
the few durable facts worth saving — plus stored memories the turn proved
wrong, which are retired. User corrections are the highest-value signal: when
the user redirects the agent, that preference is exactly what future sessions
must not re-learn the hard way.

Design notes, mirroring the failure-lesson generator in ``core.memory``:

* The meta-call is injected as an async callable, so the service stays
  decoupled from the provider stack and is trivially unit-testable.
* Everything is best-effort and bounded: a failed or garbled meta-call means
  "nothing learned this turn", never a broken turn or session.
* Reflection is designed to run in the background after the reply already
  reached the user, so it adds no latency to the turn itself.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from code_ai.config.models import MemoryConfig
from code_ai.core.memory import VALID_KINDS, MemoryService, MemoryStore
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import build_consolidation_prompt, build_reflection_prompt
from code_ai.tools.output import bound_text

logger = logging.getLogger(__name__)

# Runs the reflection meta-call: full prompt in, raw model text out.
ReflectionGenerator = Callable[[str], Awaitable[str]]

# Hard caps on what one reflection pass may change, so a hallucinating
# meta-call can never flood or gut the store.
_MAX_SAVES_PER_TURN = 3
_MAX_RETIRES_PER_TURN = 3

# Hard cap on what one consolidation pass may change, for the same reason.
_MAX_CONSOLIDATION_OPS = 10

# Bounds for the digest sections, keeping the meta-call prompt small.
_MAX_DIGEST_TEXT_CHARS = 1200
_MAX_DIGEST_ACTIONS = 30
_MAX_EVIDENCE_CHARS = 1500


@dataclass(slots=True)
class TurnDigest:
    """Compact record of one finished turn, the reflection pass's input."""

    user_text: str
    final_text: str
    actions: tuple[str, ...] = ()
    evidence: str = ""
    outcome: str = "success"

    def render(self) -> str:
        lines = [
            "User message:",
            bound_text(self.user_text.strip() or "(empty)", _MAX_DIGEST_TEXT_CHARS),
            "",
            "Actions taken (tool calls, in order):",
        ]
        if self.actions:
            shown = self.actions[:_MAX_DIGEST_ACTIONS]
            lines.extend(f"- {action}" for action in shown)
            if len(self.actions) > len(shown):
                lines.append(f"- … and {len(self.actions) - len(shown)} more")
        else:
            lines.append("- (none)")
        if self.evidence.strip():
            lines += ["", "Evidence ledger (what verifiably happened):"]
            lines.append(bound_text(self.evidence.strip(), _MAX_EVIDENCE_CHARS))
        lines += [
            "",
            f"Turn outcome: {self.outcome}",
            "Final reply to the user:",
            bound_text(self.final_text.strip() or "(empty)", _MAX_DIGEST_TEXT_CHARS),
        ]
        return "\n".join(lines)


@dataclass(slots=True)
class ReflectionReport:
    """What one reflection pass actually changed."""

    saved: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.saved or self.retired)


@dataclass(slots=True)
class _ReflectionOps:
    saves: list[tuple[str, str]] = field(default_factory=list)  # (kind, content)
    retires: list[str] = field(default_factory=list)


class ReflectionService:
    """Distills durable memories from a finished turn via one bounded meta-call."""

    def __init__(
        self,
        *,
        memory: MemoryService,
        generator: ReflectionGenerator,
        config: MemoryConfig,
        event_bus: AsyncEventBus | None = None,
    ) -> None:
        self._memory = memory
        self._generator = generator
        self._config = config
        self._event_bus = event_bus

    def should_reflect(self, *, tool_calls_executed: int) -> bool:
        """Whether a turn was substantive enough to be worth one meta-call.

        Trivial conversational turns teach nothing durable; requiring a minimum
        number of executed tool calls keeps the learning cost proportional to
        the work actually done.
        """

        if not self._config.reflection_enabled:
            return False
        return tool_calls_executed >= self._config.reflection_min_tool_calls

    async def reflect_on_turn(self, digest: TurnDigest) -> ReflectionReport:
        """Run the meta-call and apply its (bounded, validated) memory ops.

        Never raises: any failure — the meta-call itself, garbled output, a
        store hiccup — degrades to an empty report.
        """

        try:
            prompt = build_reflection_prompt(
                digest=digest.render(),
                existing_memories=self._memory.render_for_prompt(
                    limit_per_kind=self._config.render_limit_per_kind
                ),
            )
            raw = await self._generator(prompt)
        except Exception:
            logger.debug("Reflection meta-call failed; nothing learned.", exc_info=True)
            return ReflectionReport()

        ops = _parse_reflection_ops(raw or "")
        return await self._apply(ops)

    async def _apply(self, ops: _ReflectionOps) -> ReflectionReport:
        saved: list[str] = []
        for kind, content in ops.saves[:_MAX_SAVES_PER_TURN]:
            try:
                entry = self._memory.add(kind=kind, content=content, source="reflection")
            except ValueError:
                continue  # unknown kind that slipped past validation
            saved.append(entry.content)
            await self._emit(
                "memory.saved",
                {"kind": entry.kind, "content": entry.content, "via": "reflection"},
            )

        retired: list[str] = []
        # Retire after saving, and never retire something saved this same pass:
        # a model that restates a kept fact in both lists must not delete it.
        for content in ops.retires[:_MAX_RETIRES_PER_TURN]:
            if content in saved:
                continue
            if self._memory.remove_by_content(content):
                retired.append(content)

        report = ReflectionReport(saved=tuple(saved), retired=tuple(retired))
        if report.changed:
            await self._emit(
                "memory.reflection.completed",
                {"saved": list(report.saved), "retired": list(report.retired)},
            )
        return report

    # -- consolidation --------------------------------------------------------

    async def maybe_consolidate(self) -> bool:
        """Curate any store that accumulated enough new memories.

        One conservative meta-call per due store: merge near-duplicates, drop
        contradicted facts, keep everything else. Returns True when anything
        actually changed, so the caller knows to refresh the prompt.
        """

        if not self._config.consolidation_enabled:
            return False
        changed = False
        for scope, store in self._memory.scoped_stores():
            if (
                store.new_entries_since_maintenance()
                < self._config.consolidation_min_new
            ):
                continue
            changed = await self._consolidate_store(scope, store) or changed
        return changed

    async def _consolidate_store(self, scope: str, store: MemoryStore) -> bool:
        entries = store.all()
        if not entries:
            store.mark_maintained()
            return False
        listing = "\n".join(
            f"{index}. [{entry.kind}] {entry.content}"
            for index, entry in enumerate(entries, start=1)
        )
        try:
            raw = await self._generator(
                build_consolidation_prompt(scope=scope, listing=listing)
            )
        except Exception:
            # Meta-call unavailable: leave the store untouched and *unmarked*,
            # so the pass retries on a later learning run.
            logger.debug("Consolidation meta-call failed for %s.", scope, exc_info=True)
            return False

        ops = _parse_consolidation_ops(raw or "", max_index=len(entries))
        rewritten = 0
        for index, content in ops.rewrites[:_MAX_CONSOLIDATION_OPS]:
            if store.rewrite(entries[index - 1].id, content, source="consolidation"):
                rewritten += 1
        drop_budget = max(0, _MAX_CONSOLIDATION_OPS - rewritten)
        rewrite_targets = {index for index, _content in ops.rewrites}
        dropped = 0
        for index in ops.drops:
            if dropped >= drop_budget:
                break
            if index in rewrite_targets:
                continue  # the kept-and-rewritten copy must survive
            entry = entries[index - 1]
            if entry.kind == "user":
                continue  # identity is never bulk-dropped by maintenance
            if store.remove(entry.id):
                dropped += 1

        # A successful meta-call resets the trigger baseline even with zero ops
        # ("store already clean" is a valid outcome); only generator failures
        # leave the marker untouched for a retry.
        store.mark_maintained()
        if not (dropped or rewritten):
            return False
        await self._emit(
            "memory.consolidated",
            {"scope": scope, "dropped": dropped, "rewritten": rewritten},
        )
        return True

    async def _emit(self, name: str, payload: dict[str, object]) -> None:
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(name, payload, source="core.reflection")
        except Exception:  # pragma: no cover - eventing must never break learning
            logger.debug("Failed to emit %s", name, exc_info=True)


def _parse_reflection_ops(raw: str) -> _ReflectionOps:
    """Extract validated memory ops from a (possibly chatty) model reply.

    Mirrors the project's other defensive parsers: locate the outermost JSON
    object, tolerate fences and prose around it, and drop anything malformed
    item-by-item instead of discarding the batch.
    """

    ops = _ReflectionOps()
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return ops
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return ops
    if not isinstance(data, dict):
        return ops

    saves = data.get("save")
    if isinstance(saves, list):
        for item in saves:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if kind in VALID_KINDS and content:
                ops.saves.append((kind, content))

    retires = data.get("retire")
    if isinstance(retires, list):
        ops.retires.extend(
            str(item).strip() for item in retires if str(item).strip()
        )
    return ops


@dataclass(slots=True)
class _ConsolidationOps:
    drops: list[int] = field(default_factory=list)
    rewrites: list[tuple[int, str]] = field(default_factory=list)  # (index, content)


def _parse_consolidation_ops(raw: str, *, max_index: int) -> _ConsolidationOps:
    """Extract validated 1-based consolidation ops from a model reply."""

    ops = _ConsolidationOps()
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return ops
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return ops
    if not isinstance(data, dict):
        return ops

    def _valid_index(value: object) -> int | None:
        try:
            index = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return index if 1 <= index <= max_index else None

    drops = data.get("drop")
    if isinstance(drops, list):
        seen: set[int] = set()
        for item in drops:
            index = _valid_index(item)
            if index is not None and index not in seen:
                seen.add(index)
                ops.drops.append(index)

    rewrites = data.get("rewrite")
    if isinstance(rewrites, list):
        for item in rewrites:
            if not isinstance(item, dict):
                continue
            index = _valid_index(item.get("n"))
            content = str(item.get("content") or "").strip()
            if index is not None and content:
                ops.rewrites.append((index, content))
    return ops
