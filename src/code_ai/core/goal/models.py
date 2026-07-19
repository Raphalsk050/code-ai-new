from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from code_ai.events.models import utc_now_iso


class GoalStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    BLOCKED = "BLOCKED"
    EXHAUSTED = "EXHAUSTED"
    STOPPED = "STOPPED"


# Statuses a goal can never leave. STOPPED/SATISFIED/EXHAUSTED are final;
# BLOCKED is *not* here because the user can resume a blocked goal.
TERMINAL_GOAL_STATUSES = frozenset(
    {GoalStatus.SATISFIED, GoalStatus.EXHAUSTED, GoalStatus.STOPPED}
)


class CriterionKind(StrEnum):
    # Deterministic: a shell command must exit 0 (e.g. the project's test suite).
    COMMAND = "COMMAND"
    # Deterministic: a workspace path must exist (optionally containing a pattern).
    FILE = "FILE"
    # Subjective: a one-off model call judges the criterion against the evidence.
    JUDGE = "JUDGE"


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    kind: CriterionKind
    description: str
    # COMMAND: the shell command that must exit 0.
    command: str = ""
    # FILE: workspace-relative (or absolute) path that must exist, and an
    # optional plain-text pattern its content must contain.
    path: str = ""
    pattern: str = ""

    @field_validator("description")
    @classmethod
    def _description_must_not_be_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("criterion description must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _validate_kind_payload(self) -> AcceptanceCriterion:
        if self.kind == CriterionKind.COMMAND and not self.command.strip():
            raise ValueError("COMMAND criteria require a non-empty command")
        if self.kind == CriterionKind.FILE and not self.path.strip():
            raise ValueError("FILE criteria require a non-empty path")
        return self

    def label(self) -> str:
        target = {
            CriterionKind.COMMAND: self.command,
            CriterionKind.FILE: self.path,
            CriterionKind.JUDGE: "",
        }[self.kind]
        suffix = f" [{target}]" if target else ""
        return f"({self.kind.value.lower()}) {self.description}{suffix}"


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    met: bool
    detail: str = ""


class GoalEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[CriterionResult] = Field(default_factory=list)
    evaluated_at: str = Field(default_factory=utc_now_iso)

    @property
    def all_met(self) -> bool:
        return bool(self.results) and all(result.met for result in self.results)

    def unmet(self) -> list[CriterionResult]:
        return [result for result in self.results if not result.met]

    def failure_signature(self) -> tuple[str, ...]:
        """Stable token of *which* criteria failed, for no-progress detection.

        Details are excluded on purpose: a judge re-wording its justification is
        not progress, so two evaluations failing the same criteria must compare
        equal.
        """
        return tuple(sorted(result.criterion_id for result in self.unmet()))


class GoalIterationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    prompt: str
    # Marker of what the iteration's turn actually changed (changed paths +
    # verification flag), supplied by the runner from the planner snapshot.
    # Combined with the failure signature it detects spinning without progress.
    progress_marker: str = ""
    turn_error: str = ""
    wind_down_reason: str = ""
    cancelled: bool = False
    report: GoalEvaluationReport | None = None
    recorded_at: str = Field(default_factory=utc_now_iso)

    def stagnation_signature(self) -> tuple[object, ...] | None:
        """What "no progress" looks like for this iteration, or None when the
        iteration cannot participate in stagnation detection (no evaluation ran)."""
        if self.report is None:
            return None
        return (self.report.failure_signature(), self.progress_marker)


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(default_factory=lambda: str(uuid4()))
    objective: str
    status: GoalStatus = GoalStatus.DRAFT
    criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    iterations: list[GoalIterationRecord] = Field(default_factory=list)
    stop_reason: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("objective")
    @classmethod
    def _objective_must_not_be_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("goal objective must be non-empty")
        return normalized

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_GOAL_STATUSES

    def latest_report(self) -> GoalEvaluationReport | None:
        for record in reversed(self.iterations):
            if record.report is not None:
                return record.report
        return None

    def snapshot(self) -> dict[str, object]:
        latest = self.latest_report()
        met_by_id = {result.criterion_id: result for result in latest.results} if latest else {}
        criteria = []
        for criterion in self.criteria:
            result = met_by_id.get(criterion.criterion_id)
            criteria.append(
                {
                    "criterion_id": criterion.criterion_id,
                    "kind": criterion.kind.value,
                    "description": criterion.description,
                    "label": criterion.label(),
                    "met": result.met if result else None,
                    "detail": result.detail if result else "",
                }
            )
        met_count = sum(1 for item in criteria if item["met"])
        return {
            "goal_id": self.goal_id,
            "objective": self.objective,
            "status": self.status.value,
            "criteria": criteria,
            "criteria_progress": f"{met_count}/{len(criteria)}",
            "iterations": len(self.iterations),
            "stop_reason": self.stop_reason,
        }
