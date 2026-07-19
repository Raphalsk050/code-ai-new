"""Risk-proportional completion gating.

The completion gate decides whether a success ``complete_task`` claim is backed
by enough evidence. Requirements are proportional to observed risk instead of
uniform: a misclassified analysis task is released after one nudge, a routine
single-file change pays no double-check tax, and only genuinely risky changes
(many files touched, a failed verification this turn, complex tasks) get the
full reconciliation checklist.

Everything here is pure decision logic over an immutable ``CompletionContext``
snapshot: no events, no planner state, no I/O. ``PlannerService`` builds the
context, owns all side effects, and delegates only the judgement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from code_ai.core.planning.models import CompletionClaim, TaskComplexity, TaskProfile

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

# Reconciliation checklist appended to a completion rejection when the
# double-check applies. Always folded into the same rejection as any missing
# evidence so it never costs a second round-trip on its own.
DOUBLE_CHECK_CHECKLIST: tuple[str, ...] = (
    "Double-check required before successful completion.",
    "Reconcile every acceptance criterion with actual evidence.",
    "Confirm verification still reflects the current workspace state.",
    "Call complete_task again with double_check_acknowledged=true after "
    "reconciling the evidence.",
)

# Touching this many files (or more) in one turn is the risk signal that makes
# the reconciliation double-check worth its round-trip.
STRICT_CHANGED_PATHS_THRESHOLD = 3


def changes_require_verification(paths: list[str]) -> bool:
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


@dataclass(frozen=True, slots=True)
class CompletionContext:
    """Immutable snapshot of everything a completion policy may judge.

    Built by ``PlannerService`` from the evidence ledger, the task profile and
    the checklists, so policies never reach back into mutable planner state.
    """

    claim: CompletionClaim
    profile: TaskProfile
    changed_paths: tuple[str, ...]
    has_file_change: bool
    # Any write-shaped action tried this turn, even an unsuccessful one. Its
    # absence is the reclassification signal: the model never treated the task
    # as a mutation, whatever the surface classifier said.
    write_attempted: bool
    # Successful knowledge-gathering evidence (reads, listings, searches, web).
    has_analysis_evidence: bool
    # Verification settled for the current change set (passed, not required, or
    # not applicable to the changed paths).
    verified: bool
    verification_failed_this_turn: bool
    external_targets: tuple[str, ...]
    has_external_action_evidence: bool
    # Claimed changed paths with no recorded change evidence (the fabrication
    # direction; honest subset claims are filtered out by the service).
    phantom_claimed_paths: tuple[str, ...]
    # Model-authored checklist steps still pending; empty when no plan was
    # submitted. The skeleton list is populated only in that fallback case.
    pending_declared_steps: tuple[str, ...]
    incomplete_skeleton_steps: tuple[str, ...]
    double_check_enabled: bool
    double_check_pending: bool


@dataclass(frozen=True, slots=True)
class GateVerdict:
    missing_requirements: tuple[str, ...] = ()
    policy_name: str = "standard"
    double_check_requested: bool = False
    acceptance_note: str = ""

    @property
    def accepted(self) -> bool:
        return not self.missing_requirements


class CompletionEvidencePolicy(Protocol):
    """What one completion-evidence regime demands before accepting a claim."""

    name: str

    def missing_requirements(self, context: CompletionContext) -> list[str]: ...

    def requires_double_check(self, context: CompletionContext) -> bool: ...

    def acceptance_note(self, context: CompletionContext) -> str: ...


class MinimalCompletionPolicy:
    """Release a mutation-labelled task that was really an analysis.

    Selected only after at least one rejection already nudged the model toward
    file-change evidence and it insisted, having attempted no write and gathered
    real analysis evidence. At that point the classifier label is the only thing
    demanding workspace evidence, and holding the turn hostage to it pushes
    models to fabricate files. The prose summary is the deliverable.
    """

    name = "minimal"

    def missing_requirements(self, context: CompletionContext) -> list[str]:
        return []

    def requires_double_check(self, context: CompletionContext) -> bool:
        return False

    def acceptance_note(self, context: CompletionContext) -> str:
        return (
            "Note: the task was classified as a workspace change, but no change "
            "was attempted; the summary above stands as the delivered answer."
        )


class StandardCompletionPolicy:
    """Evidence requirements for ordinary mutations, without the double-check."""

    name = "standard"

    def missing_requirements(self, context: CompletionContext) -> list[str]:
        missing: list[str] = []
        # A task the surface classifier labelled a mutation must show file-change
        # evidence before completing. Exception: a mutation whose target lives
        # *outside* the workspace cannot produce workspace file-change evidence
        # (file tools are workspace-bound), so demanding it only pushes the model
        # to fabricate pointless workspace files. Command/terminal evidence is
        # the honest currency there.
        if context.profile.requires_workspace_mutation and not context.has_file_change:
            if not context.external_targets:
                missing.append("no successful file-change evidence exists.")
            elif not context.has_external_action_evidence:
                missing.append(
                    "the requested change targets paths outside the workspace "
                    f"({sorted(context.external_targets)}); apply it with "
                    "execute_command (file tools are workspace-only) instead of "
                    "creating workspace files to produce evidence."
                )
        # Independently, *any* task that actually changed files must be verified
        # before completing — that catches mutations the keyword classifier
        # missed, so the gate keys off real evidence, not the label.
        if context.has_file_change and not context.verified:
            missing.append("no current successful verification evidence exists.")
        if context.phantom_claimed_paths:
            missing.append(
                f"claimed changed paths {sorted(context.phantom_claimed_paths)} "
                "have no recorded change evidence (recorded paths: "
                f"{sorted(context.changed_paths)})."
            )
        # Checklist reconciliation is guidance, not evidence: pending steps are
        # listed only alongside a genuine evidence gap, to point the model back
        # at its own plan. A lagging cursor must never cost a round-trip on its
        # own — acceptance settles the checklist anyway.
        if missing and not _settled(context):
            missing.extend(_checklist_debt(context))
        return missing

    def requires_double_check(self, context: CompletionContext) -> bool:
        return False

    def acceptance_note(self, context: CompletionContext) -> str:
        return ""


class StrictCompletionPolicy(StandardCompletionPolicy):
    """Standard requirements plus the reconciliation double-check.

    Selected only when a real risk signal exists, so the double-check's extra
    round-trip is spent where reconciliation can actually catch something.
    """

    name = "strict"

    def requires_double_check(self, context: CompletionContext) -> bool:
        return bool(
            context.double_check_enabled
            and not context.double_check_pending
            and not context.claim.double_check_acknowledged
        )


def _settled(context: CompletionContext) -> bool:
    """Whether the mutation's outcome is already fully evidenced.

    A settled change (verified workspace change, or an external target applied
    via commands) must not be re-blocked by checklist debt.
    """
    if not context.profile.requires_workspace_mutation:
        return False
    if context.has_file_change:
        return context.verified
    return bool(context.external_targets and context.has_external_action_evidence)


def _checklist_debt(context: CompletionContext) -> list[str]:
    if context.pending_declared_steps:
        return [
            "declared plan steps not yet done: "
            f"{list(context.pending_declared_steps)}."
        ]
    if context.incomplete_skeleton_steps:
        return [
            "required plan steps are incomplete: "
            f"{list(context.incomplete_skeleton_steps)}."
        ]
    return []


_MINIMAL_POLICY = MinimalCompletionPolicy()
_STANDARD_POLICY = StandardCompletionPolicy()
_STRICT_POLICY = StrictCompletionPolicy()


def _is_reclassification_signal(context: CompletionContext) -> bool:
    return bool(
        context.profile.requires_workspace_mutation
        and not context.write_attempted
        and not context.has_file_change
        and not context.external_targets
        and context.has_analysis_evidence
    )


def _is_high_risk(context: CompletionContext) -> bool:
    return bool(
        len(context.changed_paths) >= STRICT_CHANGED_PATHS_THRESHOLD
        or context.verification_failed_this_turn
        or context.profile.complexity == TaskComplexity.COMPLEX
    )


def select_policy(
    context: CompletionContext, *, prior_rejections: int
) -> CompletionEvidencePolicy:
    """Pick the evidence regime this claim must satisfy.

    Minimal fires only on the reclassification signal *after* a rejection
    already nudged the model once, mirroring the precondition gates' "nudge at
    most once, then fail open" principle. Strict fires only on real risk
    signals; everything else pays the standard evidence cost and nothing more.
    """
    if _is_reclassification_signal(context) and prior_rejections >= 1:
        return _MINIMAL_POLICY
    if context.double_check_enabled and _is_high_risk(context):
        return _STRICT_POLICY
    return _STANDARD_POLICY


class CompletionGate:
    """Judges success completion claims against the selected evidence policy.

    Stateless apart from the rejection counter, which tracks consecutive
    rejections issued while the planner's progress fingerprint stayed frozen —
    i.e. the model re-claimed completion without producing anything new. That
    counter paces policy selection (Minimal needs one prior nudge); the fail-open
    release built on top of it never lets a wrong heuristic trap a turn.
    """

    def __init__(self) -> None:
        self._rejections_without_progress = 0
        self._fingerprint_at_last_rejection: object | None = None

    def reset(self) -> None:
        self._rejections_without_progress = 0
        self._fingerprint_at_last_rejection = None

    def evaluate(
        self, context: CompletionContext, *, progress_fingerprint: object
    ) -> GateVerdict:
        policy = select_policy(
            context, prior_rejections=self._prior_rejections(progress_fingerprint)
        )
        missing = policy.missing_requirements(context)
        double_check_requested = policy.requires_double_check(context)
        if double_check_requested:
            # Folded into the same rejection as any missing evidence, so the
            # double-check costs at most one round-trip in total.
            missing = [*missing, *DOUBLE_CHECK_CHECKLIST]
        if not missing:
            self.reset()
            return GateVerdict(
                policy_name=policy.name,
                acceptance_note=policy.acceptance_note(context),
            )
        self._note_rejection(progress_fingerprint)
        return GateVerdict(
            missing_requirements=tuple(missing),
            policy_name=policy.name,
            double_check_requested=double_check_requested,
        )

    def _prior_rejections(self, progress_fingerprint: object) -> int:
        # Progress since the last rejection restarts the pacing: the model is
        # still moving, so it gets the full guidance again rather than a
        # fast-track to the lenient policy.
        if progress_fingerprint != self._fingerprint_at_last_rejection:
            return 0
        return self._rejections_without_progress

    def _note_rejection(self, progress_fingerprint: object) -> None:
        if progress_fingerprint != self._fingerprint_at_last_rejection:
            self._fingerprint_at_last_rejection = progress_fingerprint
            self._rejections_without_progress = 1
            return
        self._rejections_without_progress += 1
