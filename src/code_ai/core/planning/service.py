from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.config.models import PlannerConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.core.planning.evidence import EvidenceLedger, EvidenceRecord
from code_ai.core.planning.models import (
    AgentPlan,
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
from code_ai.core.verification import (
    ProjectVerification,
    detect_project_verification,
    is_genuine_verification,
)
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
        workspace: Path | None = None,
        verification_detector: Callable[[Path], ProjectVerification] = detect_project_verification,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.session_id = session_id
        self._workspace = workspace
        self._verification_detector = verification_detector
        self._project_verification: ProjectVerification | None = None
        self.policy = PlannerToolPolicy()
        self.advisory = config.advisory_tool_policy
        self.mode = PlannerMode(config.mode)
        self.phase = PlanningPhase.UNDERSTAND
        self.profile: TaskProfile | None = None
        self.plan: ExecutionPlan | None = None
        # The model-authored checklist shown in the task sidebar. It stays None
        # until the model calls submit_plan with concrete steps, so the panel is
        # only shown once a real plan exists (never the deterministic skeleton).
        self.agent_plan: AgentPlan | None = None
        self.ledger = EvidenceLedger(session_id=session_id)
        self.approved_external_gaps: tuple[ApprovedExternalGap, ...] = ()
        self.approved_external_gap = False
        self.no_progress_rounds = 0
        self.double_check_pending = False
        self.accepted_final_text: str | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def project_verification(self) -> ProjectVerification:
        """The detected verification capabilities of the workspace, cached.

        Detection is lazy and best-effort: with no workspace, or none detected,
        this returns an empty ``ProjectVerification`` and the completion gate
        degrades gracefully instead of trapping the agent.
        """
        if self._project_verification is None:
            if self._workspace is None:
                self._project_verification = ProjectVerification()
            else:
                try:
                    self._project_verification = self._verification_detector(self._workspace)
                except Exception:
                    self._project_verification = ProjectVerification()
        return self._project_verification

    def _is_verification_command(self, argv: list[str]) -> bool:
        return is_genuine_verification(argv, self.project_verification())

    def _verification_context_line(self) -> str:
        """Tell the model the project's real verification command, when relevant.

        Surfaced once a mutation task is implementing/verifying so the model runs
        the project's own tests/build (not a trivial command) to prove the change
        works. Stays silent for non-mutation/research tasks.
        """
        if not (self.profile and self.profile.requires_workspace_mutation):
            return ""
        if self.phase not in {
            PlanningPhase.EXECUTE,
            PlanningPhase.VERIFY,
            PlanningPhase.REPAIR,
        }:
            return ""
        if not _changes_require_verification(self.ledger.current_changed_paths()):
            return ""
        return f"Verification: {self.project_verification().prompt_hint()}\n"

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

    async def begin_turn(
        self, text: str, *, provider_supports_tools: bool, resume: bool = False
    ) -> None:
        # Resuming keeps the plan authored in the previous (plan-mode) turn alive
        # so switching to act executes that checklist instead of reclassifying the
        # continuation text and wiping the model-authored steps.
        if resume and self.profile is not None:
            await self._resume_turn()
            return
        self.profile = TaskProfile.from_user_text(text)
        self.plan = ExecutionPlan.for_profile(
            self.profile, max_steps=self.config.max_plan_steps
        )
        self.agent_plan = None
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
        # The deterministic skeleton drives policy and completion gating, but it is
        # never surfaced as a plan: the sidebar is reserved for the model-authored
        # steps submitted via submit_plan. Advance the internal cursor silently.
        await self._mark_current_step_started()

    async def _resume_turn(self) -> None:
        """Continue the current plan into an execution turn without rebuilding it.

        Used when the user approves the plan and switches to act mode: the
        profile, deterministic skeleton, model-authored checklist and evidence
        ledger are all kept, so execution picks up where planning left off. Only
        the per-turn bookkeeping resets, and the existing plan snapshot is
        re-emitted so the task sidebar (collapsed when the plan-mode turn ended)
        reappears with live progress.
        """
        self.no_progress_rounds = 0
        self.double_check_pending = False
        self.accepted_final_text = None
        await self._emit_phase(PlanningPhase.EXECUTE)
        if self.agent_plan and self.agent_plan.status == PlanStatus.ACTIVE:
            await self.event_bus.emit(
                "planning.step.started",
                self.plan_snapshot(),
                source="core.planner",
            )

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
        if tool_name == "submit_plan":
            await self.submit_agent_plan(payload.get("steps"))
            return []
        if tool_name == "complete_plan_step":
            # The model owns its checklist cursor: it advances only when the model
            # declares a step finished, never by a heuristic that cannot know which
            # of the model's free-form steps a given piece of evidence belongs to.
            await self._advance_agent_plan()
            return []
        before = self.progress_signature()
        step_id = self.current_step.step_id if self.current_step else None
        records = self.ledger.record_tool_result(
            plan=self.plan,
            step_id=step_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload=payload,
            success=success,
            is_verification_command=self._is_verification_command,
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
        # loop would reset the guard forever. The model-authored checklist is *not*
        # advanced here: that cursor is driven solely by the model via
        # complete_plan_step, so the sidebar reflects real progress instead of
        # racing one step ahead per piece of evidence.
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
        # The model-authored plan is the source of truth for the step narrative the
        # model sees: it should work its *own* checklist, not the generic internal
        # skeleton. The skeleton's kind/required-evidence still tell the model what
        # evidence the runtime expects, but the step title and progress track the
        # model's plan whenever one has been submitted.
        if self.agent_plan is not None and self.agent_plan.current_step is not None:
            step_title = self.agent_plan.current_step.title
            plan_progress = self.agent_plan.snapshot()["progress"]
        else:
            step_title = current.title if current else "none"
            plan_progress = snapshot["progress"]
        if self.agent_plan is None:
            plan_lines = (
                "Task checklist: not submitted yet.\n"
                "FIRST ACTION: call submit_plan with the concrete ordered steps you "
                "will take for this task before any other tool call. The steps are "
                "shown to the user as the live checklist, so make them specific to "
                "this request.\n"
            )
        else:
            agent_current = self.agent_plan.current_step
            current_label = agent_current.title if agent_current else "done"
            plan_lines = (
                f"Task checklist: {self.agent_plan.snapshot()['progress']} "
                f"(current: {current_label}).\n"
                "This checklist position reflects only the steps you have reported "
                "finishing. When you actually complete the current step, call "
                "complete_plan_step so it advances to the next one. Call submit_plan "
                "again only if your approach genuinely changes.\n"
            )
        header = (
            "Runtime task state. Treat this as authoritative host state, not a user request.\n"
            f"Original objective: {self.profile.objective}\n"
            f"Acceptance criteria: {self.profile.acceptance_criteria}\n"
            f"Planner mode: {self.mode.value}\n"
            f"Semantic phase: {self.phase.value}\n"
            f"Plan revision: {self.plan.revision}\n"
            f"Plan progress: {plan_progress}\n"
            f"Current step: {step_title}\n"
            f"Current step kind: {current.kind.value if current else 'none'}\n"
            f"Required evidence: {required_evidence}\n"
            f"Changed paths: {self.ledger.current_changed_paths()}\n"
            f"Latest verification passed: {self.ledger.latest_verification_passed}\n"
            f"Approved external gaps: {[gap.to_dict() for gap in self.approved_external_gaps]}\n"
            f"Recent evidence: {self.ledger.compact_recent(limit=8)}\n"
            f"Recommended tools now: {sorted(recommended_tool_names)}\n"
            + self._verification_context_line()
            + plan_lines
        )
        if self.mode == PlannerMode.PLAN:
            # Plan mode = think, don't touch. The model investigates with read-only
            # tools and delivers a thorough plan as its answer; write/process tools
            # are denied by policy, so it must not attempt them or claim completion.
            return header + (
                "PLAN MODE — produce a plan, do not change anything.\n"
                "Rules:\n"
                "- Investigate first with read-only tools (read_file, search_code, "
                "list_files) until you genuinely understand the task and the code it "
                "touches. Do not guess.\n"
                "- Do NOT call write_file, edit_code, execute_command, or "
                "complete_task — they are disabled in plan mode and will be rejected.\n"
                "- Deliver a deep, concrete plan as your final answer: the approach "
                "and trade-offs, the exact files/functions to change with what each "
                "change does, edge cases and risks, and how the result will be "
                "verified. Number the steps so they can be executed in order.\n"
                "- Use ask_user only if a genuine ambiguity blocks planning.\n"
                "- End by telling the user to switch to act mode (/act) to execute "
                "the plan."
            )
        return header + (
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
                    "Confirm verification still reflects the current workspace state.",
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

        The model-authored checklist (``agent_plan``) is part of this token: when
        the model advances its own cursor via ``complete_plan_step`` that is real
        forward progress the user can see in the sidebar, so it must not be
        misread as a stall just because the internal skeleton stayed put.
        """
        agent_cursor: tuple[object, ...] = ()
        if self.agent_plan:
            agent_completed = sum(
                1
                for step in self.agent_plan.steps
                if step.status == PlanStepStatus.COMPLETED
            )
            agent_cursor = (
                self.agent_plan.status.value,
                self.agent_plan.current_index,
                agent_completed,
            )
        if not self.plan:
            return (self.phase.value, agent_cursor, self.ledger.progress_fingerprint())
        completed = sum(
            1 for step in self.plan.steps if step.status == PlanStepStatus.COMPLETED
        )
        return (
            self.phase.value,
            self.plan.status.value,
            self.plan.current_step_index,
            self.current_step.status.value if self.current_step else "",
            completed,
            agent_cursor,
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
        # Step-level fields (current_step, completed/remaining, progress, status)
        # come only from the model-authored plan, so the sidebar stays empty and
        # hidden until the model submits one. The deterministic skeleton remains
        # internal and never reaches the UI.
        data: dict[str, Any] = {
            "mode": self.mode.value,
            "phase": self.phase.value,
            "changed_paths": self.ledger.current_changed_paths(),
            "latest_verification_passed": self.ledger.latest_verification_passed,
            "approved_external_gaps": [
                gap.to_dict() for gap in self.approved_external_gaps
            ],
            "profile": self.profile.model_dump(mode="json") if self.profile else None,
        }
        if self.agent_plan:
            data.update(self.agent_plan.snapshot())
        return data

    async def submit_agent_plan(self, steps: object) -> None:
        """Adopt the model-authored steps and reveal the task sidebar.

        The first submission emits ``planning.plan.created`` (the panel appears);
        a later submission emits ``planning.plan.revised`` (the model corrected or
        re-scoped its plan). Steps with no usable title are ignored.
        """
        titles = _coerce_plan_step_titles(steps)
        if not titles:
            return
        first_time = self.agent_plan is None
        self.agent_plan = AgentPlan.from_titles(
            titles, max_steps=self.config.max_plan_steps
        )
        snapshot = self.plan_snapshot()
        await self.event_bus.emit(
            "planning.plan.created" if first_time else "planning.plan.revised",
            snapshot,
            source="core.planner",
        )
        await self.event_bus.emit(
            "planning.step.started",
            snapshot,
            source="core.planner",
        )

    async def _advance_agent_plan(self) -> None:
        """Move the sidebar cursor one step forward when the model reports a step done.

        Driven solely by the model's complete_plan_step calls, so the checklist
        tracks real declared progress rather than racing one step ahead per piece
        of evidence. The last step stays running until completion settles the
        whole plan, so the panel always shows a live step.
        """
        if not self.agent_plan or self.agent_plan.status != PlanStatus.ACTIVE:
            return
        if not self.agent_plan.advance():
            # advance() refuses the final step by design; remember that the model
            # declared it done so a clean final answer can settle the plan (see
            # settle_agent_plan_on_final_answer).
            self.agent_plan.final_step_declared = True
            return
        snapshot = self.plan_snapshot()
        await self.event_bus.emit(
            "planning.step.completed",
            snapshot,
            source="core.planner",
        )
        if self.agent_plan.current_step:
            await self.event_bus.emit(
                "planning.step.started",
                snapshot,
                source="core.planner",
            )

    def annotate_plan_step_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Make complete_plan_step's result honest when it cannot advance.

        The final checklist step stays running until the whole task settles, so
        a plain echo would tell the model its checklist is finished while the
        runtime still expects the task's actual conclusion. Without this note
        the model believes it is done, answers in prose, and never learns why
        the sidebar kept its last step spinning.
        """
        if self.agent_plan is None or not self.agent_plan.on_final_step:
            return payload
        return {
            **payload,
            "status": "final_step_still_running",
            "note": (
                "This is the plan's final step; it stays running until the task "
                "settles. Do not call complete_plan_step again - conclude the "
                "task now: call complete_task for workspace tasks, or deliver "
                "your final answer."
            ),
        }

    async def settle_agent_plan_on_final_answer(self) -> None:
        """Complete the checklist when a turn ends cleanly in a final answer.

        ``complete_all`` normally runs only when a ``complete_task`` claim is
        accepted, but a read-only task legitimately ends in a plain prose
        answer. When the model has already declared the final step done via
        ``complete_plan_step``, that answer *is* the final step's execution, so
        the plan settles instead of freezing the sidebar at N-1/N with the last
        step spinning forever.
        """
        plan = self.agent_plan
        if plan is None or plan.status != PlanStatus.ACTIVE:
            return
        if not plan.final_step_declared:
            return
        plan.complete_all()
        await self.event_bus.emit(
            "planning.plan.completed",
            self.plan_snapshot(),
            source="core.planner",
        )

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
            await self._settle_misclassified_research_step()
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

    async def _settle_misclassified_research_step(self) -> None:
        """Stop demanding web evidence when the task is actually a local edit.

        Graceful degradation for a misclassified profile: if the model performs a
        real workspace mutation while the skeleton still sits on a RESEARCH_WEB
        step, the local action is stronger evidence of intent than the upfront
        guess. Skip the unsatisfied research step and move to completion so a
        wrong classification can never trap the agent in a web_search loop. A
        genuine research task is preserved — once any web evidence or an approved
        external gap exists, this no-ops.
        """
        if not self.plan:
            return
        if self.approved_external_gaps or self.ledger.has_success(EvidenceType.WEB_RESULT):
            return
        research = next(
            (
                step
                for step in self.plan.steps
                if step.kind == PlanStepKind.RESEARCH_WEB
                and step.status != PlanStepStatus.COMPLETED
            ),
            None,
        )
        if research is None:
            return
        research.status = PlanStepStatus.SKIPPED
        self.plan.updated_at = utc_now_iso()
        await self._move_to_step_kind(PlanStepKind.COMPLETE, PlanningPhase.COMPLETE)

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

    async def _mark_current_step_failed(self, message: str) -> None:
        if not self.plan or not self.current_step:
            return
        self.current_step.status = PlanStepStatus.FAILED
        self.current_step.last_error = message
        self.plan.updated_at = utc_now_iso()

    async def _emit_phase(self, phase: PlanningPhase) -> None:
        self.phase = phase
        await self.event_bus.emit(
            "planning.phase.changed",
            {"phase": phase.value, "mode": self.mode.value},
            source="core.planner",
        )

    def _completion_verification_note(self, changed_paths: list[str]) -> str:
        if self.ledger.latest_verification_evidence_id:
            return f"Verified by evidence {self.ledger.latest_verification_evidence_id}."
        code_changed = _changes_require_verification(changed_paths)
        if code_changed and not self.project_verification().has_any:
            return (
                "Warning: no automated test/build system was detected in this "
                "project, so the change was not automatically verified."
            )
        return "No verification evidence was required."

    async def _accept_success_completion(self, claim: CompletionClaim) -> None:
        if self.plan and self.current_step and self.current_step.kind == PlanStepKind.COMPLETE:
            self.current_step.status = PlanStepStatus.COMPLETED
            self.plan.status = PlanStatus.COMPLETED
            self.plan.updated_at = utc_now_iso()
        if self.agent_plan:
            self.agent_plan.complete_all()
        changed_paths = self.ledger.current_changed_paths()
        verification = self._completion_verification_note(changed_paths)
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
        # The sidebar only re-renders on snapshot-carrying events; without this
        # it freezes on the last "running" step even though complete_all() has
        # already settled every step.
        if self.agent_plan:
            await self.event_bus.emit(
                "planning.plan.completed",
                self.plan_snapshot(),
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
        # A genuine "blocked"/"failed" outcome must never trap the agent: the model
        # has decided it cannot proceed, so we always accept it and surface its
        # summary. Prefer the structured remaining_issues/limitations when supplied;
        # otherwise fall back to the summary so the user still sees a final message
        # instead of the turn spinning to a budget/stall wind-down that discards it.
        if claim.outcome == "blocked" and not (claim.remaining_issues or claim.limitations):
            claim = claim.model_copy(update={"remaining_issues": [claim.summary]})
        terminal_status = (
            PlanStatus.BLOCKED if claim.outcome == "blocked" else PlanStatus.FAILED
        )
        if self.plan:
            self.plan.status = terminal_status
        if self.agent_plan:
            self.agent_plan.settle(terminal_status)
        await self._emit_phase(PlanningPhase.BLOCKED)
        self.accepted_final_text = claim.summary
        await self.event_bus.emit(
            "planning.completion.accepted",
            {"outcome": claim.outcome, "summary": claim.summary},
            source="core.planner",
        )
        # Same as the success path: push the settled snapshot so the sidebar
        # stops showing the interrupted step as still running.
        if self.agent_plan:
            await self.event_bus.emit(
                "planning.plan.blocked"
                if claim.outcome == "blocked"
                else "planning.plan.failed",
                self.plan_snapshot(),
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
        has_file_change = self.ledger.has_success(
            EvidenceType.FILE_CREATED, EvidenceType.FILE_CHANGED
        )
        # Verification only applies when (a) the change is not documentation-only
        # and (b) the project actually exposes a way to verify it. With no
        # detectable test/build system we degrade gracefully and complete with a
        # warning rather than trapping the agent demanding evidence it cannot get.
        verification_applies = (
            _changes_require_verification(self.ledger.current_changed_paths())
            and self.project_verification().has_any
        )
        verified = (
            not self.config.require_verification_for_changes
            or not verification_applies
            or self.ledger.latest_verification_passed
        )
        # A task the surface classifier labelled a mutation must show file-change
        # evidence before completing. Independently, *any* task that actually
        # changed files must be verified before completing — that catches mutations
        # the keyword classifier missed (e.g. "faça um jogo de pong", read as
        # conversation), so the gate keys off real evidence, not the label.
        if self.profile.requires_workspace_mutation and not has_file_change:
            missing.append("no successful file-change evidence exists.")
        if has_file_change and not verified:
            missing.append("no current successful verification evidence exists.")
        if has_file_change or self.profile.requires_workspace_mutation:
            actual_paths = set(self.ledger.current_changed_paths())
            claimed_paths = set(claim.changed_paths)
            if claimed_paths and claimed_paths != actual_paths:
                missing.append(
                    f"claimed changed paths {sorted(claimed_paths)} do not match "
                    f"recorded paths {sorted(actual_paths)}."
                )
        missing.extend(
            self._incomplete_plan_steps(has_file_change=has_file_change, verified=verified)
        )
        return missing

    def _incomplete_plan_steps(self, *, has_file_change: bool, verified: bool) -> list[str]:
        """Steps still owed before a clean completion.

        Reconcile against the model's *own* checklist (``AgentPlan``) when it
        submitted one, so completion judges what the model said it would do rather
        than the generic internal skeleton. The model drives its own cursor (via
        complete_plan_step) and may forget to advance it, so once a mutation's
        change is verified we trust the evidence and stop blocking on a lagging
        cursor (fail-soft). With no submitted plan we fall back to the
        deterministic skeleton.
        """
        # Once a mutation's change is settled (file changed and verification either
        # passed or does not apply, e.g. a documentation-only edit) we trust the
        # evidence and stop blocking on a lagging checklist cursor — for both the
        # model's plan and the internal skeleton.
        mutation_settled = bool(
            self.profile
            and self.profile.requires_workspace_mutation
            and has_file_change
            and verified
        )
        if mutation_settled:
            return []
        if self.agent_plan is not None:
            pending = [
                step.title
                for step in self.agent_plan.steps
                if step.status == PlanStepStatus.PENDING
            ]
            return [f"declared plan steps not yet done: {pending}."] if pending else []
        if not self.plan:
            return []
        incomplete = [
            step.title
            for step in self.plan.steps
            if step.kind != PlanStepKind.COMPLETE
            and step.status not in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
        ]
        return [f"required plan steps are incomplete: {incomplete}."] if incomplete else []

    def current_step_index_is_last(self) -> bool:
        return bool(self.plan and self.plan.current_step_index >= len(self.plan.steps) - 1)


# File suffixes whose changes carry no executable behaviour, so there is nothing
# meaningful to verify (no test/command applies). Completion of a change that
# touches only these must not be blocked on verification evidence.
_DOC_ONLY_SUFFIXES = frozenset(
    {
        ".md",
        ".markdown",
        ".mdx",
        ".rst",
        ".adoc",
        ".txt",
        ".text",
    }
)


def _changes_require_verification(paths: list[str]) -> bool:
    """Whether a set of changed paths warrants verification evidence.

    Pure documentation/prose edits (e.g. a ``.md`` progress tracker) have nothing
    to run or assert against, so they do not require verification. Any path that
    is not clearly documentation keeps the gate strict — a mixed change still
    needs verification.
    """
    if not paths:
        return False
    return any(not _is_doc_only_path(path) for path in paths)


def _is_doc_only_path(path: str) -> bool:
    dot = path.rfind(".")
    slash = max(path.rfind("/"), path.rfind("\\"))
    if dot <= slash:  # no suffix (or a dotfile with no extension)
        return False
    return path[dot:].lower() in _DOC_ONLY_SUFFIXES


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


def _coerce_plan_step_titles(steps: object) -> list[str]:
    """Extract ordered step titles from a submit_plan payload.

    Accepts a list of plain strings or of objects carrying ``title``/``step``/
    ``description``, so weaker models that wrap each step in an object still
    produce a usable plan. Empty or untitled entries are dropped.
    """
    if not isinstance(steps, list):
        return []
    titles: list[str] = []
    for item in steps:
        if isinstance(item, str):
            title = item.strip()
        elif isinstance(item, dict):
            raw = item.get("title") or item.get("step") or item.get("description")
            title = str(raw).strip() if raw is not None else ""
        else:
            title = ""
        if title:
            titles.append(title)
    return titles


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
