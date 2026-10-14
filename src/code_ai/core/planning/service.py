from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.config.models import PlannerConfig
from code_ai.core.errors import ToolExecutionError
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
    """Owns semantic task progress and completion decisions."""

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
        # commands; the task context points the model there instead of at
        # workspace files. Seeded from the objective, grown by boundary errors.
        self.external_targets: tuple[str, ...] = ()
        self.accepted_final_text: str | None = None
        # The blocking ask_user question the previous turn ended on, if any.
        # The user's next message answers it: the app layer resumes the paused
        # plan instead of reclassifying the reply as a brand-new task, and the
        # reply is recorded as USER_ANSWER evidence at that point.
        self.pending_question: str | None = None
        # Set when the user denies a workspace/command action this turn - the
        # strongest available signal that the task is not the change it was
        # declared or guessed to be (e.g. a question that mentions "implement").
        # While set, every consumer sees the task as a prose deliverable: no
        # mutation rules in the task context, prose streams as the answer. See
        # note_user_denial.
        self.user_declined_mutation = False
        # The model's own decision on whether this task changes the workspace,
        # declared through submit_plan's changes_workspace. ``None`` until the
        # model says. The surface classifier only guesses from keywords in the
        # first message; this is the authority. The mutation rules in the task
        # context key off this declaration or off observed evidence, never off
        # the keyword guess - a guess the model disagreed with used to turn
        # into a round of corrections in both directions.
        self.declared_mutation: bool | None = None
        # Evidence-based preconditions checked before action-taking tools run.
        self._precondition_gate = PreconditionGate(workspace=workspace)
        # Sub-agent profile names that can mutate the workspace; delegating to
        # one of these before any reconnaissance is gated. Empty (the default
        # for directly-constructed services) disables the delegation gate.
        self._write_agent_types = write_agent_types
        # Workspace-relative paths whose current content the agent has observed
        # (read, written, or reported by a sub-agent). Deliberately survives
        # begin_turn's per-turn ledger reset: a file read two turns ago is still
        # known content, so the read-before-write gate must not demand a re-read.
        self._known_content_paths: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def project_verification(self) -> ProjectVerification:
        """The detected verification capabilities of the workspace, cached.

        Detection is lazy and best-effort: with no workspace, or none detected,
        this returns an empty ``ProjectVerification`` and the verification hint
        says there is nothing to run.
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
        if not (self.profile and self._task_produces_workspace_effects()):
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
        self.accepted_final_text = None
        self.pending_question = None
        self.user_declined_mutation = False
        self.declared_mutation = None
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
        # The deterministic skeleton drives the phases and the tool policy, but it
        # is never surfaced as a plan: the sidebar is reserved for the model-authored
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
                await self.event_bus.emit(
                    "planning.evidence.recorded",
                    record.compact(),
                    source="core.planner",
                )
        self.accepted_final_text = None
        # Per-turn like everything above it, and missing here was a trap: a
        # refusal in the previous turn kept governing this one, so a user who
        # said "no" once and then "continue" got an agent that still believed
        # it must not touch files - and said so instead of working.
        self.user_declined_mutation = False
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
            await self.submit_agent_plan(
                payload.get("steps"), changes_workspace=payload.get("changes_workspace")
            )
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
        # The model-authored checklist is *not* advanced here: that cursor is
        # driven solely by the model via complete_plan_step, so the sidebar
        # reflects real progress instead of racing one step ahead per piece of
        # evidence.
        await self._advance_after_evidence(records, tool_name=tool_name, payload=payload)
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
        # No gate asks "were you asked to write files": whether a task changes
        # the workspace is the model's call (submit_plan's changes_workspace, or
        # simply doing it), not the keyword classifier's. Rejecting a write
        # because the request text did not look like a mutation was the round
        # trip the model kept losing to its own runtime.
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
        the task context point the model at commands for it instead of at
        pointless workspace files, and lets the completion summary name it.
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
        deliverable from here on (read-only context rules, prose streams as the
        answer), but the model may still mutate later in the turn if the user
        asks - the advisory gates keep working.
        """
        if not self.enabled or self.user_declined_mutation:
            return
        if not self.profile:
            return
        if not (
            self.profile.requires_workspace_mutation or self._task_changes_workspace()
        ):
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
        paths are treated as internal.
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
        """Whether the task's deliverable right now is tool work, not prose.

        Only the model's own declaration makes it so: it said the task changes
        the workspace, so its prose is working commentary rather than the
        answer. A keyword guess never does - a question that happens to
        mention "implement" streams its prose as the answer.
        """
        if not (self.enabled and self.profile and self.declared_mutation is True):
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
            and not self._task_changes_workspace()
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
                "Task checklist: none yet. If this task takes more than two or "
                "three actions, call submit_plan with the concrete ordered steps "
                "in the same response as your first real action (it runs first; "
                "alone, it costs a round trip); a small task needs no checklist, "
                "just do the work. When "
                "you do submit, set changes_workspace to say whether the task "
                "changes files (true) or ends in an answer (false) - that "
                "decision is yours, not the runtime's. The steps are shown to "
                "the user as the live checklist, so make them specific to this "
                "request.\n"
            )
        else:
            agent_current = self.agent_plan.current_step
            current_label = agent_current.title if agent_current else "done"
            plan_lines = (
                f"Task checklist: {self.agent_plan.snapshot()['progress']} "
                f"(current: {current_label}).\n"
                "This checklist position reflects only the steps you have reported "
                "finishing. As soon as a result shows the current step is done, "
                "call complete_plan_step with its title in the same response as "
                "your next action - never in a response of its own, and never "
                "saved for the end of the task. Call submit_plan again only if "
                "your approach genuinely changes.\n"
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
                "create or edit workspace files in their place.\n"
                if self.external_targets
                else ""
            )
            + f"Latest verification passed: {self.ledger.latest_verification_passed}\n"
            + (
                "Approved external gaps: "
                f"{[gap.to_dict() for gap in self.approved_external_gaps]}\n"
                if self.approved_external_gaps
                else ""
            )
            + self._recent_evidence_lines()
            + f"Recommended tools now: {sorted(recommended_tool_names)}\n"
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
                "- Investigate first with read-only tools, starting with "
                "search_index to locate the code and read_file to study what it "
                "points at (search_code for exact literals, list_files for a "
                "directory), until you genuinely understand the task and the "
                "code it touches. Do not guess.\n"
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
            origin = (
                "your declaration"
                if self.declared_mutation is True
                else "observed: the workspace already changed"
            )
            return header + (
                f"Task kind: changes the workspace ({origin}).\n"
                "Rules: prefer the recommended tools and work on the current step. Make "
                "workspace changes with write_file or edit_code, not as code in the chat; "
                "when the work is done, call complete_task with a summary. If you conclude "
                "the task needs no workspace change after all, call submit_plan again "
                "with changes_workspace=false and answer in the chat."
            )
        if self.declared_mutation is False or self.user_declined_mutation:
            # The model said so (or the user refused the change): the prose
            # answer IS the deliverable. Told "do not claim completion from
            # prose" while holding only reading evidence, models invent a
            # deliverable - typically an unrequested notes/summary document
            # written at the end just to have "completion evidence".
            origin = (
                "the user declined the change"
                if self.user_declined_mutation
                else "your declaration"
            )
            return header + (
                f"READ-ONLY TASK - the deliverable is your answer in the chat ({origin}).\n"
                "Rules:\n"
                "- Gather evidence with the recommended read-only tools and keep the "
                "analysis internal.\n"
                "- When you have enough evidence, answer the user directly in the chat. "
                "Your prose answer completes this task; calling complete_task is not "
                "required.\n"
                "- Nobody asked for a document: put notes, summaries or analysis in "
                "your answer, not in a file. If the task genuinely turns out to need "
                "a file change, make it and verify it - the runtime follows what you do."
            )
        # Undeclared: the keyword guess is shown as a hint, never as a demand.
        # The model decides; until it does, nothing is required of it beyond
        # working honestly with the evidence.
        hint = (
            "looks like a workspace change"
            if self.profile.requires_workspace_mutation
            else "looks like a question"
        )
        return header + (
            "Task kind: not declared yet - you decide. Set changes_workspace in "
            "submit_plan: true if this task creates or edits files, false if your "
            "answer in the chat is the deliverable. Surface hint from the request "
            f"text: {hint} (only a hint).\n"
            "Rules until you declare: gather evidence with the recommended tools. "
            "If you change files, verify the change before finishing; if the "
            "deliverable is an answer, write it in the chat - no completion call "
            "and no notes file needed."
        )

    def _task_produces_workspace_effects(self) -> bool:
        """Whether this task's deliverable lives in the workspace.

        A task the model declared as a workspace change (or has already changed)
        legitimately ends in tool actions and a complete_task claim. Everything
        else ends in a chat answer, and the task context must say so. The
        user's own denial still wins - that is a decision, not a misreading.
        """
        if self.user_declined_mutation:
            return False
        return self._task_changes_workspace()

    def _task_changes_workspace(self) -> bool:
        """Whether this task is about changing the workspace, per the model.

        Two sources, and only two: the model's declaration through submit_plan,
        and what the model actually did (a recorded file change, by file tool or
        by shell). The keyword classifier's guess is deliberately absent - it is
        a hint for the skeleton and the tool recommendations, never a fact the
        runtime holds the model to.
        """
        if self.profile is None:
            return True  # fail toward the stricter, action-oriented rules
        return self.declared_mutation is True or self._has_workspace_change_evidence()

    def _effective_profile(self) -> TaskProfile | None:
        """The task profile as the runtime's demands actually see it.

        The surface classifier reads keywords in the first message; the model
        declares what the task is, and the ledger records what it did. Where
        the keyword label disagrees with either, the model wins, in both
        directions:

        - No declaration and no change *downgrades* a keyword mutation label:
          nothing is demanded of a task the model never said was a change.
        - A user denial mid-turn downgrades it too (see
          :meth:`note_user_denial`).
        - A declared or observed workspace change *upgrades* a task the
          classifier read as conversation or inspection, so the change gets the
          verification discipline it deserves.

        Every consumer that steers or gates on the label - the task context
        block, the tool policy - sees this corrected view.
        A declaration also rewrites :attr:`profile` itself (see
        :meth:`_declare_workspace_mutation`), so the skeleton follows.
        """
        if self.profile is None:
            return None
        wants_mutation = self._task_produces_workspace_effects()
        if wants_mutation == self.profile.requires_workspace_mutation:
            return self.profile
        if wants_mutation:
            return _profile_with_mutation(self.profile)
        return _profile_without_mutation(self.profile)

    async def _declare_workspace_mutation(self, changes_workspace: bool) -> None:
        """Adopt the model's decision on whether this task changes the workspace.

        When the decision contradicts the keyword guess, the profile and the
        deterministic skeleton are rebuilt around it, and the new skeleton is
        fast-forwarded through whatever the ledger already proves (a listing or
        a read settles inspection; a recorded change settles implementation).
        The evidence ledger and the model-authored checklist are untouched.
        """
        self.declared_mutation = changes_workspace
        if self.profile is None or self.plan is None:
            return
        await self.event_bus.emit(
            "planning.task.declared",
            {
                "changes_workspace": changes_workspace,
                "surface_guess": self.profile.requires_workspace_mutation,
            },
            source="core.planner",
        )
        if self.profile.requires_workspace_mutation == changes_workspace:
            return
        self.profile = (
            _profile_with_mutation(self.profile)
            if changes_workspace
            else _profile_without_mutation(self.profile)
        )
        self.plan = ExecutionPlan.for_profile(
            self.profile, max_steps=self.config.max_plan_steps
        )
        await self._emit_phase(
            PlanningPhase.DISCOVER_LOCAL
            if self.profile.requires_local_context
            else PlanningPhase.EXECUTE
        )
        await self._mark_current_step_started()
        await self._fast_forward_skeleton()

    async def _fast_forward_skeleton(self) -> None:
        """Settle rebuilt skeleton steps the ledger already has evidence for."""
        if self.plan is None:
            return
        if self.ledger.has_success(
            EvidenceType.WORKSPACE_LISTED,
            EvidenceType.FILE_READ,
            EvidenceType.LOCAL_SEARCH_MATCH,
            EvidenceType.LOCAL_SEARCH_COMPLETED,
            EvidenceType.DISCOVERY_COMPLETED,
        ):
            await self._complete_step_if_kind(PlanStepKind.INSPECT_LOCAL)
        if not self._has_workspace_change_evidence():
            return
        if not any(step.kind == PlanStepKind.IMPLEMENT for step in self.plan.steps):
            return
        await self._complete_step_by_kind(PlanStepKind.IMPLEMENT)
        if self.ledger.latest_verification_passed:
            await self._complete_step_by_kind(PlanStepKind.VERIFY)
            await self._move_to_step_kind(PlanStepKind.COMPLETE, PlanningPhase.COMPLETE)
        else:
            await self._move_to_step_kind(PlanStepKind.VERIFY, PlanningPhase.VERIFY)

    def _recent_evidence_lines(self) -> str:
        # One line per record: the block is re-sent every step, and the tool
        # results in the history already hold the detail.
        lines = self.ledger.brief_recent(limit=8)
        if not lines:
            return "Recent evidence: none\n"
        return "Recent evidence:\n" + "".join(f"- {line}\n" for line in lines)

    def _known_paths_preview(self, *, limit: int = 15) -> str:
        paths = sorted(self._known_content_paths)
        if len(paths) <= limit:
            return str(paths)
        shown = paths[:limit]
        return f"{shown} (+{len(paths) - limit} more)"

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

        await self._accept_success_completion(claim)
        return CompletionDecision(
            accepted=True,
            outcome="success",
            final_text=self.accepted_final_text or claim.summary,
        )

    def _claim_validity_gaps(self, claim: CompletionClaim) -> list[str]:
        """Structural problems with the claim itself.

        These are practically unreachable through the normal tool flow (the tool
        already rejects an empty summary; objectives are set once per turn).
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
        if self._task_produces_workspace_effects():
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

    async def submit_agent_plan(
        self, steps: object, *, changes_workspace: object = None
    ) -> None:
        """Adopt the model-authored steps and reveal the task sidebar.

        The first submission emits ``planning.plan.created`` (the panel appears);
        a later submission emits ``planning.plan.revised`` (the model corrected or
        re-scoped its plan). Steps with no usable title are ignored.

        ``changes_workspace`` is the model's decision on the nature of the task
        and is honoured before the steps: it is what the runtime's demands key
        off from here on (see :meth:`_declare_workspace_mutation`). Absent, the
        task stays undeclared and nothing is demanded either way.
        """
        if isinstance(changes_workspace, bool):
            await self._declare_workspace_mutation(changes_workspace)
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

    async def settle_agent_plan_on_final_answer(self) -> None:
        """Complete the checklist when a turn ends cleanly in a final answer.

        ``complete_all`` also runs when a ``complete_task`` claim is accepted,
        but most turns end the other way: the model stops calling tools and
        answers. That answer is the end of the task as far as the model is
        concerned - the same role ``complete_task`` plays - so the checklist
        settles on it, whatever kind of task it was and however many steps the
        model remembered to report. Leaving the plan ACTIVE here put the sidebar
        at "paused, waiting for you" after a delivered answer, with nothing
        actually pending; models routinely finish without declaring the closing
        step.

        Only the clean no-tool final-answer path calls this. A blocking
        question raised via ``ask_user``, a cancellation or a wind-down suspend
        the plan instead, and those are the turns that genuinely wait on the
        user.
        """
        plan = self.agent_plan
        if plan is None or plan.status != PlanStatus.ACTIVE:
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
        code_changed = self.ledger.command_mutated_workspace or changes_require_verification(
            changed_paths
        )
        if not code_changed:
            return "No verification evidence was required."
        if not self.project_verification().has_any:
            return (
                "Warning: no automated test/build system was detected in this "
                "project, so the change was not automatically verified."
            )
        return "Not verified: no passing check covers the current changes."

    async def _accept_success_completion(self, claim: CompletionClaim) -> None:
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
        # The claim's own disclosures reach the user too: what the model reports
        # as still open must not vanish from the final message.
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
                    remaining_line,
                    limitations_line,
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


# File suffixes whose changes carry no executable behaviour, so there is nothing
# meaningful to verify (no test/command applies).
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


def changes_require_verification(paths: list[str]) -> bool:
    """Whether a set of changed paths warrants verification evidence.

    Pure documentation/prose edits (e.g. a ``.md`` progress tracker) have nothing
    to run or assert against, so they do not require verification. Any path that
    is not clearly documentation counts - a mixed change still needs
    verification.
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


# Path-like tokens in free text: "~", "~/...", or an absolute "/..." segment.
# The lookbehind keeps URL fragments ("https://host/x"), protocol-relative
# references and word-internal slashes ("e/ou") from being misread as paths.
_PATH_TOKEN_RE = re.compile(r"(?<![:\w/])(~(?:/[\w.+-][\w./+-]*)?|/[\w.+-][\w./+-]*)")


def _external_path_targets(text: str, workspace: Path | None) -> tuple[str, ...]:
    """Path-like tokens in the objective that resolve outside the workspace root.

    Purely a *signal*, never a hard classification: it steers the task context
    toward commands for these targets and names them in the completion summary.
    With no workspace configured nothing is "outside", and unresolvable tokens
    are ignored, so a false positive costs nothing and a miss degrades to the
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


_MUTATION_INTENTS = frozenset(
    {TaskIntent.IMPLEMENTATION, TaskIntent.BUG_FIX, TaskIntent.REFACTORING}
)


def _profile_with_mutation(profile: TaskProfile) -> TaskProfile:
    """The profile as a workspace-change task, whatever the keywords said.

    Upgrading costs nothing the model's decision does not already carry: the
    change will exist (or does), and the only new demand is verification.
    """
    criteria = [
        criterion
        for criterion in profile.acceptance_criteria
        if criterion not in {CRITERION_APPLY_VIA_TOOLS, CRITERION_VERIFY_AFTER_MUTATION}
    ]
    return profile.model_copy(
        update={
            "intent": (
                profile.intent
                if profile.intent in _MUTATION_INTENTS
                else TaskIntent.IMPLEMENTATION
            ),
            "requires_workspace_mutation": True,
            "requires_local_context": True,
            "requires_verification": True,
            "allows_web_first": False,
            "acceptance_criteria": [
                *criteria,
                CRITERION_APPLY_VIA_TOOLS,
                CRITERION_VERIFY_AFTER_MUTATION,
            ],
        }
    )


def _profile_without_mutation(profile: TaskProfile) -> TaskProfile:
    """The profile as a prose deliverable: nothing in the workspace is owed."""
    criteria = [
        criterion
        for criterion in profile.acceptance_criteria
        if criterion not in {CRITERION_APPLY_VIA_TOOLS, CRITERION_VERIFY_AFTER_MUTATION}
    ]
    return profile.model_copy(
        update={
            "intent": (
                TaskIntent.LOCAL_INSPECTION
                if profile.intent in _MUTATION_INTENTS
                else profile.intent
            ),
            "requires_workspace_mutation": False,
            "requires_verification": profile.intent == TaskIntent.COMMAND_EXECUTION,
            "acceptance_criteria": criteria,
        }
    )


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
