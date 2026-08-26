from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# A reminder fires no earlier than this many tool rounds into a turn. Before
# that there is not enough observed behaviour to say anything useful, and an
# early nudge reads as noise on a task that was about to finish anyway.
_MIN_ROUNDS = 3
# Rounds a reminder must wait before it may fire again. A condition like "you
# have not verified yet" stays true for a while; repeating it every round trains
# the model to skim past reminders instead of acting on them.
_COOLDOWN_ROUNDS = 8
# Hard cap per turn across all reminders, for the same reason.
_MAX_PER_TURN = 3


@dataclass(frozen=True, slots=True)
class ToolRound:
    """What one round of tool calls did, classified by the caller.

    The caller owns the tool registry and therefore the capability lookup; this
    module deliberately takes the conclusions rather than the registry, so the
    rules stay pure predicates over observed behaviour and can be tested without
    building a session.
    """

    names: tuple[str, ...]
    # Every call in the round was read-only (and so ran concurrently).
    read_only: bool
    # At least one call created or edited a file.
    mutating: bool
    # At least one call executed a command.
    ran_process: bool
    # At least one call came back an error.
    errored: bool = False


@dataclass(slots=True)
class TurnToolActivity:
    """Running tally of how the current turn has been using its tools.

    Counts rounds rather than calls wherever the distinction matters: "three
    steps in a row that each read one file" is a batching problem, while "three
    files read" is not.
    """

    rounds: int = 0
    calls_by_tool: dict[str, int] = field(default_factory=dict)
    last_round_by_tool: dict[str, int] = field(default_factory=dict)
    # Rounds so far that consisted of exactly one read-only call. Reset by any
    # round that batches, mutates, or runs something.
    consecutive_single_read_rounds: int = 0
    read_only_rounds: int = 0
    last_mutation_round: int | None = None
    last_process_round: int | None = None
    errored_rounds: int = 0
    first_error_round: int | None = None

    def observe(self, round_: ToolRound) -> None:
        self.rounds += 1
        for name in round_.names:
            self.calls_by_tool[name] = self.calls_by_tool.get(name, 0) + 1
            self.last_round_by_tool[name] = self.rounds
        if round_.read_only:
            self.read_only_rounds += 1
            if len(round_.names) == 1:
                self.consecutive_single_read_rounds += 1
            else:
                self.consecutive_single_read_rounds = 0
        else:
            self.consecutive_single_read_rounds = 0
        if round_.mutating:
            self.last_mutation_round = self.rounds
        if round_.ran_process:
            self.last_process_round = self.rounds
        if round_.errored:
            self.errored_rounds += 1
            if self.first_error_round is None:
                self.first_error_round = self.rounds

    def used(self, name: str) -> bool:
        return self.calls_by_tool.get(name, 0) > 0

    def rounds_since(self, name: str) -> int | None:
        """Rounds elapsed since ``name`` last ran, or ``None`` if it never did."""
        last = self.last_round_by_tool.get(name)
        return None if last is None else self.rounds - last


@dataclass(frozen=True, slots=True)
class Reminder:
    """One thing worth pointing out, and the condition under which it is worth it.

    ``applies`` sees the turn's activity and the tools actually available, so a
    reminder never mentions a capability this session does not have.
    """

    name: str
    applies: Callable[[TurnToolActivity, frozenset[str]], bool]
    message: str


def _reading_one_at_a_time(activity: TurnToolActivity, tools: frozenset[str]) -> bool:
    return activity.consecutive_single_read_rounds >= 3


def _investigating_alone(activity: TurnToolActivity, tools: frozenset[str]) -> bool:
    return (
        "dispatch_agent" in tools
        and not activity.used("dispatch_agent")
        and activity.read_only_rounds >= 8
    )


def _checklist_drifting(activity: TurnToolActivity, tools: frozenset[str]) -> bool:
    since_plan = activity.rounds_since("submit_plan")
    if since_plan is None:
        return False
    # Before the first step is reported the plan itself is the anchor, so drift
    # is measured from whichever happened more recently.
    since_step = activity.rounds_since("complete_plan_step")
    return (since_step if since_step is not None else since_plan) >= 5


def _change_left_unverified(activity: TurnToolActivity, tools: frozenset[str]) -> bool:
    if activity.last_mutation_round is None:
        return False
    if activity.rounds - activity.last_mutation_round < 4:
        return False
    last_process = activity.last_process_round
    return last_process is None or last_process < activity.last_mutation_round


def _mistake_went_unrecorded(activity: TurnToolActivity, tools: frozenset[str]) -> bool:
    """Something failed, the agent worked past it, and nothing was written down.

    The runtime files its own lesson for failures it can recognise, but the ones
    worth the most are the ones only the agent can state: what it had understood
    wrongly, and what it now knows to do instead. That is only knowable once it
    has recovered - hence waiting for the work to move on from the error rather
    than asking at the moment of failure.
    """

    if "remember" not in tools or activity.first_error_round is None:
        return False
    if activity.rounds - activity.first_error_round < 3:
        return False
    saved = activity.last_round_by_tool.get("remember")
    return saved is None or saved < activity.first_error_round


DEFAULT_REMINDERS: tuple[Reminder, ...] = (
    Reminder(
        name="record_lesson",
        applies=_mistake_went_unrecorded,
        message=(
            "Something failed earlier this turn and nothing has been recorded "
            "about it. If you now understand what went wrong - a wrong "
            "assumption, a step that has to come first, a command that needs "
            "different arguments here - save that with remember (kind "
            "'feedback'), phrased as what to do next time rather than what "
            "happened. That is the only way the next session starts already "
            "knowing it. If the failure taught you nothing durable, ignore this."
        ),
    ),
    Reminder(
        name="batch_reads",
        applies=_reading_one_at_a_time,
        message=(
            "The last few steps each fetched a single piece of information on its "
            "own. When the things you still need to look at do not depend on each "
            "other, ask for them in one response - several read_file calls, or a "
            "list_files alongside a search_code - and they run concurrently."
        ),
    ),
    Reminder(
        name="fan_out",
        applies=_investigating_alone,
        message=(
            "This turn has spent many steps investigating one thread at a time. If "
            "independent questions are still open, dispatch_agent can run several "
            "explorer sub-agents at once and hand back what each of them found."
        ),
    ),
    Reminder(
        name="advance_checklist",
        applies=_checklist_drifting,
        message=(
            "The live checklist has not moved for several steps. If you have "
            "finished the step it is showing, call complete_plan_step so the user "
            "sees real progress; if your approach has changed, call submit_plan "
            "with the revised steps."
        ),
    ),
    Reminder(
        name="verify_change",
        applies=_change_left_unverified,
        message=(
            "You changed files earlier in this turn and have not run anything "
            "since. Running the project's tests or build now - rather than at the "
            "very end - is how you find out whether the change works while it is "
            "still cheap to fix."
        ),
    ),
)


class ReminderEngine:
    """Decides when the runtime should point something out to the model.

    Entirely deterministic: it observes which tools ran and, from fixed rules,
    surfaces at most one short note per round. Nothing here is a gate - a
    reminder is advice the model is free to ignore, so a wrong one costs a few
    tokens rather than a blocked turn. One engine belongs to one turn.
    """

    def __init__(
        self,
        reminders: tuple[Reminder, ...] = DEFAULT_REMINDERS,
        *,
        min_rounds: int = _MIN_ROUNDS,
        cooldown_rounds: int = _COOLDOWN_ROUNDS,
        max_per_turn: int = _MAX_PER_TURN,
    ) -> None:
        self._reminders = reminders
        self._min_rounds = min_rounds
        self._cooldown = cooldown_rounds
        self._max_per_turn = max_per_turn
        self.activity = TurnToolActivity()
        self._fired_at: dict[str, int] = {}
        self._fired_total = 0

    def observe(self, round_: ToolRound) -> None:
        self.activity.observe(round_)

    def due(self, available_tools: frozenset[str]) -> str | None:
        """The one reminder worth surfacing now, or ``None`` (the usual case)."""

        if self.activity.rounds < self._min_rounds:
            return None
        if self._fired_total >= self._max_per_turn:
            return None
        for reminder in self._reminders:
            last = self._fired_at.get(reminder.name)
            if last is not None and self.activity.rounds - last < self._cooldown:
                continue
            if not reminder.applies(self.activity, available_tools):
                continue
            self._fired_at[reminder.name] = self.activity.rounds
            self._fired_total += 1
            return reminder.message
        return None
