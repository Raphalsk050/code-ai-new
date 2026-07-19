from __future__ import annotations

from code_ai.config.models import GoalConfig
from code_ai.core.errors import GoalStateError
from code_ai.core.goal.models import (
    AcceptanceCriterion,
    Goal,
    GoalEvaluationReport,
    GoalIterationRecord,
    GoalStatus,
)
from code_ai.events.bus import AsyncEventBus
from code_ai.events.models import utc_now_iso
from code_ai.tools.output import bound_text

# Continuation prompts must read as an imperative mutation request so the
# per-turn TaskProfile classifier keeps the file/verification evidence gates
# active (see TaskProfile.from_user_text) instead of misreading the follow-up
# as conversation.
_MAX_PROMPT_GAP_LINES = 8


class GoalService:
    """Owns the goal lifecycle: one goal at a time, explicit transitions, events.

    Pure state machine — it never runs turns or evaluates criteria itself; the
    app-layer GoalRunner drives it. Illegal transitions raise
    :class:`GoalStateError` so a UI bug can never silently corrupt goal state.
    """

    def __init__(self, *, config: GoalConfig, event_bus: AsyncEventBus) -> None:
        self.config = config
        self.event_bus = event_bus
        self.goal: Goal | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def define(self, objective: str) -> Goal:
        if self.goal is not None and not self.goal.is_terminal:
            raise GoalStateError(
                "A goal is already defined "
                f"({self.goal.status.value.lower()}). Stop it with /goal stop "
                "before defining a new one."
            )
        self.goal = Goal(objective=objective)
        await self.event_bus.emit(
            "goal.defined",
            {"goal_id": self.goal.goal_id, "objective": self.goal.objective},
            source="core.goal",
        )
        return self.goal

    async def propose_criteria(self, criteria: list[AcceptanceCriterion]) -> None:
        goal = self._require_goal()
        if goal.status != GoalStatus.DRAFT:
            raise GoalStateError(
                f"Criteria can only be proposed on a draft goal, not {goal.status.value}."
            )
        if not criteria:
            raise GoalStateError("At least one acceptance criterion is required.")
        goal.criteria = list(criteria)
        goal.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "goal.criteria.proposed",
            {
                "goal_id": goal.goal_id,
                "criteria": [item.label() for item in goal.criteria],
            },
            source="core.goal",
        )

    async def activate(self) -> Goal:
        goal = self._require_goal()
        if goal.status != GoalStatus.DRAFT:
            raise GoalStateError(
                f"Only a draft goal can be activated, not {goal.status.value}."
            )
        if not goal.criteria:
            raise GoalStateError(
                "The goal has no acceptance criteria yet; propose them first."
            )
        goal.status = GoalStatus.ACTIVE
        goal.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "goal.activated",
            {
                "goal_id": goal.goal_id,
                "objective": goal.objective,
                "criteria": [item.label() for item in goal.criteria],
                "max_iterations": self.config.max_iterations,
            },
            source="core.goal",
        )
        return goal

    async def resume(self) -> Goal:
        goal = self._require_goal()
        if goal.status != GoalStatus.BLOCKED:
            raise GoalStateError(
                f"Only a blocked goal can be resumed, not {goal.status.value}."
            )
        goal.status = GoalStatus.ACTIVE
        goal.stop_reason = ""
        goal.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "goal.resumed", {"goal_id": goal.goal_id}, source="core.goal"
        )
        return goal

    async def note_iteration_started(self, index: int) -> None:
        goal = self._require_active_goal()
        await self.event_bus.emit(
            "goal.iteration.started",
            {
                "goal_id": goal.goal_id,
                "iteration": index,
                "max_iterations": self.config.max_iterations,
            },
            source="core.goal",
        )

    async def record_iteration(self, record: GoalIterationRecord) -> None:
        goal = self._require_active_goal()
        goal.iterations.append(record)
        goal.updated_at = utc_now_iso()
        if record.report is not None:
            for result in record.report.results:
                await self.event_bus.emit(
                    "goal.criterion.evaluated",
                    {
                        "goal_id": goal.goal_id,
                        "iteration": record.index,
                        "criterion_id": result.criterion_id,
                        "met": result.met,
                        "detail": result.detail,
                    },
                    source="core.goal",
                )
        met = len(record.report.results) - len(record.report.unmet()) if record.report else 0
        total = len(record.report.results) if record.report else len(goal.criteria)
        await self.event_bus.emit(
            "goal.iteration.completed",
            {
                "goal_id": goal.goal_id,
                "iteration": record.index,
                "max_iterations": self.config.max_iterations,
                "criteria_met": met,
                "criteria_total": total,
                "turn_error": record.turn_error,
                "wind_down_reason": record.wind_down_reason,
            },
            source="core.goal",
        )

    async def satisfy(self, report: GoalEvaluationReport) -> Goal:
        goal = self._require_active_goal()
        if not report.all_met:
            raise GoalStateError(
                "Cannot mark the goal satisfied: unmet criteria remain "
                f"({[item.criterion_id for item in report.unmet()]})."
            )
        goal.status = GoalStatus.SATISFIED
        goal.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "goal.satisfied",
            {
                "goal_id": goal.goal_id,
                "objective": goal.objective,
                "iterations": len(goal.iterations),
            },
            source="core.goal",
        )
        return goal

    async def block(self, reason: str) -> Goal:
        goal = self._require_active_goal()
        goal.status = GoalStatus.BLOCKED
        goal.stop_reason = reason
        goal.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "goal.blocked",
            {"goal_id": goal.goal_id, "reason": reason},
            source="core.goal",
        )
        return goal

    async def exhaust(self, reason: str) -> Goal:
        goal = self._require_active_goal()
        goal.status = GoalStatus.EXHAUSTED
        goal.stop_reason = reason
        goal.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "goal.exhausted",
            {
                "goal_id": goal.goal_id,
                "reason": reason,
                "snapshot": goal.snapshot(),
            },
            source="core.goal",
        )
        return goal

    async def stop(self, reason: str) -> Goal:
        goal = self._require_goal()
        if goal.is_terminal:
            return goal
        goal.status = GoalStatus.STOPPED
        goal.stop_reason = reason
        goal.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "goal.stopped",
            {"goal_id": goal.goal_id, "reason": reason},
            source="core.goal",
        )
        return goal

    # ------------------------------------------------------------------ #
    # Loop guidance
    # ------------------------------------------------------------------ #
    def continuation_prompt(self) -> str:
        """The user-channel prompt driving the next iteration's turn.

        The first iteration carries the objective itself. Later iterations
        carry the unmet criteria with the evaluator's feedback, phrased as an
        imperative follow-up so the turn classifier keeps the mutation
        evidence gates active.
        """
        goal = self._require_active_goal()
        latest = goal.latest_report()
        if latest is None:
            return goal.objective
        unmet = latest.unmet()
        by_id = {item.criterion_id: item for item in goal.criteria}
        lines = []
        for result in unmet[:_MAX_PROMPT_GAP_LINES]:
            criterion = by_id.get(result.criterion_id)
            label = criterion.label() if criterion else result.criterion_id
            detail = f" — {result.detail}" if result.detail else ""
            lines.append(f"- {label}{detail}")
        if len(unmet) > _MAX_PROMPT_GAP_LINES:
            lines.append(f"- (+{len(unmet) - _MAX_PROMPT_GAP_LINES} more)")
        return (
            "Continue trabalhando no objetivo persistente e corrija o que "
            "falta. A avaliação independente reprovou estes critérios de "
            "aceitação:\n"
            + "\n".join(lines)
            + "\n\nImplemente as mudanças necessárias no workspace para "
            "satisfazer cada critério reprovado e verifique o resultado."
        )

    def context_block(self, *, iteration: int) -> str:
        """Host-state block injected alongside each iteration's prompt."""
        goal = self._require_active_goal()
        latest = goal.latest_report()
        met_by_id = (
            {result.criterion_id: result.met for result in latest.results}
            if latest
            else {}
        )
        lines = []
        for criterion in goal.criteria:
            met = met_by_id.get(criterion.criterion_id)
            state = "met" if met else ("NOT met" if met is not None else "not evaluated yet")
            lines.append(f"- {criterion.label()} -> {state}")
        return (
            "Persistent goal state. Treat this as authoritative host state, "
            "not a user request.\n"
            f"Goal: {bound_text(goal.objective, 600)}\n"
            f"Iteration: {iteration}/{self.config.max_iterations}\n"
            "Acceptance criteria (ALL must pass an independent evaluation "
            "before the goal completes; your own claims do not count):\n"
            + "\n".join(lines)
        )

    def no_progress_exceeded(self) -> bool:
        """True when the last N evaluated iterations show the exact same failure.

        Same unmet criteria *and* the same progress marker (nothing changed in
        the workspace) for ``max_no_progress_iterations`` consecutive
        iterations means another blind lap will not help — escalate instead.
        """
        goal = self._require_goal()
        limit = max(2, self.config.max_no_progress_iterations)
        if len(goal.iterations) < limit:
            return False
        signatures = [
            record.stagnation_signature() for record in goal.iterations[-limit:]
        ]
        if any(signature is None for signature in signatures):
            return False
        first = signatures[0]
        # An empty failure signature means everything passed; never stagnation.
        if first is not None and not first[0]:
            return False
        return all(signature == first for signature in signatures)

    def snapshot(self) -> dict[str, object]:
        if self.goal is None:
            return {"status": "none"}
        return self.goal.snapshot()

    # ------------------------------------------------------------------ #
    # Internal guards
    # ------------------------------------------------------------------ #
    def _require_goal(self) -> Goal:
        if self.goal is None:
            raise GoalStateError("No goal is defined. Use /goal <objetivo> first.")
        return self.goal

    def _require_active_goal(self) -> Goal:
        goal = self._require_goal()
        if goal.status != GoalStatus.ACTIVE:
            raise GoalStateError(
                f"This operation requires an active goal, not {goal.status.value}."
            )
        return goal
