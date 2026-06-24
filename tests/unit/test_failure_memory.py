from __future__ import annotations

from code_ai.core.memory import FailureMemory, FailureMemoryStore


def _store(tmp_path, generator=None) -> FailureMemoryStore:
    return FailureMemoryStore(tmp_path / "memories", lesson_generator=generator)


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
