from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from code_ai.config.models import GoalConfig
from code_ai.core.goal.evaluator import EvaluationContext, GoalEvaluator
from code_ai.core.goal.models import Goal, GoalIterationRecord
from code_ai.core.goal.service import GoalService
from code_ai.core.orchestration import TurnResult
from code_ai.tools.output import bound_text

# The runner talks to the rest of the app through three callables so it can be
# tested with fakes and never imports CodeAIApplication (no app-internal cycle).
# RunIteration submits one turn: (prompt, context_block) -> TurnResult.
RunIteration = Callable[[str, str], Awaitable[TurnResult]]
# ProgressMarker snapshots what the last turn changed (changed paths +
# verification flag), the workspace half of the stagnation signature.
ProgressMarker = Callable[[], str]
# EvidenceSummary renders the last turn's evidence ledger for the judge.
EvidenceSummary = Callable[[], str]

# Consecutive iterations ending in a provider/turn error before the goal blocks:
# a persistent provider failure must not burn the whole iteration budget.
_MAX_CONSECUTIVE_TURN_ERRORS = 2


class GoalRunner:
    """Drives an active goal to satisfaction, one full turn per iteration.

    The loop above the turns: each iteration is a normal ``run_turn`` with all
    of its safety budgets intact, followed by an independent evaluation of the
    goal's acceptance criteria. Only that evaluation — never the model's own
    claim — ends the loop in SATISFIED. Every other exit is an explicit guard:
    user stop, stagnation (BLOCKED), or the hard iteration/time ceilings
    (EXHAUSTED).
    """

    def __init__(
        self,
        *,
        service: GoalService,
        evaluator: GoalEvaluator,
        config: GoalConfig,
        run_iteration: RunIteration,
        progress_marker: ProgressMarker,
        evidence_summary: EvidenceSummary,
    ) -> None:
        self._service = service
        self._evaluator = evaluator
        self._config = config
        self._run_iteration = run_iteration
        self._progress_marker = progress_marker
        self._evidence_summary = evidence_summary
        self._stop_requested = False

    def request_stop(self) -> None:
        """Ask the loop to end after the in-flight iteration settles.

        Cooperative by design: the current turn is cancelled separately (the
        application owns the turn's cancel event), and the runner observes the
        flag at the next checkpoint.
        """
        self._stop_requested = True

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    async def run(self) -> Goal:
        goal = self._require_active_goal()
        deadline = time.monotonic() + self._config.max_goal_minutes * 60
        consecutive_turn_errors = 0

        while True:
            if self._stop_requested:
                return await self._service.stop("stopped by user")
            index = len(goal.iterations) + 1
            if index > self._config.max_iterations:
                return await self._service.exhaust(
                    f"reached the iteration ceiling ({self._config.max_iterations})"
                )
            if time.monotonic() > deadline:
                return await self._service.exhaust(
                    f"reached the time ceiling ({self._config.max_goal_minutes} minutes)"
                )
            if self._service.no_progress_exceeded():
                return await self._service.block(
                    "the same acceptance criteria kept failing with no workspace "
                    f"progress for {self._config.max_no_progress_iterations} "
                    "consecutive iterations"
                )

            prompt = self._service.continuation_prompt()
            context = self._service.context_block(iteration=index)
            await self._service.note_iteration_started(index)
            try:
                result = await self._run_iteration(prompt, context)
            except Exception as exc:
                # A turn that cannot even start (e.g. another turn is running)
                # is not something more laps can fix.
                return await self._service.block(f"iteration could not run: {exc}")

            if result.cancelled or self._stop_requested:
                return await self._service.stop(
                    "stopped by user" if self._stop_requested else "turn cancelled"
                )

            report = await self._evaluator.evaluate(
                goal,
                EvaluationContext(
                    last_answer=result.text or "",
                    evidence_summary=self._safe_evidence_summary(),
                ),
            )
            await self._service.record_iteration(
                GoalIterationRecord(
                    index=index,
                    prompt=bound_text(prompt, 600),
                    progress_marker=self._safe_progress_marker(),
                    turn_error=result.error or "",
                    wind_down_reason=result.wind_down_reason or "",
                    report=report,
                )
            )
            if report.all_met:
                return await self._service.satisfy(report)

            if result.error:
                consecutive_turn_errors += 1
                if consecutive_turn_errors >= _MAX_CONSECUTIVE_TURN_ERRORS:
                    return await self._service.block(
                        f"{consecutive_turn_errors} consecutive iterations failed "
                        f"with turn errors (last: {bound_text(result.error, 300)})"
                    )
            else:
                consecutive_turn_errors = 0

    def _require_active_goal(self) -> Goal:
        goal = self._service.goal
        if goal is None:
            raise RuntimeError("GoalRunner started without a goal.")
        return goal

    def _safe_progress_marker(self) -> str:
        try:
            return self._progress_marker()
        except Exception:
            return ""

    def _safe_evidence_summary(self) -> str:
        try:
            return self._evidence_summary()
        except Exception:
            return ""
