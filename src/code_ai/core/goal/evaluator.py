from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from code_ai.core.goal.models import (
    AcceptanceCriterion,
    CriterionKind,
    CriterionResult,
    Goal,
    GoalEvaluationReport,
)
from code_ai.tools.output import bound_text

# Ports injected by the app layer so this module never imports providers or
# process tooling directly (dependency rule: core knows only its own contracts).
# CommandPort runs a shell command inside the workspace and returns
# (exit_code, combined output tail); JudgePort is a one-off model completion
# (system prompt, user prompt) -> raw text.
CommandPort = Callable[[str], Awaitable[tuple[int | None, str]]]
JudgePort = Callable[[str, str], Awaitable[str]]

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict acceptance-criterion judge for an autonomous coding "
    "agent. You receive one criterion, the goal it belongs to, and the "
    "evidence from the latest work iteration. Decide whether the criterion is "
    "objectively met RIGHT NOW based on the evidence alone. Be skeptical: "
    "claims without evidence are not met, partial work is not met, and when "
    "in doubt answer not met. Reply with ONLY a JSON object, no prose and no "
    'code fences: {"met": true|false, "justification": "<one sentence>"}.'
)

# Output given to the judge is bounded so a huge turn transcript cannot blow
# the one-off call's context.
_JUDGE_EVIDENCE_CHARS = 6000
_RESULT_DETAIL_CHARS = 400


@dataclass(slots=True)
class EvaluationContext:
    """Evidence handed to evaluators about the iteration that just finished."""

    last_answer: str = ""
    evidence_summary: str = ""


class CommandCriterionEvaluator:
    kind = CriterionKind.COMMAND

    def __init__(self, command_port: CommandPort) -> None:
        self._command_port = command_port

    async def evaluate(
        self, criterion: AcceptanceCriterion, context: EvaluationContext
    ) -> CriterionResult:
        exit_code, output = await self._command_port(criterion.command)
        met = exit_code == 0
        detail = f"exit code {exit_code}"
        if not met and output:
            detail += f": {bound_text(output, _RESULT_DETAIL_CHARS)}"
        return CriterionResult(
            criterion_id=criterion.criterion_id, met=met, detail=detail
        )


class FileCriterionEvaluator:
    kind = CriterionKind.FILE

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def evaluate(
        self, criterion: AcceptanceCriterion, context: EvaluationContext
    ) -> CriterionResult:
        candidate = Path(criterion.path).expanduser()
        if not candidate.is_absolute():
            candidate = self._workspace / candidate
        if not candidate.exists():
            return CriterionResult(
                criterion_id=criterion.criterion_id,
                met=False,
                detail=f"path does not exist: {criterion.path}",
            )
        if not criterion.pattern:
            return CriterionResult(
                criterion_id=criterion.criterion_id, met=True, detail="path exists"
            )
        if not candidate.is_file():
            return CriterionResult(
                criterion_id=criterion.criterion_id,
                met=False,
                detail=f"pattern requires a file, got a directory: {criterion.path}",
            )
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return CriterionResult(
                criterion_id=criterion.criterion_id,
                met=False,
                detail=f"could not read {criterion.path}: {exc}",
            )
        if criterion.pattern in content:
            return CriterionResult(
                criterion_id=criterion.criterion_id,
                met=True,
                detail="path exists and contains the pattern",
            )
        return CriterionResult(
            criterion_id=criterion.criterion_id,
            met=False,
            detail=f"pattern not found in {criterion.path}: {criterion.pattern!r}",
        )


class JudgeCriterionEvaluator:
    kind = CriterionKind.JUDGE

    def __init__(self, judge_port: JudgePort, *, enabled: bool = True) -> None:
        self._judge_port = judge_port
        self._enabled = enabled

    async def evaluate(
        self, criterion: AcceptanceCriterion, context: EvaluationContext
    ) -> CriterionResult:
        if not self._enabled:
            # With the judge disabled, subjective criteria cannot gate the goal
            # forever: they pass with an explicit caveat instead of trapping the
            # loop demanding an evaluation that will never run.
            return CriterionResult(
                criterion_id=criterion.criterion_id,
                met=True,
                detail="judge disabled by configuration; criterion not enforced",
            )
        user_prompt = (
            f"Criterion: {criterion.description}\n\n"
            "Evidence recorded during the latest iteration:\n"
            f"{bound_text(context.evidence_summary or '(none)', _JUDGE_EVIDENCE_CHARS)}\n\n"
            "Agent's final answer for the latest iteration:\n"
            f"{bound_text(context.last_answer or '(none)', _JUDGE_EVIDENCE_CHARS)}"
        )
        raw = await self._judge_port(_JUDGE_SYSTEM_PROMPT, user_prompt)
        met, justification = _parse_judge_verdict(raw)
        return CriterionResult(
            criterion_id=criterion.criterion_id,
            met=met,
            detail=bound_text(justification, _RESULT_DETAIL_CHARS),
        )


class GoalEvaluator:
    """Evaluates every criterion of a goal, composing one strategy per kind.

    Never raises: an evaluator blowing up yields a not-met result carrying the
    error, so a broken verification command reads as "criterion not satisfied"
    and the loop keeps its normal escalation path instead of dying.
    """

    def __init__(
        self,
        *,
        command_port: CommandPort,
        judge_port: JudgePort,
        workspace: Path,
        judge_enabled: bool = True,
    ) -> None:
        self._evaluators = {
            CriterionKind.COMMAND: CommandCriterionEvaluator(command_port),
            CriterionKind.FILE: FileCriterionEvaluator(workspace),
            CriterionKind.JUDGE: JudgeCriterionEvaluator(
                judge_port, enabled=judge_enabled
            ),
        }

    async def evaluate(
        self, goal: Goal, context: EvaluationContext
    ) -> GoalEvaluationReport:
        results: list[CriterionResult] = []
        for criterion in goal.criteria:
            evaluator = self._evaluators[criterion.kind]
            try:
                results.append(await evaluator.evaluate(criterion, context))
            except Exception as exc:
                results.append(
                    CriterionResult(
                        criterion_id=criterion.criterion_id,
                        met=False,
                        detail=bound_text(
                            f"evaluation failed: {exc}", _RESULT_DETAIL_CHARS
                        ),
                    )
                )
        return GoalEvaluationReport(results=results)


def _parse_judge_verdict(raw: str) -> tuple[bool, str]:
    """Extract (met, justification) from a possibly chatty judge reply.

    Biased toward "not met": anything unparseable counts as a failed criterion,
    because a false negative costs one more iteration while a false positive
    breaks the command's core promise (stop only when the goal is truly done).
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return False, f"unparseable judge reply: {bound_text(text, 200)}"
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return False, f"unparseable judge reply: {bound_text(text, 200)}"
    if not isinstance(data, dict) or not isinstance(data.get("met"), bool):
        return False, f"judge reply missing a boolean 'met': {bound_text(text, 200)}"
    justification = str(data.get("justification") or "").strip()
    return data["met"], justification or ("met" if data["met"] else "not met")
