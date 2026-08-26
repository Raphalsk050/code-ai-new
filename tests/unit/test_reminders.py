from __future__ import annotations

from code_ai.core.reminders import DEFAULT_REMINDERS, Reminder, ReminderEngine, ToolRound

ALL_TOOLS = frozenset(
    {"read_file", "search_code", "list_files", "write_file", "edit_code",
     "execute_command", "dispatch_agent", "submit_plan", "complete_plan_step"}
)


def _read(name: str = "read_file") -> ToolRound:
    return ToolRound(names=(name,), read_only=True, mutating=False, ran_process=False)


def _batched_read() -> ToolRound:
    return ToolRound(
        names=("read_file", "search_code"), read_only=True, mutating=False, ran_process=False
    )


def _write() -> ToolRound:
    return ToolRound(
        names=("write_file",), read_only=False, mutating=True, ran_process=False
    )


def _run() -> ToolRound:
    return ToolRound(
        names=("execute_command",), read_only=False, mutating=False, ran_process=True
    )


def _drive(engine: ReminderEngine, rounds: list[ToolRound]) -> list[str]:
    fired = []
    for round_ in rounds:
        engine.observe(round_)
        message = engine.due(ALL_TOOLS)
        if message:
            fired.append(message)
    return fired


def test_quiet_while_the_turn_behaves() -> None:
    """Batched reads followed by a verified change deserve no commentary."""

    engine = ReminderEngine()
    fired = _drive(engine, [_batched_read(), _batched_read(), _write(), _run()] * 3)
    assert fired == []


def test_nothing_fires_before_the_minimum_rounds() -> None:
    engine = ReminderEngine()
    assert _drive(engine, [_read(), _read()]) == []


def test_serial_reads_prompt_batching() -> None:
    engine = ReminderEngine()
    fired = _drive(engine, [_read()] * 4)
    assert len(fired) == 1
    assert "one response" in fired[0]


def test_batching_resets_the_serial_read_streak() -> None:
    engine = ReminderEngine()
    assert _drive(engine, [_read(), _read(), _batched_read(), _read(), _read()]) == []


def test_reminder_respects_its_cooldown() -> None:
    """A condition that stays true must not be repeated every round.

    The cooldown is per reminder, so this counts one reminder's own firings
    rather than the total - a long solo-read streak legitimately trips the
    fan-out reminder as well.
    """

    engine = ReminderEngine(cooldown_rounds=8)
    fired = _drive(engine, [_read()] * 9)
    assert sum("one response" in message for message in fired) == 1


def test_reminder_may_fire_again_after_the_cooldown() -> None:
    engine = ReminderEngine(cooldown_rounds=3, max_per_turn=99)
    fired = _drive(engine, [_read()] * 10)
    assert sum("one response" in message for message in fired) > 1


def test_unverified_change_is_raised_once_reads_pile_up() -> None:
    engine = ReminderEngine()
    fired = _drive(engine, [_write()] + [_batched_read()] * 4)
    assert len(fired) == 1
    assert "tests or build" in fired[0]


def test_running_something_after_the_change_stays_quiet() -> None:
    engine = ReminderEngine()
    assert _drive(engine, [_write(), _run()] + [_batched_read()] * 4) == []


def test_long_solo_investigation_suggests_fanning_out() -> None:
    engine = ReminderEngine()
    fired = _drive(engine, [_batched_read()] * 9)
    assert any("explorer sub-agents" in message for message in fired)


def test_fan_out_is_never_suggested_when_delegation_is_unavailable() -> None:
    """A sub-agent has no dispatch tool; naming one would be an invalid nudge."""

    engine = ReminderEngine()
    tools = ALL_TOOLS - {"dispatch_agent"}
    for _ in range(12):
        engine.observe(_batched_read())
        assert engine.due(tools) is None


def test_stale_checklist_is_flagged() -> None:
    engine = ReminderEngine()
    plan = ToolRound(
        names=("submit_plan",), read_only=False, mutating=False, ran_process=False
    )
    fired = _drive(engine, [plan] + [_batched_read()] * 6)
    assert any("complete_plan_step" in message for message in fired)


def test_per_turn_cap_holds() -> None:
    always = tuple(
        Reminder(name=f"r{index}", applies=lambda a, t: True, message=f"note {index}")
        for index in range(6)
    )
    engine = ReminderEngine(always, max_per_turn=2)
    fired = _drive(engine, [_read()] * 12)
    assert len(fired) == 2


def test_default_reminders_have_distinct_names() -> None:
    names = [reminder.name for reminder in DEFAULT_REMINDERS]
    assert len(names) == len(set(names))


def test_rounds_since_reports_none_for_unused_tools() -> None:
    engine = ReminderEngine()
    engine.observe(_read())
    assert engine.activity.rounds_since("write_file") is None
    assert engine.activity.rounds_since("read_file") == 0
