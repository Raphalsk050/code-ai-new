from __future__ import annotations

from code_ai.core.memory import FailureMemoryStore
from code_ai.core.reminders import ReminderEngine, ToolRound

TOOLS = frozenset({"read_file", "edit_code", "execute_command", "remember"})


def _round(*names: str, errored: bool = False) -> ToolRound:
    return ToolRound(
        names=names or ("read_file",),
        read_only=False,
        mutating=False,
        ran_process=False,
        errored=errored,
    )


async def _record(store: FailureMemoryStore, signature: str, lesson: str) -> None:
    await store.record(
        trigger="tool_error", signature=signature, context="ctx", fallback_lesson=lesson
    )


async def test_lesson_is_retrievable_by_its_exact_signature(tmp_path) -> None:
    """Keyed lookup is what makes recall reliable rather than best-effort."""

    store = FailureMemoryStore(tmp_path)
    await _record(store, "tool_error:edit_code", "Read the file before editing it.")

    found = store.lesson_for("tool_error:edit_code")
    assert found is not None
    assert found.lesson == "Read the file before editing it."


async def test_unknown_signature_returns_nothing(tmp_path) -> None:
    store = FailureMemoryStore(tmp_path)
    await _record(store, "tool_error:edit_code", "Read the file first.")
    assert store.lesson_for("tool_error:write_file") is None


async def test_lookup_on_an_empty_store_is_safe(tmp_path) -> None:
    assert FailureMemoryStore(tmp_path).lesson_for("tool_error:read_file") is None


async def test_recurrence_is_counted_for_the_same_signature(tmp_path) -> None:
    """The count is what lets a warning say how often this has already bitten."""

    store = FailureMemoryStore(tmp_path)
    for _ in range(3):
        await _record(store, "tool_error:edit_code", "Read the file first.")
    found = store.lesson_for("tool_error:edit_code")
    assert found is not None and found.count == 3


def test_unrecorded_mistake_is_raised_once_the_work_moves_on() -> None:
    engine = ReminderEngine()
    fired = []
    for round_ in [_round(errored=True)] + [_round() for _ in range(4)]:
        engine.observe(round_)
        message = engine.due(TOOLS)
        if message:
            fired.append(message)
    assert any("remember" in message for message in fired)


def test_nothing_is_raised_while_the_failure_is_still_fresh() -> None:
    """Asked too early, the agent does not yet know what the lesson is."""

    engine = ReminderEngine()
    engine.observe(_round(errored=True))
    engine.observe(_round())
    assert engine.due(TOOLS) is None


def test_saving_after_the_failure_settles_it() -> None:
    engine = ReminderEngine()
    engine.observe(_round(errored=True))
    engine.observe(_round("remember"))
    for _ in range(5):
        engine.observe(_round())
        message = engine.due(TOOLS)
        assert message is None or "remember" not in message


def test_a_save_made_before_the_failure_does_not_count() -> None:
    engine = ReminderEngine()
    engine.observe(_round("remember"))
    engine.observe(_round(errored=True))
    fired = []
    for _ in range(5):
        engine.observe(_round())
        message = engine.due(TOOLS)
        if message:
            fired.append(message)
    assert any("remember" in message for message in fired)


def test_an_errorless_turn_is_never_asked_to_record() -> None:
    engine = ReminderEngine()
    fired = []
    for _ in range(10):
        engine.observe(_round())
        message = engine.due(TOOLS)
        if message:
            fired.append(message)
    assert not any("remember" in message for message in fired)


def test_nothing_is_suggested_when_remember_is_unavailable() -> None:
    """A sub-agent has no memory tool; naming it would be a dead-end nudge."""

    engine = ReminderEngine()
    tools = TOOLS - {"remember"}
    fired = []
    for round_ in [_round(errored=True)] + [_round() for _ in range(6)]:
        engine.observe(round_)
        message = engine.due(tools)
        if message:
            fired.append(message)
    assert not any("remember" in message for message in fired)
