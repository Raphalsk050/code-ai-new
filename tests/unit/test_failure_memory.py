from __future__ import annotations

import hashlib
import json
import time

from code_ai.core.memory import FailureMemory, FailureMemoryStore


def _store(tmp_path, generator=None, **kwargs) -> FailureMemoryStore:
    return FailureMemoryStore(tmp_path / "memories", lesson_generator=generator, **kwargs)


def _write_entry(
    directory, *, signature: str, lesson: str, count: int, age_days: float
) -> None:
    """Write a lesson file directly, with a controlled age and count.

    Uses the store's production naming (hash of the signature) so entries
    written here are indistinguishable from recorded ones — including for
    eviction, which resolves files by that name.
    """

    directory.mkdir(parents=True, exist_ok=True)
    seen = time.time() - age_days * 86400.0
    entry = FailureMemory(
        signature=signature,
        trigger="t",
        lesson=lesson,
        count=count,
        first_seen=seen,
        last_seen=seen,
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    (directory / f"{digest}.json").write_text(
        json.dumps(entry.to_dict()), encoding="utf-8"
    )


async def test_records_model_generated_lesson(tmp_path) -> None:
    async def generator(context: str) -> str:
        assert "budget" in context
        return "Slice large files into smaller edits."

    store = _store(tmp_path, generator)
    entry = await store.record(
        trigger="token_budget_exceeded",
        context="ran out of budget",
        fallback_lesson="fallback",
    )

    assert entry.lesson == "Slice large files into smaller edits."
    assert entry.count == 1
    assert store.lessons()[0].lesson == "Slice large files into smaller edits."


async def test_dedup_skips_generator_and_bumps_count(tmp_path) -> None:
    calls = 0

    async def generator(context: str) -> str:
        nonlocal calls
        calls += 1
        return f"lesson {calls}"

    store = _store(tmp_path, generator)
    await store.record(trigger="stall", context="c", fallback_lesson="f")
    second = await store.record(trigger="stall", context="c", fallback_lesson="f")

    # Same signature: the model is consulted only once; the repeat just reinforces.
    assert calls == 1
    assert second.count == 2
    assert len(store.lessons()) == 1


async def test_distinct_signatures_are_separate(tmp_path) -> None:
    async def generator(context: str) -> str:
        return "lesson"

    store = _store(tmp_path, generator)
    await store.record(
        trigger="tool_error", signature="tool_error:read_file", context="c", fallback_lesson="f"
    )
    await store.record(
        trigger="tool_error", signature="tool_error:write_file", context="c", fallback_lesson="f"
    )

    assert len(store.lessons()) == 2


async def test_falls_back_when_generator_raises(tmp_path) -> None:
    async def generator(context: str) -> str:
        raise RuntimeError("meta-call itself truncated")

    store = _store(tmp_path, generator)
    entry = await store.record(
        trigger="token_budget_exceeded",
        context="c",
        fallback_lesson="Commit to one concrete action.",
    )

    assert entry.lesson == "Commit to one concrete action."


async def test_falls_back_when_generator_returns_empty(tmp_path) -> None:
    async def generator(context: str) -> str:
        return "   "

    store = _store(tmp_path, generator)
    entry = await store.record(
        trigger="token_budget_exceeded", context="c", fallback_lesson="use the fallback"
    )

    assert entry.lesson == "use the fallback"


async def test_uses_fallback_without_a_generator(tmp_path) -> None:
    store = _store(tmp_path, None)
    entry = await store.record(trigger="stall", context="c", fallback_lesson="deterministic")

    assert entry.lesson == "deterministic"


def test_render_for_prompt_empty_when_no_lessons(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.render_for_prompt() == ""


async def test_render_for_prompt_lists_lessons(tmp_path) -> None:
    async def generator(context: str) -> str:
        return "Always validate paths."

    store = _store(tmp_path, generator)
    await store.record(
        trigger="tool_error", signature="tool_error:read_file", context="c", fallback_lesson="f"
    )

    rendered = store.render_for_prompt()
    assert "Lessons learned" in rendered
    assert "- Always validate paths." in rendered


async def test_lessons_survive_a_fresh_store_instance(tmp_path) -> None:
    async def generator(context: str) -> str:
        return "persisted lesson"

    await _store(tmp_path, generator).record(
        trigger="stall", context="c", fallback_lesson="f"
    )

    # A brand-new store over the same directory recalls what was learned.
    reopened = _store(tmp_path)
    assert reopened.lessons()[0].lesson == "persisted lesson"


def test_corrupt_entry_is_ignored(tmp_path) -> None:
    directory = tmp_path / "memories"
    directory.mkdir(parents=True)
    (directory / "deadbeef.json").write_text("{not json", encoding="utf-8")

    store = FailureMemoryStore(directory)
    # A corrupt file must not break recall.
    assert store.lessons() == []
    assert store.render_for_prompt() == ""


async def test_long_lesson_is_clipped(tmp_path) -> None:
    async def generator(context: str) -> str:
        return "x" * 5000

    store = _store(tmp_path, generator)
    entry = await store.record(trigger="stall", context="c", fallback_lesson="f")

    assert len(entry.lesson) <= 400


def test_round_trips_through_dict() -> None:
    entry = FailureMemory(signature="s", trigger="t", lesson="l", count=3)
    restored = FailureMemory.from_dict(entry.to_dict())

    assert restored.signature == "s"
    assert restored.trigger == "t"
    assert restored.lesson == "l"
    assert restored.count == 3


def test_reinforced_lesson_outranks_fresh_one_off(tmp_path) -> None:
    directory = tmp_path / "memories"
    # Hit 40 times, last seen two days ago vs. seen once just now. The chronic
    # lesson's reinforcement bonus (log2(41) ~ 5.4 days) must win.
    _write_entry(directory, signature="chronic", lesson="chronic", count=40, age_days=2)
    _write_entry(directory, signature="fresh", lesson="fresh", count=1, age_days=0)

    ordered = [e.signature for e in FailureMemoryStore(directory).lessons()]
    assert ordered == ["chronic", "fresh"]


def test_recency_still_orders_equally_reinforced_lessons(tmp_path) -> None:
    directory = tmp_path / "memories"
    _write_entry(directory, signature="older", lesson="older", count=1, age_days=3)
    _write_entry(directory, signature="newer", lesson="newer", count=1, age_days=0)

    ordered = [e.signature for e in FailureMemoryStore(directory).lessons()]
    assert ordered == ["newer", "older"]


def test_chronic_lesson_is_pinned_into_the_render(tmp_path) -> None:
    directory = tmp_path / "memories"
    # An old chronic lesson whose score falls far below three fresh one-offs.
    _write_entry(directory, signature="chronic", lesson="chronic", count=6, age_days=60)
    _write_entry(directory, signature="faded", lesson="faded", count=4, age_days=60)
    for i in range(3):
        _write_entry(directory, signature=f"f{i}", lesson=f"fresh {i}", count=1, age_days=0)

    rendered = FailureMemoryStore(directory, pin_count=5).render_for_prompt(limit=3)

    assert "- chronic" in rendered  # pinned despite falling outside the top 3
    assert "- faded" not in rendered  # below pin_count: subject to the limit
    assert rendered.count("- fresh") == 3


async def test_prune_evicts_weakest_not_merely_oldest(tmp_path) -> None:
    directory = tmp_path / "memories"
    # chronic: 4 days old but 50 hits -> score ~ now + 1.7 days.
    # one-off: 2 days old, 1 hit    -> score ~ now - 1 day (the weakest).
    _write_entry(directory, signature="chronic", lesson="chronic", count=50, age_days=4)
    _write_entry(directory, signature="one-off", lesson="one-off", count=1, age_days=2)

    store = FailureMemoryStore(directory, max_entries=2)
    await store.record(trigger="new", context="c", fallback_lesson="new lesson")

    # The cap evicted the weakest entry; the chronic lesson survives even
    # though it is the oldest by recency alone.
    assert {e.signature for e in store.lessons()} == {"chronic", "new"}
