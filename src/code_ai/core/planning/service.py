from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.config.models import PlannerConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.core.planning.completion import (
    CompletionContext,
    CompletionGate,
    changes_require_verification,
)
from code_ai.core.planning.evidence import EvidenceLedger, EvidenceRecord
from code_ai.core.planning.models import (
    CRITERION_APPLY_VIA_TOOLS,
    CRITERION_VERIFY_AFTER_MUTATION,
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
    is_continuation_request,
)
from code_ai.core.planning.policy import PlannerToolPolicy, PolicyDecision
from code_ai.core.planning.preconditions import PreconditionGate
from code_ai.core.verification import (
    KIND_PRIORITY,
    CommandKind,
    ProjectVerification,
    detect_project_verification,
    verification_kind,
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
        verification_memo: Callable[[ProjectVerification], None] | None = None,
        write_agent_types: frozenset[str] = frozenset(),
        review_tools_available: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.session_id = session_id
        self._workspace = workspace
        self._verification_detector = verification_detector
        # Optional sink for a successful detection (e.g. persist the project's
        # test/build commands as a durable memory). Best-effort by contract.
        self._verification_memo = verification_memo
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
        # Paths the task targets that live *outside* the workspace (e.g. ~/.zshrc).
        # File tools are workspace-bound, so these can only be changed through
        # commands; the completion gate must accept command evidence for them
        # instead of demanding workspace file-change evidence the task cannot
        # honestly produce. Seeded from the objective, grown by boundary errors.
        self.external_targets: tuple[str, ...] = ()
        self.no_progress_rounds = 0
        self.double_check_pending = False
        self.accepted_final_text: str | None = None
        # The blocking ask_user question the previous turn ended on, if any.
        # The user's next message answers it: the app layer resumes the paused
        # plan instead of reclassifying the reply as a brand-new task, and the
        # reply is recorded as USER_ANSWER evidence at that point.
        self.pending_question: str | None = None
        # Set when the user denies a workspace/command action this turn - the
        # strongest available signal that the surface classifier's mutation
        # label is wrong (e.g. a question that merely mentions "implement").
        # While set, every consumer sees the task as a prose deliverable: no
        # corrective nudges toward write tools, no file-change completion
        # demands, prose streams as the answer. See note_user_denial.
        self.user_declined_mutation = False
        # Whether the model's own tool calls produced successful evidence this
        # task (the host's automatic workspace listing does not count). A prose
        # ending on a prose-deliverable task settles the checklist only when
        # real work backs it; see settle_agent_plan_on_final_answer.
        self._gathered_non_host_evidence = False
        # Risk-proportional evidence gate for success completion claims.
        self.completion_gate = CompletionGate(
            max_rejections_without_progress=config.max_completion_rejections
        )
        # Evidence-based preconditions checked before action-taking tools run.
        self._precondition_gate = PreconditionGate(workspace=workspace)
        # Sub-agent profile names that can mutate the workspace; delegating to
        # one of these before any reconnaissance is gated. Empty (the default
        # for directly-constructed services) disables the delegation gate.
        self._write_agent_types = write_agent_types
        # Whether a review channel (review tools or a reviewer sub-agent)
        # actually exists in this session. ``None`` (the default for
        # directly-constructed services) means no review can be demanded, so
        # the high-risk review requirement never fires without a way to comply.
        self._review_tools_available = review_tools_available
        # Workspace-relative paths whose current content the agent has observed
        # (read, written, or reported by a sub-agent). Deliberately survives
        # begin_turn's per-turn ledger reset: a file read two turns ago is still
        # known content, so the read-before-write gate must not demand a re-read.
        self._known_content_paths: set[str] = set()
        # Whether the turn already spent its one nudge about ending in prose
        # with the workspace changed but unverified. See
        # note_final_answer_verification_debt.
        self._verification_debt_nudged = False

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
                else:
                    self._memo_verification(self._project_verification)
        return self._project_verification

    def _memo_verification(self, verification: ProjectVerification) -> None:
        """Hand a successful detection to the configured memo sink, best-effort.

        Persisting what was detected means future sessions know the project's
        test/build commands without re-discovering them; a failing sink must
        never taint the detection result itself.
        """

        if self._verification_memo is None or not verification.has_any:
            return
        try:
            self._verification_memo(verification)
        except Exception:  # noqa: BLE001 - the memo is an optional side channel
            pass

    def _classify_verification(self, argv: list[str] | str | None) -> CommandKind | None:
        return verification_kind(argv, self.project_verification())

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
        if not changes_require_verification(self.ledger.current_changed_paths()):
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
        #
        # A bare "continue"/"siga" takes the same path even without the explicit
        # resume flag: it states no new objective, so classifying it afresh only
        # loses the task. The surface classifier reads it as CONVERSATION, which
        # silences the whole runtime task state block precisely when the model
        # most needs it - the observed failure was mid-implementation follow-ups
        # running with no planner scaffolding at all.
        if self._continues_current_task(text, resume=resume):
            self._require_tool_calling(provider_supports_tools)
            await self._resume_turn(text)
            return
        self.profile = TaskProfile.from_user_text(text)
        self.plan = ExecutionPlan.for_profile(
            self.profile, max_steps=self.config.max_plan_steps
        )
        self.agent_plan = None
        self.ledger = EvidenceLedger(session_id=self.session_id)
        self.approved_external_gaps = ()
        self.approved_external_gap = self.profile.allows_web_first
        self.external_targets = _external_path_targets(text, self._workspace)
        self.no_progress_rounds = 0
        self.double_check_pending = False
        self.accepted_final_text = None
        self.pending_question = None
        self.user_declined_mutation = False
        self._gathered_non_host_evidence = False
        self.completion_gate.reset()
        self._precondition_gate.note_turn_started()
        self._verification_debt_nudged = False
        self._require_tool_calling(provider_supports_tools)

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

    async def _resume_turn(self, text: str = "") -> None:
        """Continue the current plan into an execution turn without rebuilding it.

        Used when the user approves the plan and switches to act mode, and when
        their message answers a blocking ask_user question: the profile,
        deterministic skeleton, model-authored checklist and evidence ledger are
        all kept, so execution picks up where the previous turn left off. Only
        the per-turn bookkeeping resets, and the existing plan snapshot is
        re-emitted so the task sidebar (collapsed when the previous turn ended)
        reappears with live progress.

        With a question pending, ``text`` is the user's reply and becomes the
        USER_ANSWER evidence the ask recorded nothing for (at ask time no answer
        exists yet). Whatever the text was, the question stops being pending:
        the resumed turn continues from what the user actually said.
        """
        if self.pending_question:
            question, self.pending_question = self.pending_question, None
            answer = text.strip()
            if answer:
                record = self.ledger.record_user_answer(
                    plan=self.plan,
                    step_id=self.current_step.step_id if self.current_step else None,
                    question=question,
                    answer=answer,
                )
                # The user's reply is real task input, not host bookkeeping.
                self._gathered_non_host_evidence = True
                await self.event_bus.emit(
                    "planning.evidence.recorded",
                    record.compact(),
                    source="core.planner",
                )
        self.no_progress_rounds = 0
        self.double_check_pending = False
        self.accepted_final_text = None
        self.completion_gate.reset()
        self._verification_debt_nudged = False
        await self._emit_phase(PlanningPhase.EXECUTE)
        # A plan paused at the previous turn's end (see suspend_agent_plan) comes
        # back to life: the current step starts running again in the sidebar.
        if self.agent_plan and self.agent_plan.status == PlanStatus.WAITING:
            self.agent_plan.resume()
        if self.agent_plan and self.agent_plan.status == PlanStatus.ACTIVE:
            await self.event_bus.emit(
                "planning.step.started",
                self.plan_snapshot(),
                source="core.planner",
            )

    def _continues_current_task(self, text: str, *, resume: bool) -> bool:
        """Whether this turn carries on the live task instead of starting one.

        Either the caller said so (plan approved, or a reply to a blocking
        question) or the message itself is a bare continuation marker. Both need
        a live task to continue: with no profile or plan there is nothing to
        resume and the text must be classified normally.
        """
        if self.profile is None or self.plan is None:
            return False
        return resume or is_continuation_request(text)

    def _require_tool_calling(self, provider_supports_tools: bool) -> None:
        if not (self.profile and self.profile.requires_workspace_mutation):
            return
        if provider_supports_tools:
            return
        raise ToolExecutionError(
            "This implementation task requires tool-calling support; chat text cannot "
            "modify the workspace."
        )

    def has_pending_question(self) -> bool:
        """Whether the previous turn ended on a blocking ask_user question."""
        return bool(self.enabled and self.pending_question)

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
            profile=self._effective_profile(),
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
            profile=self._effective_profile(),
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
            profile=self._effective_profile(),
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
        host_initiated: bool = False,
    ) -> list[EvidenceRecord]:
        if tool_name == "submit_plan":
            await self.submit_agent_plan(payload.get("steps"))
            return []
        if tool_name == "complete_plan_step":
            # The model owns its checklist cursor: it advances only when the model
            # declares a step finished, never by a heuristic that cannot know which
            # of the model's free-form steps a given piece of evidence belongs to.
            await self._advance_agent_plan(
                completed_step=str(payload.get("completed_step") or "")
            )
            return []
        if tool_name == "ask_user":
            # The question is not evidence of an answer. Remember it as pending
            # so the user's actual reply - the next message, which resumes the
            # paused plan - is what gets recorded as USER_ANSWER.
            question = str(payload.get("question") or "").strip()
            if question:
                self.pending_question = question
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
            classify_verification=self._classify_verification,
        )
        if not host_initiated and any(record.success for record in records):
            # Work the model itself did (the host's automatic workspace listing
            # does not count); lets a prose ending settle the checklist even
            # when the model never declared steps done. See
            # settle_agent_plan_on_final_answer.
            self._gathered_non_host_evidence = True
        for record in records:
            if record.success and record.evidence_type in {
                EvidenceType.FILE_READ,
                EvidenceType.FILE_CREATED,
                EvidenceType.FILE_CHANGED,
            }:
                self._known_content_paths.update(record.affected_paths)
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

    def precondition_gap(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Advisory evidence check before an action-taking tool runs.

        Returns a corrective instruction when the call is not yet grounded in
        enough evidence (e.g. mutating an existing file that was never read),
        or ``None`` when the call may proceed. Each gate nudges at most once
        and then fails open, so a wrong heuristic costs one round-trip, never
        a trapped turn.
        """
        if not self.enabled:
            return None
        if tool_name == "dispatch_agent":
            return self._precondition_gate.blind_delegation_gap(
                arguments,
                has_local_grounding=self._has_delegation_grounding(),
                write_agent_types=self._write_agent_types,
            )
        # Checked first: on a read-only task the write itself is the anomaly,
        # so "you were not asked to write files" beats "read the file first".
        artifact_gap = self._precondition_gate.unrequested_artifact_gap(
            tool_name,
            arguments,
            task_requests_mutation=self._task_produces_workspace_effects(),
        )
        if artifact_gap:
            return artifact_gap
        unread_gap = self._precondition_gate.unread_mutation_gap(
            tool_name, arguments, known_content_paths=self._known_content_paths
        )
        if unread_gap:
            return unread_gap
        # Last, because grounding beats sizing: a blind overwrite should hear
        # "read it first" before it hears "write it in smaller steps".
        return self._precondition_gate.oversized_write_gap(tool_name, arguments)

    def _has_delegation_grounding(self) -> bool:
        """Whether the orchestrator has observed enough to brief a coder.

        Real content knowledge counts (files read/written this session, local
        searches, a deliberately concluded discovery); a bare workspace listing
        does not - knowing file names is not understanding the code a coder
        prompt must describe.
        """
        if self._known_content_paths:
            return True
        return self.ledger.has_success(
            EvidenceType.FILE_READ,
            EvidenceType.LOCAL_SEARCH_MATCH,
            EvidenceType.LOCAL_SEARCH_COMPLETED,
            EvidenceType.DISCOVERY_COMPLETED,
        )

    async def note_workspace_boundary_rejection(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> None:
        """Learn mid-turn that the task's real target lives outside the workspace.

        A file tool rejecting a path with a boundary error is direct evidence the
        model is trying to change an external file. Remembering the target lets
        the completion gate accept command evidence for it instead of demanding
        workspace file-change evidence - which is what pushes models to fabricate
        pointless workspace files just to satisfy the gate.
        """
        path = str(arguments.get("path") or "").strip()
        if not path or not self._path_is_outside_workspace(path):
            return
        if path in self.external_targets:
            return
        self.external_targets = (*self.external_targets, path)
        await self.event_bus.emit(
            "planning.external_target.detected",
            {
                "tool_name": tool_name,
                "path": path,
                "external_targets": list(self.external_targets),
            },
            source="core.planner",
        )

    async def note_user_denial(self, tool_name: str, reason: str) -> None:
        """The user refused a workspace/command action this turn.

        An explicit denial is the strongest signal available that the surface
        classifier's mutation label is wrong - the observed failure was a plain
        question ("pelo que voce comecaria a implementar hoje?") classified as
        an implementation task, where the runtime kept demanding file changes
        through three denials and even nudged the model back into mutating
        after it had correctly fallen back to prose.

        Downgrade the demand, not the tools: the task is treated as a prose
        deliverable from here on (read-only context rules, no corrective nudge,
        no file-change completion requirement, prose streams as the answer),
        but the model may still mutate later in the turn if the user asks -
        the advisory gates keep working.
        """
        if not self.enabled or self.user_declined_mutation:
            return
        if not (self.profile and self._task_produces_workspace_effects()):
            return
        self.user_declined_mutation = True
        await self.event_bus.emit(
            "planning.mutation_demand.dropped",
            {"tool_name": tool_name, "reason": reason},
            source="core.planner",
        )

    def _path_is_outside_workspace(self, path_value: str) -> bool:
        """Best-effort: does this path resolve outside the workspace root?

        With no configured workspace nothing is "outside", and unresolvable
        paths are treated as internal so the strict evidence gate stays intact.
        """
        if self._workspace is None:
            return False
        try:
            root = self._workspace.expanduser().resolve()
            candidate = Path(path_value).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve(strict=False)
        except (OSError, ValueError):
            return False
        return resolved != root and not resolved.is_relative_to(root)

    def _has_external_action_evidence(self) -> bool:
        """Whether any successful system action could have applied the external change.

        Commands and terminal interactions are the only channels that can touch
        files outside the workspace, so their success is the honest stand-in for
        the file-change evidence the ledger cannot hash there.
        """
        return self.ledger.has_success(
            EvidenceType.COMMAND_SUCCEEDED,
            EvidenceType.VERIFICATION_PASSED,
            EvidenceType.TERMINAL_OBSERVED,
        )

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
        if self.user_declined_mutation:
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
        # Chat needs no scaffolding - but a "conversation" that has started
        # writing to the workspace is not chat, whatever the classifier said.
        # Staying silent there is how a mislabelled turn ends up running with no
        # runtime state at all.
        if (
            self.profile.intent == TaskIntent.CONVERSATION
            and not self._has_workspace_change_evidence()
        ):
            return ""
        profile = self._effective_profile()
        assert profile is not None  # guarded above
        current = self.current_step
        snapshot = self.plan.snapshot()
        # After a user denial the skeleton's file-evidence demands no longer
        # apply; showing them would keep steering the model toward the very
        # changes the user refused.
        required_evidence = (
            [item.value for item in current.required_evidence]
            if current and not self.user_declined_mutation
            else []
        )
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
                "finishing. The moment you finish the current step, call "
                "complete_plan_step with its title - in the same tool batch as the "
                "step's final action - so progress is marked immediately. Never "
                "save the marking calls for the end of the task. Call submit_plan "
                "again only if your approach genuinely changes.\n"
            )
        header = (
            "Runtime task state. Treat this as authoritative host state, not a user request.\n"
            f"Original objective: {profile.objective}\n"
            f"Acceptance criteria: {profile.acceptance_criteria}\n"
            f"Planner mode: {self.mode.value}\n"
            f"Semantic phase: {self.phase.value}\n"
            f"Plan revision: {self.plan.revision}\n"
            f"Plan progress: {plan_progress}\n"
            f"Current step: {step_title}\n"
            f"Current step kind: {current.kind.value if current else 'none'}\n"
            f"Required evidence: {required_evidence}\n"
            f"Changed paths: {self.ledger.current_changed_paths()}\n"
            + (
                "Files whose current content you have seen (read or written): "
                f"{self._known_paths_preview()}. Before modifying any *other* "
                "existing file, read it first so the change is grounded in its "
                "actual content.\n"
                if self._known_content_paths
                else ""
            )
            + (
                f"Outside-workspace target(s): {sorted(self.external_targets)}. "
                "File tools only work inside the workspace: change these targets "
                "with execute_command and confirm with a read-back command. Never "
                "create or edit workspace files just to satisfy completion "
                "evidence.\n"
                if self.external_targets
                else ""
            )
            + f"Latest verification passed: {self.ledger.latest_verification_passed}\n"
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
        if self._task_produces_workspace_effects():
            return header + (
                "Rules: prefer the recommended tools, work on the current step, and do not "
                "claim completion from prose. For workspace changes, call write_file or "
                "edit_code; for completion, call complete_task after verification evidence exists."
            )
        # Read-only task: the completion rules above would misdirect the model.
        # Told "do not claim completion from prose" while holding only reading
        # evidence, models invent a deliverable - typically writing an unrequested
        # notes/summary document at the end just to have "completion evidence".
        # For a question, the prose answer IS the deliverable.
        return header + (
            "READ-ONLY TASK - the user asked for information, not for workspace "
            "changes.\n"
            "Rules:\n"
            "- Gather evidence with the recommended read-only tools and keep the "
            "analysis internal.\n"
            "- When you have enough evidence, answer the user directly in the chat. "
            "Your prose answer completes this task; calling complete_task is not "
            "required.\n"
            "- Do NOT create or edit any file. Nobody asked for a document: if you "
            "are about to write notes, a summary, or an analysis file, put that "
            "content in your answer instead."
        )

    def _task_produces_workspace_effects(self) -> bool:
        """Whether this task's deliverable lives in the workspace (or a command).

        Mutation and command tasks legitimately end in tool actions and a
        complete_task claim. Everything else (inspection, research, explanation)
        ends in a chat answer, and the task context must say so explicitly.

        A task that was labelled read-only but has since *changed the workspace*
        counts too: the classifier was wrong, and the evidence proves it. The
        user's own denial still wins - that is a decision, not a misreading.
        """
        if self.profile is None:
            return True  # fail toward the stricter, action-oriented rules
        if self.user_declined_mutation:
            return False
        return (
            self.profile.requires_workspace_mutation
            or self.profile.intent == TaskIntent.COMMAND_EXECUTION
            or self._has_workspace_change_evidence()
        )

    def _effective_profile(self) -> TaskProfile | None:
        """The task profile with runtime corrections applied.

        The surface classifier reads keywords in the first message; the ledger
        records what the agent actually did. Where they disagree, the evidence
        wins, in both directions:

        - A user denial mid-turn *downgrades* the mutation label (see
          :meth:`note_user_denial`) - the strongest possible signal that the
          label was wrong.
        - An observed workspace change *upgrades* a task the classifier read as
          conversation or inspection. Without this, a continuation or a
          low-level instruction that mutates files runs with none of the
          mutation discipline the change deserves.

        Every consumer that steers or gates on the label - the task context
        block, the tool policy, the completion gate - sees this corrected view.
        """
        if self.profile is None:
            return None
        if self.user_declined_mutation:
            criteria = [
                criterion
                for criterion in self.profile.acceptance_criteria
                if criterion
                not in {CRITERION_APPLY_VIA_TOOLS, CRITERION_VERIFY_AFTER_MUTATION}
            ]
            return self.profile.model_copy(
                update={
                    "requires_workspace_mutation": False,
                    "requires_verification": False,
                    "acceptance_criteria": criteria,
                }
            )
        if (
            not self.profile.requires_workspace_mutation
            and self._has_workspace_change_evidence()
        ):
            # Upgrading costs nothing the evidence does not already carry: the
            # change exists, so the file-evidence requirement it switches on is
            # satisfied by construction, and only the verification demand is new.
            return self.profile.model_copy(
                update={
                    "requires_workspace_mutation": True,
                    "requires_local_context": True,
                    "requires_verification": True,
                    "allows_web_first": False,
                    "acceptance_criteria": [
                        *self.profile.acceptance_criteria,
                        CRITERION_VERIFY_AFTER_MUTATION,
                    ],
                }
            )
        return self.profile

    def _known_paths_preview(self, *, limit: int = 15) -> str:
        paths = sorted(self._known_content_paths)
        if len(paths) <= limit:
            return str(paths)
        shown = paths[:limit]
        return f"{shown} (+{len(paths) - limit} more)"

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

        invalid = self._claim_validity_gaps(claim)
        if invalid:
            await self.event_bus.emit(
                "planning.completion.rejected",
                {"missing_requirements": list(invalid)},
                source="core.planner",
            )
            return CompletionDecision(
                accepted=False,
                outcome="success",
                missing_requirements=tuple(invalid),
            )

        verdict = self.completion_gate.evaluate(
            self._completion_context(claim),
            progress_fingerprint=self.progress_signature(),
        )
        if verdict.double_check_requested:
            self.double_check_pending = True
        if not verdict.accepted:
            await self.event_bus.emit(
                "planning.completion.rejected",
                {
                    "missing_requirements": list(verdict.missing_requirements),
                    "policy": verdict.policy_name,
                },
                source="core.planner",
            )
            return CompletionDecision(
                accepted=False,
                outcome="success",
                missing_requirements=verdict.missing_requirements,
            )

        await self._accept_success_completion(
            claim,
            acceptance_note=verdict.acceptance_note,
            unresolved=verdict.missing_requirements if verdict.fail_open else (),
        )
        return CompletionDecision(
            accepted=True,
            outcome="success",
            final_text=self.accepted_final_text or claim.summary,
        )

    def _claim_validity_gaps(self, claim: CompletionClaim) -> list[str]:
        """Structural problems with the claim itself, before any evidence policy.

        These are practically unreachable through the normal tool flow (the tool
        already rejects an empty summary; objectives are set once per turn), so
        they are judged outside the gate's pacing counter.
        """
        if not (self.profile and self.plan):
            return ["No active plan exists."]
        gaps: list[str] = []
        if claim.summary.strip() == "":
            gaps.append("summary is required.")
        if self.plan.objective != self.profile.objective:
            gaps.append("plan objective no longer matches the original objective.")
        return gaps

    def _has_workspace_change_evidence(self) -> bool:
        """Whether this task actually changed the workspace, by any channel.

        File tools produce hashed ``FILE_CREATED``/``FILE_CHANGED`` evidence;
        shell commands produce only the ledger's coarse flag. Both are real
        changes, and every consumer that keys off "did something change" must
        see both - keying off file tools alone made the whole discipline blind
        to the channel weak models actually use.
        """
        return (
            self.ledger.has_success(EvidenceType.FILE_CREATED, EvidenceType.FILE_CHANGED)
            or self.ledger.command_mutated_workspace
        )

    def _change_verification_state(self) -> tuple[bool, bool, str]:
        """``(workspace changed, verification settled, what is still owed)``.

        Verification only applies when (a) something actually changed, (b) the
        change is not documentation-only and (c) the project exposes a way to
        verify it. With no detectable test/build system we degrade gracefully
        and complete with a warning rather than trapping the agent demanding
        evidence it cannot get. A command-driven change has no paths to judge
        doc-only-ness by, so it always counts as verifiable work.
        """
        has_file_change = self._has_workspace_change_evidence()
        verification_applies = (
            has_file_change
            and (
                self.ledger.command_mutated_workspace
                or changes_require_verification(self.ledger.current_changed_paths())
            )
            and self.project_verification().has_any
        )
        verified, gap = self._verification_status(verification_applies)
        return has_file_change, verified, gap

    def _completion_context(self, claim: CompletionClaim) -> CompletionContext:
        """Snapshot the evidence the completion policies are allowed to judge."""
        profile = self._effective_profile()
        assert profile is not None  # guarded by _claim_validity_gaps
        has_file_change, verified, verification_gap = self._change_verification_state()
        changed_paths = tuple(self.ledger.current_changed_paths())
        phantom: tuple[str, ...] = ()
        # A command-driven change is real but path-less, so no claimed path can
        # be checked against it. Calling those paths fabricated would accuse the
        # model of inventing exactly the work it just did through the shell.
        if (
            has_file_change or profile.requires_workspace_mutation
        ) and not self.ledger.command_mutated_workspace:
            # Paths outside the workspace never enter the ledger's hash map, so
            # honestly claiming an external target must not read as fabrication.
            claimed = {
                path
                for path in claim.changed_paths
                if not self._path_is_outside_workspace(path)
            }
            phantom = tuple(sorted(claimed - set(changed_paths)))
        pending_declared: tuple[str, ...] = ()
        incomplete_skeleton: tuple[str, ...] = ()
        if self.agent_plan is not None:
            # The model's own checklist is the source of truth once submitted;
            # the skeleton is only the fallback narrative.
            pending_declared = tuple(
                step.title
                for step in self.agent_plan.steps
                if step.status == PlanStepStatus.PENDING
            )
        elif self.plan is not None:
            incomplete_skeleton = tuple(
                step.title
                for step in self.plan.steps
                if step.kind != PlanStepKind.COMPLETE
                and step.status
                not in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
            )
        return CompletionContext(
            claim=claim,
            profile=profile,
            changed_paths=changed_paths,
            has_file_change=has_file_change,
            write_attempted=self.ledger.mutation_was_attempted(),
            has_analysis_evidence=self.ledger.has_success(
                EvidenceType.FILE_READ,
                EvidenceType.WORKSPACE_LISTED,
                EvidenceType.LOCAL_SEARCH_MATCH,
                EvidenceType.LOCAL_SEARCH_COMPLETED,
                EvidenceType.DISCOVERY_COMPLETED,
                EvidenceType.WEB_RESULT,
            ),
            verified=verified,
            verification_gap=verification_gap,
            verification_failed_this_turn=self.ledger.has_record(
                EvidenceType.VERIFICATION_FAILED
            ),
            external_targets=self.external_targets,
            has_external_action_evidence=self._has_external_action_evidence(),
            phantom_claimed_paths=phantom,
            pending_declared_steps=pending_declared,
            incomplete_skeleton_steps=incomplete_skeleton,
            double_check_enabled=self.config.double_check_completion,
            double_check_pending=self.double_check_pending,
            severe_review_findings=self.ledger.open_severe_review_findings,
            has_current_review=self.ledger.review_ran_after_last_change,
            review_required_for_risk=self._review_required_for_risk(),
        )

    def _review_required_for_risk(self) -> bool:
        """Whether a high-risk claim may be asked to carry review evidence.

        Requires both the config switch and an actual review channel; demanding
        a review the session cannot run would only trap the turn until the
        gate's fail-open released it.
        """
        if not self.config.require_review_for_risky_changes:
            return False
        if self._review_tools_available is None:
            return False
        try:
            return bool(self._review_tools_available())
        except Exception:
            return False

    def _verification_status(self, verification_applies: bool) -> tuple[bool, str]:
        """Whether the current change set counts as verified, and why not.

        Verified means a verification passed against the current changes *and*
        it was at least as strong as the strongest check the project exposes
        (test > build > typecheck > lint). A lint-only pass on a project with a
        test suite is the classic way to game the gate with a cheap exit-0 run,
        so it does not count; the returned gap message tells the model exactly
        which command it still owes. Projects that only expose weaker checks
        degrade gracefully - their strongest available check is enough.
        """
        if not self.config.require_verification_for_changes or not verification_applies:
            return True, ""
        project = self.project_verification()
        if not self.ledger.latest_verification_passed:
            return False, (
                "no current successful verification evidence exists. "
                + project.prompt_hint()
            )
        required = project.required_kind
        passed = self.ledger.strongest_verification_kind_passed()
        if required is None or (
            passed is not None and KIND_PRIORITY[passed] <= KIND_PRIORITY[required]
        ):
            return True, ""
        primary = project.primary()
        ran = passed.value if passed else "an unclassified check"
        return False, (
            f"verification for the current changes only ran at the '{ran}' level, "
            f"but this project exposes a {required.value} command "
            f"(`{primary.display if primary else required.value}`). Run it against "
            "the current changes; a weaker check cannot prove the change works."
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
        if self.profile.requires_workspace_mutation and not self.user_declined_mutation:
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

    async def _advance_agent_plan(self, *, completed_step: str = "") -> None:
        """Move the sidebar cursor forward when the model reports a step done.

        Driven solely by the model's complete_plan_step calls, so the checklist
        tracks real declared progress rather than racing one step ahead per piece
        of evidence. When the declared title names a step *ahead* of the cursor
        (the model worked through several steps before reporting), the cursor
        catches up through that step - one completed/started event per step, so
        the sidebar marks each one instead of freezing until complete_task's
        complete_all() flips everything at once. The last step stays running
        until completion settles the whole plan, so the panel always shows a
        live step.
        """
        if not self.agent_plan or self.agent_plan.status != PlanStatus.ACTIVE:
            return
        target = self.agent_plan.resolve_completed_index(completed_step)
        while (
            self.agent_plan.status == PlanStatus.ACTIVE
            and self.agent_plan.current_index <= target
        ):
            if not self.agent_plan.advance():
                # advance() refuses the final step by design; remember that the
                # model declared it done so a clean final answer can settle the
                # plan (see settle_agent_plan_on_final_answer).
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

    async def suspend_agent_plan(self) -> None:
        """Pause the checklist when a turn ends without settling the plan.

        Every turn exit that leaves the model-authored checklist ACTIVE - a
        blocking ask_user question, a prose answer without a declared final
        step, a cancellation, a provider failure, an exhausted budget - hands
        control back to the user (phase ``waiting_user``) while the sidebar
        still shows a spinning step. That step would spin forever: nothing is
        running anymore. Pause the plan and push the WAITING snapshot so every
        surface renders the step as paused; a resumed turn reactivates it (see
        ``_resume_turn``) and the next fresh turn replaces it anyway.

        Idempotent and safe on every path: settled/absent plans are left alone.
        """
        plan = self.agent_plan
        if plan is None or plan.status != PlanStatus.ACTIVE:
            return
        plan.pause()
        await self.event_bus.emit(
            "planning.plan.waiting",
            self.plan_snapshot(),
            source="core.planner",
        )

    async def note_final_answer_verification_debt(self) -> str | None:
        """One nudge when a turn ends in prose having changed but not verified.

        The evidence gate is the runtime's only hard quality trap, and it hangs
        off ``complete_task`` - a meta-tool weak models simply never call (zero
        calls across 241 observed model steps). Every one of those turns ended
        the other way: the model stopped calling tools and answered. So the
        checkpoint belongs at *that* stop, keyed on what the ledger recorded
        rather than on the model volunteering a claim.

        Returns the correction to inject, or ``None`` to let the answer stand.
        Bounded to once per turn and fail-open by construction: a second prose
        answer is accepted with the debt unpaid, exactly like ``CompletionGate``
        releases a turn it cannot satisfy. Keyed on observed change, never on the
        task label, so a read-only task never sees it.
        """
        if not (self.enabled and self.profile):
            return None
        if self.user_declined_mutation or self._verification_debt_nudged:
            return None
        has_change, verified, gap = self._change_verification_state()
        if not has_change or verified:
            return None
        self._verification_debt_nudged = True
        await self.event_bus.emit(
            "planning.verification_debt.nudged",
            {
                "changed_paths": self.ledger.current_changed_paths(),
                "command_mutated_workspace": self.ledger.command_mutated_workspace,
            },
            source="core.planner",
        )
        return (
            "Runtime check: this turn changed the workspace, but "
            + (gap or "no current successful verification evidence exists.")
            + " Run that check now against the current state. If it genuinely "
            "cannot run here, answer again and state the unverified change as an "
            "explicit limitation instead of leaving it unsaid."
        )

    async def settle_agent_plan_on_final_answer(self) -> None:
        """Complete the checklist when a turn ends cleanly in a final answer.

        ``complete_all`` normally runs only when a ``complete_task`` claim is
        accepted, but a read-only task legitimately ends in a plain prose answer.

        The plan settles on that answer when *either*:

        - the model has already declared the final step done via
          ``complete_plan_step`` (the answer is that step's execution), or
        - the task's deliverable *is* prose (inspection / research /
          conversation) *and* the model has completed at least one checklist
          step. For those tasks the final answer is the completion signal -
          exactly the role ``complete_task`` plays for mutation tasks, and what
          the read-only task context already tells the model ("Your prose answer
          completes this task"). Requiring a separate ``complete_plan_step`` on
          the last step otherwise strands genuinely finished work at "waiting
          for you", since models routinely answer in prose without declaring the
          closing step (the observed failure: a checklist frozen at 1/N after a
          full answer was already delivered).

        "Completed at least one step" used to be the only progress signal, but
        models routinely do the work without ever calling complete_plan_step:
        the observed failure was a research turn that submitted a plan, ran a
        web search, delivered the full synthesized answer - and left the
        sidebar at "0/4, waiting for you" with nothing actually pending. Real
        gathered evidence (any successful model-initiated tool result, or a
        recorded user answer; the host's automatic workspace listing does not
        count) is just as much proof the prose is a worked deliverable, so
        either signal settles the plan. The pure pause case stays apart: a turn
        that submits a plan and immediately asks a clarifying question in
        prose - no declared step, no gathered evidence - is genuinely
        unfinished, so its plan is left ACTIVE for :meth:`suspend_agent_plan`
        to pause and the sidebar to show where it stopped.

        Mutation/command tasks stay conservative regardless of progress: their
        real completion runs through the evidence gate (``complete_task`` ->
        ``_accept_success_completion``), so an undeclared prose ending is never
        marked done here without verification.

        This call only fires from the clean no-tool final-answer path; blocking
        questions raised via ``ask_user``, cancellations and wind-downs suspend
        the plan instead.
        """
        plan = self.agent_plan
        if plan is None or plan.status != PlanStatus.ACTIVE:
            return
        completed_steps = sum(
            1 for step in plan.steps if step.status == PlanStepStatus.COMPLETED
        )
        prose_deliverable_done = not self._task_produces_workspace_effects() and (
            completed_steps > 0 or self._gathered_non_host_evidence
        )
        if not (plan.final_step_declared or prose_deliverable_done):
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
        code_changed = changes_require_verification(changed_paths)
        if code_changed and not self.project_verification().has_any:
            return (
                "Warning: no automated test/build system was detected in this "
                "project, so the change was not automatically verified."
            )
        return "No verification evidence was required."

    async def _accept_success_completion(
        self,
        claim: CompletionClaim,
        *,
        acceptance_note: str = "",
        unresolved: tuple[str, ...] = (),
    ) -> None:
        if self.plan and self.current_step and self.current_step.kind == PlanStepKind.COMPLETE:
            self.current_step.status = PlanStepStatus.COMPLETED
            self.plan.status = PlanStatus.COMPLETED
            self.plan.updated_at = utc_now_iso()
        if self.agent_plan:
            self.agent_plan.complete_all()
        changed_paths = self.ledger.current_changed_paths()
        verification = self._completion_verification_note(changed_paths)
        # An outside-workspace change never enters the ledger's hash map; name the
        # external targets instead of reporting a misleading "none".
        if changed_paths:
            changed_line = f"Changed paths: {', '.join(changed_paths)}"
        elif self.external_targets:
            changed_line = (
                "Outside-workspace target(s): "
                + ", ".join(self.external_targets)
                + " (applied via commands; not covered by project verification)"
            )
            verification = ""
        else:
            changed_line = "Changed paths: none"
        # A fail-open acceptance stays honest: what the gate could not confirm is
        # reported to the user instead of silently dropped.
        unresolved_line = (
            "Unresolved completion requirements: " + "; ".join(unresolved)
            if unresolved
            else ""
        )
        # The claim's own disclosures reach the user too. They are the honest
        # currency the completion gate accepts in place of a fix (e.g. an open
        # review finding), so hiding them from the final message would turn the
        # disclosure escape hatch into a silent bypass.
        remaining_line = (
            "Remaining issues: " + "; ".join(claim.remaining_issues)
            if claim.remaining_issues
            else ""
        )
        limitations_line = (
            "Limitations: " + "; ".join(claim.limitations) if claim.limitations else ""
        )
        self.accepted_final_text = bound_text(
            "\n".join(
                item
                for item in (
                    claim.summary,
                    changed_line,
                    verification,
                    acceptance_note,
                    remaining_line,
                    limitations_line,
                    unresolved_line,
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


# Path-like tokens in free text: "~", "~/...", or an absolute "/..." segment.
# The lookbehind keeps URL fragments ("https://host/x"), protocol-relative
# references and word-internal slashes ("e/ou") from being misread as paths.
_PATH_TOKEN_RE = re.compile(r"(?<![:\w/])(~(?:/[\w.+-][\w./+-]*)?|/[\w.+-][\w./+-]*)")


def _external_path_targets(text: str, workspace: Path | None) -> tuple[str, ...]:
    """Path-like tokens in the objective that resolve outside the workspace root.

    Purely a *signal*, never a hard classification: it widens what the
    completion gate accepts as evidence (commands instead of workspace file
    hashes) but removes no requirement for ordinary workspace tasks. With no
    workspace configured nothing is "outside", and unresolvable tokens are
    ignored, so a false positive costs nothing and a miss degrades to the
    boundary-error fallback (see note_workspace_boundary_rejection).
    """
    if workspace is None:
        return ()
    try:
        root = workspace.expanduser().resolve()
    except OSError:
        return ()
    targets: list[str] = []
    for token in _PATH_TOKEN_RE.findall(text):
        cleaned = token.rstrip(".,;:!?)('\"")
        if not cleaned or cleaned == "/":
            continue
        try:
            resolved = Path(cleaned).expanduser().resolve(strict=False)
        except (OSError, ValueError):
            continue
        if not resolved.is_absolute():
            continue
        if resolved != root and not resolved.is_relative_to(root):
            if cleaned not in targets:
                targets.append(cleaned)
    return tuple(targets)


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
