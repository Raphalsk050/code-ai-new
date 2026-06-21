from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from code_ai.config.models import PlannerConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.core.planning.evidence import EvidenceLedger, EvidenceRecord
from code_ai.core.planning.models import (
    CompletionClaim,
    EvidenceType,
    ExecutionPlan,
    PlannerMode,
    PlanningPhase,
    PlanStatus,
    PlanStep,
    PlanStepKind,
    PlanStepStatus,
    TaskIntent,
    TaskProfile,
)
from code_ai.core.planning.policy import PlannerToolPolicy, PolicyDecision
from code_ai.events.bus import AsyncEventBus
from code_ai.events.models import utc_now_iso
from code_ai.tools.output import bound_text
from code_ai.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    accepted: bool
    outcome: str
    final_text: str = ""
    missing_requirements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovedExternalGap:
    question: str
    why_local_files_are_insufficient: str
    decision_depends_on: str

    def to_dict(self) -> dict[str, str]:
        return {
            "question": self.question,
            "why_local_files_are_insufficient": self.why_local_files_are_insufficient,
            "decision_depends_on": self.decision_depends_on,
        }


class PlannerService:
    """Owns semantic task progress and evidence-based completion decisions."""

    def __init__(
        self,
        *,
        config: PlannerConfig,
        event_bus: AsyncEventBus,
        session_id: str,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.session_id = session_id
        self.policy = PlannerToolPolicy()
        self.advisory = config.advisory_tool_policy
        self.mode = PlannerMode(config.mode)
        self.phase = PlanningPhase.UNDERSTAND
        self.profile: TaskProfile | None = None
        self.plan: ExecutionPlan | None = None
        self.ledger = EvidenceLedger(session_id=session_id)
        self.approved_external_gaps: tuple[ApprovedExternalGap, ...] = ()
        self.approved_external_gap = False
        self.no_progress_rounds = 0
        self.double_check_pending = False
        self.accepted_final_text: str | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def current_step(self) -> PlanStep | None:
        return self.plan.current_step if self.plan else None

    async def set_mode(self, mode: PlannerMode | str) -> None:
        self.mode = PlannerMode(mode)
        await self.event_bus.emit(
            "planning.mode.changed",
            {"mode": self.mode.value},
            source="core.planner",
        )

    async def begin_turn(self, text: str, *, provider_supports_tools: bool) -> None:
        self.profile = TaskProfile.from_user_text(text)
        self.plan = ExecutionPlan.for_profile(
            self.profile, max_steps=self.config.max_plan_steps
        )
        self.ledger = EvidenceLedger(session_id=self.session_id)
        self.approved_external_gaps = ()
        self.approved_external_gap = self.profile.allows_web_first
        self.no_progress_rounds = 0
        self.double_check_pending = False
        self.accepted_final_text = None
        if self.profile.requires_workspace_mutation and not provider_supports_tools:
            raise ToolExecutionError(
                "This implementation task requires tool-calling support; chat text cannot "
                "modify the workspace."
            )

        await self._emit_phase(
            PlanningPhase.DISCOVER_LOCAL
            if self.profile.requires_local_context
            else PlanningPhase.EXECUTE
        )
        await self.event_bus.emit(
            "planning.task.classified",
            self.profile.model_dump(mode="json"),
            source="core.planner",
        )
        await self.event_bus.emit(
            "planning.plan.created",
            self.plan_snapshot(),
            source="core.planner",
        )
        await self._mark_current_step_started()

    def should_auto_list_workspace(self) -> bool:
        return bool(
            self.enabled
            and self.config.local_first
            and self.profile
            and self.profile.requires_local_context
            and self.phase == PlanningPhase.DISCOVER_LOCAL
        )

    def allowed_tool_names(self, registry: ToolRegistry) -> set[str]:
        return self.policy.allowed_tool_names(
            registry=registry,
            profile=self.profile,
            mode=self.mode,
            phase=self.phase,
            current_step=self.current_step,
            approved_external_gap=self.approved_external_gap,
            advisory=self.advisory,
        )

    def recommended_tool_names(self, registry: ToolRegistry) -> set[str]:
        """Focused set the model is steered toward, regardless of policy mode.

        In advisory mode every tool stays callable; this is only guidance shown
        in the task context block.
        """
        return self.policy.allowed_tool_names(
            registry=registry,
            profile=self.profile,
            mode=self.mode,
            phase=self.phase,
            current_step=self.current_step,
            approved_external_gap=self.approved_external_gap,
            advisory=False,
        )

    def evaluate_tool(self, tool_name: str, registry: ToolRegistry) -> PolicyDecision:
        return self.policy.evaluate(
            tool_name=tool_name,
            registry=registry,
            profile=self.profile,
            mode=self.mode,
            phase=self.phase,
            current_step=self.current_step,
            approved_external_gap=self.approved_external_gap,
            advisory=self.advisory,
        )

    async def record_tool_result(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        payload: dict[str, Any],
        success: bool,
    ) -> list[EvidenceRecord]:
        before = self.progress_signature()
        step_id = self.current_step.step_id if self.current_step else None
        records = self.ledger.record_tool_result(
            plan=self.plan,
            step_id=step_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload=payload,
            success=success,
        )
        for record in records:
            await self.event_bus.emit(
                "planning.evidence.recorded",
                record.compact(),
                source="core.planner",
            )
        await self._advance_after_evidence(records, tool_name=tool_name, payload=payload)
        # Only genuine forward motion clears the no-progress counter. Recording yet
        # another identical observation must not look like progress, or a tool-call
        # loop would reset the guard forever.
        if self.progress_signature() != before:
            self.no_progress_rounds = 0
        return records

    async def record_policy_denial(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        reason: str,
        allowed_tool_names: set[str],
    ) -> None:
        step_id = self.current_step.step_id if self.current_step else None
        record = self.ledger.record_policy_denial(
            plan=self.plan,
            step_id=step_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            reason=reason,
        )
        await self.event_bus.emit(
            "planning.policy.denied",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "reason": reason,
                "allowed_tools": sorted(allowed_tool_names),
            },
            source="core.planner",
        )
        await self.event_bus.emit(
            "planning.evidence.recorded",
            record.compact(),
            source="core.planner",
        )

    def requires_tool_for_progress(self) -> bool:
        if not (self.enabled and self.profile and self.profile.requires_workspace_mutation):
            return False
        if self.mode == PlannerMode.PLAN:
            return False
        return self.phase in {
            PlanningPhase.EXECUTE,
            PlanningPhase.VERIFY,
            PlanningPhase.REPAIR,
            PlanningPhase.COMPLETE,
        }

    def task_context_block(self, *, recommended_tool_names: set[str]) -> str:
        if not (self.enabled and self.profile and self.plan):
            return ""
        if self.profile.intent == TaskIntent.CONVERSATION:
            return ""
        current = self.current_step
        snapshot = self.plan.snapshot()
        required_evidence = [
            item.value for item in current.required_evidence
        ] if current else []
        return (
            "Runtime task state. Treat this as authoritative host state, not a user request.\n"
            f"Original objective: {self.profile.objective}\n"
            f"Acceptance criteria: {self.profile.acceptance_criteria}\n"
            f"Planner mode: {self.mode.value}\n"
            f"Semantic phase: {self.phase.value}\n"
            f"Plan revision: {self.plan.revision}\n"
            f"Plan progress: {snapshot['progress']}\n"
            f"Current step: {current.title if current else 'none'}\n"
            f"Current step kind: {current.kind.value if current else 'none'}\n"
            f"Required evidence: {required_evidence}\n"
            f"Changed paths: {self.ledger.current_changed_paths()}\n"
            f"Latest verification passed: {self.ledger.latest_verification_passed}\n"
            f"Approved external gaps: {[gap.to_dict() for gap in self.approved_external_gaps]}\n"
            f"Recent evidence: {self.ledger.compact_recent(limit=8)}\n"
            f"Recommended tools now: {sorted(recommended_tool_names)}\n"
            "Rules: prefer the recommended tools, work on the current step, and do not "
            "claim completion from prose. For workspace changes, call write_file or "
            "edit_code; for completion, call complete_task after verification evidence exists."
        )

    def corrective_message(self, *, recommended_tool_names: set[str]) -> str:
        current = self.current_step
        required_evidence = [
            item.value for item in current.required_evidence
        ] if current else []
        return (
            "Runtime correction: this task requires workspace evidence. Do not provide "
            "the implementation, diff, or command as chat text. Use the recommended tools "
            "to satisfy the current step, then verify the result. "
            f"Phase: {self.phase.value}. "
            f"Current step: {current.title if current else 'none'}. "
            f"Recommended tools: {sorted(recommended_tool_names)}. "
            f"Required evidence: {required_evidence}."
        )

    async def note_no_tool_response(self, *, recommended_tool_names: set[str]) -> str:
        self.no_progress_rounds += 1
        await self.event_bus.emit(
            "agent.corrective_prompt.injected",
            {
                "phase": self.phase.value,
                "current_step": self.current_step.title if self.current_step else None,
                "recommended_tools": sorted(recommended_tool_names),
                "no_progress_rounds": self.no_progress_rounds,
            },
            source="core.planner",
        )
        if self.no_progress_rounds >= self.config.max_no_progress_rounds:
            await self.event_bus.emit(
                "planning.no_progress.detected",
                {
                    "rounds": self.no_progress_rounds,
                    "phase": self.phase.value,
                    "current_step": self.current_step.title if self.current_step else None,
                },
                source="core.planner",
            )
        if self.no_progress_rounds > self.config.max_no_progress_rounds:
            await self._emit_phase(PlanningPhase.BLOCKED)
        return self.corrective_message(recommended_tool_names=recommended_tool_names)

    async def evaluate_completion(self, arguments: dict[str, Any]) -> CompletionDecision:
        try:
            claim = CompletionClaim.model_validate(arguments)
        except Exception as exc:
            return CompletionDecision(
                accepted=False,
                outcome="failed",
                missing_requirements=(f"Invalid complete_task payload: {exc}",),
            )

        await self.event_bus.emit(
            "planning.completion.requested",
            claim.model_dump(mode="json"),
            source="core.planner",
        )
        if claim.outcome in {"blocked", "failed"}:
            return await self._accept_non_success_completion(claim)

        missing = self._successful_completion_missing_requirements(claim)
        if missing:
            await self.event_bus.emit(
                "planning.completion.rejected",
                {"missing_requirements": list(missing)},
                source="core.planner",
            )
            return CompletionDecision(
                accepted=False,
                outcome="success",
                missing_requirements=tuple(missing),
            )

        if (
            self.config.double_check_completion
            and self.profile
            and self.profile.requires_workspace_mutation
        ):
            if not self.double_check_pending:
                self.double_check_pending = True
                checklist = (
                    "Double-check required before successful completion.",
                    "Reconcile every acceptance criterion with actual evidence.",
                    "Confirm verification still applies to the current changed hashes.",
                    "Call complete_task again after reconciling the evidence.",
                )
                await self.event_bus.emit(
                    "planning.completion.rejected",
                    {"missing_requirements": list(checklist)},
                    source="core.planner",
                )
                return CompletionDecision(
                    accepted=False,
                    outcome="success",
                    missing_requirements=checklist,
                )

        await self._accept_success_completion(claim)
        return CompletionDecision(
            accepted=True,
            outcome="success",
            final_text=self.accepted_final_text or claim.summary,
        )

    def progress_signature(self) -> tuple[object, ...]:
        """Opaque token capturing semantic forward progress within a turn.

        It changes only when the task genuinely moves forward — a phase or step
        transition, a step completion, or new knowledge/state in the evidence
        ledger — and stays constant when the model merely repeats observations.
        The orchestrator compares it across model steps to detect stalled loops.
        """
        if not self.plan:
            return (self.phase.value, self.ledger.progress_fingerprint())
        completed = sum(
            1 for step in self.plan.steps if step.status == PlanStepStatus.COMPLETED
        )
        return (
            self.phase.value,
            self.plan.status.value,
            self.plan.current_step_index,
            self.current_step.status.value if self.current_step else "",
            completed,
            self.ledger.progress_fingerprint(),
        )

    def best_effort_summary(self) -> str:
        """A short evidence-backed summary for a turn that ends without a clean
        ``complete_task`` (e.g. a stalled loop or an exhausted budget)."""
        if not (self.enabled and self.plan and self.profile):
            return ""
        if self.profile.intent == TaskIntent.CONVERSATION:
            return ""
        changed = self.ledger.current_changed_paths()
        parts: list[str] = []
        if changed:
            parts.append(f"Changed paths: {', '.join(changed)}.")
        if self.profile.requires_workspace_mutation:
            parts.append(
                "Verification passed."
                if self.ledger.latest_verification_passed
                else "Verification was not confirmed."
            )
        if not parts:
            return ""
        return bound_text(
            "Stopped before a clean completion. Work so far is preserved.\n"
            + "\n".join(parts),
            2000,
        )

    def plan_snapshot(self) -> dict[str, Any]:
        if not self.plan:
            return {
                "mode": self.mode.value,
                "phase": self.phase.value,
                "profile": self.profile.model_dump(mode="json") if self.profile else None,
            }
        data = self.plan.snapshot()
        data.update(
            {
                "mode": self.mode.value,
                "phase": self.phase.value,
                "changed_paths": self.ledger.current_changed_paths(),
                "latest_verification_passed": self.ledger.latest_verification_passed,
                "approved_external_gaps": [
                    gap.to_dict() for gap in self.approved_external_gaps
                ],
                "profile": self.profile.model_dump(mode="json") if self.profile else None,
            }
        )
        return data

    async def _advance_after_evidence(
        self,
        records: list[EvidenceRecord],
        *,
        tool_name: str,
        payload: dict[str, Any],
    ) -> None:
        if not (self.enabled and self.profile and self.plan) or not records:
            return
        evidence_types = {record.evidence_type for record in records if record.success}
        if EvidenceType.DISCOVERY_COMPLETED in evidence_types:
            await self._review_external_gap_requests(payload.get("external_knowledge_gaps"))
            await self._complete_step_if_kind(PlanStepKind.INSPECT_LOCAL)
        if EvidenceType.WORKSPACE_LISTED in evidence_types and self.current_step:
            if self.current_step.kind == PlanStepKind.INSPECT_LOCAL:
                await self._complete_step_if_kind(PlanStepKind.INSPECT_LOCAL)
        if evidence_types.intersection({EvidenceType.FILE_CREATED, EvidenceType.FILE_CHANGED}):
            if self.phase in {PlanningPhase.EXECUTE, PlanningPhase.REPAIR}:
                await self._complete_step_if_kind(PlanStepKind.IMPLEMENT)
                await self._move_to_step_kind(PlanStepKind.VERIFY, PlanningPhase.VERIFY)
        if EvidenceType.VERIFICATION_FAILED in evidence_types:
            await self._mark_current_step_failed("Verification failed.")
            await self._move_to_step_kind(PlanStepKind.IMPLEMENT, PlanningPhase.REPAIR)
        elif (
            EvidenceType.VERIFICATION_PASSED in evidence_types
            and self.phase in {PlanningPhase.VERIFY, PlanningPhase.REPAIR}
        ):
            # A passing check resolves the work regardless of whether we reached it
            # straight from EXECUTE or after a REPAIR loop. Settle the implement and
            # verify steps so completion is not blocked by a step left in-progress.
            await self._complete_step_by_kind(PlanStepKind.IMPLEMENT)
            await self._complete_step_by_kind(PlanStepKind.VERIFY)
            await self._move_to_step_kind(PlanStepKind.COMPLETE, PlanningPhase.COMPLETE)
        if tool_name == "web_search" and self.current_step:
            if self.current_step.kind == PlanStepKind.RESEARCH_WEB:
                await self._complete_step_if_kind(PlanStepKind.RESEARCH_WEB)

    async def _review_external_gap_requests(self, gaps: object) -> None:
        requested = _coerce_external_gap_requests(gaps)
        approved = tuple(gap for gap in requested if self._external_gap_is_valid(gap))
        self.approved_external_gaps = approved
        self.approved_external_gap = bool(
            self.profile and (self.profile.allows_web_first or approved)
        )
        event_type = (
            "planning.external_gap.approved"
            if approved
            else "planning.external_gap.rejected"
        )
        await self.event_bus.emit(
            event_type,
            {
                "requested_count": len(requested),
                "approved_gaps": [gap.to_dict() for gap in approved],
                "requires_external_information": self.profile.requires_external_information
                if self.profile
                else False,
                "requires_local_context": self.profile.requires_local_context
                if self.profile
                else False,
            },
            source="core.planner",
        )

    def _external_gap_is_valid(self, gap: ApprovedExternalGap) -> bool:
        if not (self.profile and _is_concrete_external_gap(gap)):
            return False
        if self.profile.allows_web_first:
            return True
        if not self.profile.requires_local_context:
            return self.profile.requires_external_information
        if self.profile.requires_external_information:
            return True
        return _gap_is_supported_by_local_discovery(gap) and self.ledger.has_success(
            EvidenceType.FILE_READ,
            EvidenceType.LOCAL_SEARCH_MATCH,
            EvidenceType.LOCAL_SEARCH_COMPLETED,
        )

    async def _complete_step_if_kind(self, kind: PlanStepKind) -> None:
        if not self.plan or self.current_step is None or self.current_step.kind != kind:
            return
        self.current_step.status = PlanStepStatus.COMPLETED
        self.plan.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "planning.step.completed",
            self.plan_snapshot(),
            source="core.planner",
        )
        if self.current_step_index_is_last():
            return
        self.plan.current_step_index += 1
        await self._mark_current_step_started()
        next_kind = self.current_step.kind
        if next_kind == PlanStepKind.IMPLEMENT and self.mode == PlannerMode.AUTO:
            await self._emit_phase(PlanningPhase.EXECUTE)
        elif next_kind == PlanStepKind.RESEARCH_WEB:
            await self._emit_phase(PlanningPhase.EXECUTE)
        elif next_kind == PlanStepKind.VERIFY:
            await self._emit_phase(PlanningPhase.VERIFY)
        elif next_kind == PlanStepKind.COMPLETE:
            await self._emit_phase(PlanningPhase.COMPLETE)

    async def _complete_step_by_kind(self, kind: PlanStepKind) -> None:
        """Mark the first not-yet-completed step of ``kind`` as completed in place,
        without moving the cursor. Used when evidence settles a step other than the
        current one (e.g. verification passing while the cursor sits on repair)."""
        if not self.plan:
            return
        for step in self.plan.steps:
            if step.kind == kind and step.status != PlanStepStatus.COMPLETED:
                step.status = PlanStepStatus.COMPLETED
                step.last_error = None
                self.plan.updated_at = utc_now_iso()
                await self.event_bus.emit(
                    "planning.step.completed",
                    self.plan_snapshot(),
                    source="core.planner",
                )
                return

    async def _move_to_step_kind(self, kind: PlanStepKind, phase: PlanningPhase) -> None:
        if not self.plan:
            return
        for index, step in enumerate(self.plan.steps):
            if step.kind == kind:
                self.plan.current_step_index = index
                if step.status in {PlanStepStatus.COMPLETED, PlanStepStatus.FAILED}:
                    step.status = PlanStepStatus.PENDING
                await self._mark_current_step_started()
                await self._emit_phase(phase)
                return

    async def _mark_current_step_started(self) -> None:
        if not self.plan or not self.current_step:
            return
        if self.current_step.status == PlanStepStatus.PENDING:
            self.current_step.status = PlanStepStatus.IN_PROGRESS
        self.current_step.attempt_count += 1
        self.plan.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "planning.step.started",
            self.plan_snapshot(),
            source="core.planner",
        )

    async def _mark_current_step_failed(self, message: str) -> None:
        if not self.plan or not self.current_step:
            return
        self.current_step.status = PlanStepStatus.FAILED
        self.current_step.last_error = message
        self.plan.updated_at = utc_now_iso()
        await self.event_bus.emit(
            "planning.step.failed",
            self.plan_snapshot(),
            source="core.planner",
        )

    async def _emit_phase(self, phase: PlanningPhase) -> None:
        self.phase = phase
        await self.event_bus.emit(
            "planning.phase.changed",
            {"phase": phase.value, "mode": self.mode.value},
            source="core.planner",
        )

    async def _accept_success_completion(self, claim: CompletionClaim) -> None:
        if self.plan and self.current_step and self.current_step.kind == PlanStepKind.COMPLETE:
            self.current_step.status = PlanStepStatus.COMPLETED
            self.plan.status = PlanStatus.COMPLETED
            self.plan.updated_at = utc_now_iso()
        changed_paths = self.ledger.current_changed_paths()
        verification = (
            f"Verified by evidence {self.ledger.latest_verification_evidence_id}."
            if self.ledger.latest_verification_evidence_id
            else "No verification evidence was required."
        )
        self.accepted_final_text = bound_text(
            "\n".join(
                item
                for item in (
                    claim.summary,
                    f"Changed paths: {', '.join(changed_paths) if changed_paths else 'none'}",
                    verification,
                )
                if item
            ),
            4000,
        )
        await self.event_bus.emit(
            "planning.completion.accepted",
            {
                "outcome": "success",
                "changed_paths": changed_paths,
                "verification_evidence_id": self.ledger.latest_verification_evidence_id,
            },
            source="core.planner",
        )
        await self.event_bus.emit(
            "assistant.final",
            {"text": self.accepted_final_text},
            source="core.planner",
        )

    async def _accept_non_success_completion(
        self, claim: CompletionClaim
    ) -> CompletionDecision:
        if claim.outcome == "blocked" and not (claim.remaining_issues or claim.limitations):
            return CompletionDecision(
                accepted=False,
                outcome=claim.outcome,
                missing_requirements=(
                    "Blocked completion requires remaining issues or limitations.",
                ),
            )
        if self.plan:
            self.plan.status = (
                PlanStatus.BLOCKED if claim.outcome == "blocked" else PlanStatus.FAILED
            )
        await self._emit_phase(PlanningPhase.BLOCKED)
        self.accepted_final_text = claim.summary
        await self.event_bus.emit(
            "planning.completion.accepted",
            {"outcome": claim.outcome, "summary": claim.summary},
            source="core.planner",
        )
        await self.event_bus.emit(
            "assistant.final",
            {"text": claim.summary},
            source="core.planner",
        )
        return CompletionDecision(True, claim.outcome, final_text=claim.summary)

    def _successful_completion_missing_requirements(
        self, claim: CompletionClaim
    ) -> list[str]:
        missing: list[str] = []
        if not (self.profile and self.plan):
            return ["No active plan exists."]
        if claim.summary.strip() == "":
            missing.append("summary is required.")
        if self.plan.objective != self.profile.objective:
            missing.append("plan objective no longer matches the original objective.")
        incomplete = [
            step.title
            for step in self.plan.steps
            if step.kind != PlanStepKind.COMPLETE
            and step.status
            not in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
        ]
        if incomplete:
            missing.append(f"required plan steps are incomplete: {incomplete}.")
        if self.profile.requires_workspace_mutation:
            if not self.ledger.has_success(EvidenceType.FILE_CREATED, EvidenceType.FILE_CHANGED):
                missing.append("no successful file-change evidence exists.")
            if (
                self.config.require_verification_for_changes
                and not self.ledger.latest_verification_passed
            ):
                missing.append("no current successful verification evidence exists.")
            actual_paths = set(self.ledger.current_changed_paths())
            claimed_paths = set(claim.changed_paths)
            if claimed_paths and claimed_paths != actual_paths:
                missing.append(
                    f"claimed changed paths {sorted(claimed_paths)} do not match "
                    f"recorded paths {sorted(actual_paths)}."
                )
        return missing

    def current_step_index_is_last(self) -> bool:
        return bool(self.plan and self.plan.current_step_index >= len(self.plan.steps) - 1)


_GENERIC_EXTERNAL_GAP_PHRASES = {
    "external evidence",
    "external information",
    "informacao externa",
    "informacoes externas",
    "local files are insufficient",
    "need web search",
    "preciso pesquisar",
    "precisa pesquisar",
    "search the web",
}

_EXTERNAL_DECISION_MARKERS = {
    "api",
    "advisory",
    "changelog",
    "cve",
    "dependency",
    "docs",
    "documentation",
    "framework",
    "library",
    "license",
    "package",
    "pricing",
    "regulation",
    "release",
    "sdk",
    "security",
    "standard",
    "version",
    "versao",
}


def _coerce_external_gap_requests(gaps: object) -> tuple[ApprovedExternalGap, ...]:
    if not isinstance(gaps, list):
        return ()
    approved: list[ApprovedExternalGap] = []
    for item in gaps:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        why = str(item.get("why_local_files_are_insufficient") or "").strip()
        depends_on = str(item.get("decision_depends_on") or "").strip()
        if question and why and depends_on:
            approved.append(
                ApprovedExternalGap(
                    question=question,
                    why_local_files_are_insufficient=why,
                    decision_depends_on=depends_on,
                )
            )
    return tuple(approved)


def _is_concrete_external_gap(gap: ApprovedExternalGap) -> bool:
    fields = (
        gap.question,
        gap.why_local_files_are_insufficient,
        gap.decision_depends_on,
    )
    if any(len(value.strip()) < 12 for value in fields):
        return False
    text = _normalized_gap_text(gap)
    if len([token for token in text.split(" ") if len(token) >= 4]) < 8:
        return False
    if _contains_phrase(text, _GENERIC_EXTERNAL_GAP_PHRASES) and not _contains_phrase(
        text, _EXTERNAL_DECISION_MARKERS
    ):
        return False
    return True


def _gap_is_supported_by_local_discovery(gap: ApprovedExternalGap) -> bool:
    return _contains_phrase(_normalized_gap_text(gap), _EXTERNAL_DECISION_MARKERS)


def _normalized_gap_text(gap: ApprovedExternalGap) -> str:
    return " ".join(
        (
            gap.question,
            gap.why_local_files_are_insufficient,
            gap.decision_depends_on,
        )
    ).casefold()


def _contains_phrase(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)
