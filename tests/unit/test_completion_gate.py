from __future__ import annotations

from code_ai.core.planning.completion import (
    CompletionContext,
    CompletionGate,
    MinimalCompletionPolicy,
    StandardCompletionPolicy,
    StrictCompletionPolicy,
    select_policy,
)
from code_ai.core.planning.models import CompletionClaim, TaskComplexity, TaskProfile


def make_context(**overrides: object) -> CompletionContext:
    """A settled single-file mutation context; overrides shape each scenario."""
    profile_overrides = overrides.pop("profile", None)
    profile = profile_overrides or TaskProfile.from_user_text("Create src/example.py")
    claim = overrides.pop("claim", None) or CompletionClaim(summary="done")
    defaults: dict[str, object] = {
        "claim": claim,
        "profile": profile,
        "changed_paths": ("src/example.py",),
        "has_file_change": True,
        "write_attempted": True,
        "has_analysis_evidence": True,
        "verified": True,
        "verification_failed_this_turn": False,
        "external_targets": (),
        "has_external_action_evidence": False,
        "phantom_claimed_paths": (),
        "pending_declared_steps": (),
        "incomplete_skeleton_steps": (),
        "double_check_enabled": True,
        "double_check_pending": False,
    }
    defaults.update(overrides)
    return CompletionContext(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# StandardCompletionPolicy
# --------------------------------------------------------------------------- #


def test_standard_accepts_settled_single_file_mutation() -> None:
    policy = StandardCompletionPolicy()

    assert policy.missing_requirements(make_context()) == []
    assert policy.requires_double_check(make_context()) is False


def test_standard_requires_file_change_for_mutation() -> None:
    context = make_context(
        has_file_change=False, write_attempted=False, changed_paths=()
    )

    missing = StandardCompletionPolicy().missing_requirements(context)

    assert any("file-change" in item for item in missing)


def test_standard_points_external_mutation_at_execute_command() -> None:
    context = make_context(
        has_file_change=False,
        write_attempted=False,
        changed_paths=(),
        external_targets=("~/.zshrc",),
        has_external_action_evidence=False,
    )

    missing = StandardCompletionPolicy().missing_requirements(context)

    assert any("execute_command" in item for item in missing)
    assert all("file-change evidence" not in item for item in missing)


def test_standard_accepts_external_mutation_on_action_evidence() -> None:
    context = make_context(
        has_file_change=False,
        write_attempted=False,
        changed_paths=(),
        external_targets=("~/.zshrc",),
        has_external_action_evidence=True,
    )

    assert StandardCompletionPolicy().missing_requirements(context) == []


def test_standard_requires_verification_for_unverified_change() -> None:
    context = make_context(verified=False)

    missing = StandardCompletionPolicy().missing_requirements(context)

    assert any("verification" in item for item in missing)


def test_standard_rejects_phantom_claimed_paths() -> None:
    context = make_context(phantom_claimed_paths=("src/ghost.py",))

    missing = StandardCompletionPolicy().missing_requirements(context)

    assert any("src/ghost.py" in item for item in missing)


def test_standard_lists_checklist_debt_only_with_an_evidence_gap() -> None:
    policy = StandardCompletionPolicy()
    # Evidence gap present: the pending steps ride along as guidance.
    with_gap = make_context(
        verified=False, pending_declared_steps=("Run tests", "Announce")
    )
    assert any(
        "declared plan steps" in item for item in policy.missing_requirements(with_gap)
    )
    # Same pending steps, evidence settled: the checklist never blocks alone.
    settled = make_context(pending_declared_steps=("Run tests", "Announce"))
    assert policy.missing_requirements(settled) == []


def test_standard_falls_back_to_skeleton_checklist_without_declared_plan() -> None:
    context = make_context(
        has_file_change=False,
        write_attempted=False,
        changed_paths=(),
        incomplete_skeleton_steps=("Implement the change",),
    )

    missing = StandardCompletionPolicy().missing_requirements(context)

    assert any("required plan steps" in item for item in missing)


# --------------------------------------------------------------------------- #
# StrictCompletionPolicy
# --------------------------------------------------------------------------- #


def test_strict_requests_double_check_until_pending_or_acknowledged() -> None:
    policy = StrictCompletionPolicy()

    assert policy.requires_double_check(make_context()) is True
    assert policy.requires_double_check(make_context(double_check_pending=True)) is False
    acknowledged = make_context(
        claim=CompletionClaim(summary="done", double_check_acknowledged=True)
    )
    assert policy.requires_double_check(acknowledged) is False
    disabled = make_context(double_check_enabled=False)
    assert policy.requires_double_check(disabled) is False


def test_strict_keeps_standard_evidence_requirements() -> None:
    context = make_context(verified=False)

    missing = StrictCompletionPolicy().missing_requirements(context)

    assert any("verification" in item for item in missing)


# --------------------------------------------------------------------------- #
# MinimalCompletionPolicy
# --------------------------------------------------------------------------- #


def test_minimal_requires_nothing_and_notes_the_reclassification() -> None:
    policy = MinimalCompletionPolicy()
    context = make_context(
        has_file_change=False, write_attempted=False, changed_paths=()
    )

    assert policy.missing_requirements(context) == []
    assert policy.requires_double_check(context) is False
    assert "no change was attempted" in policy.acceptance_note(context)


# --------------------------------------------------------------------------- #
# select_policy
# --------------------------------------------------------------------------- #


def test_select_policy_defaults_to_standard() -> None:
    assert select_policy(make_context(), prior_rejections=0).name == "standard"


def test_select_policy_goes_strict_on_many_changed_paths() -> None:
    context = make_context(changed_paths=("a.py", "b.py", "c.py"))

    assert select_policy(context, prior_rejections=0).name == "strict"


def test_select_policy_goes_strict_after_a_failed_verification() -> None:
    context = make_context(verification_failed_this_turn=True)

    assert select_policy(context, prior_rejections=0).name == "strict"


def test_select_policy_goes_strict_for_complex_tasks() -> None:
    profile = TaskProfile.from_user_text("Create src/example.py").model_copy(
        update={"complexity": TaskComplexity.COMPLEX}
    )

    assert select_policy(make_context(profile=profile), prior_rejections=0).name == "strict"


def test_select_policy_respects_the_double_check_switch() -> None:
    context = make_context(
        changed_paths=("a.py", "b.py", "c.py"), double_check_enabled=False
    )

    assert select_policy(context, prior_rejections=0).name == "standard"


def test_select_policy_needs_one_nudge_before_minimal() -> None:
    reclassified = make_context(
        has_file_change=False, write_attempted=False, changed_paths=()
    )

    assert select_policy(reclassified, prior_rejections=0).name == "standard"
    assert select_policy(reclassified, prior_rejections=1).name == "minimal"


def test_select_policy_never_goes_minimal_after_a_write_attempt() -> None:
    # A failed write is a mutation in progress, not a misclassified analysis.
    attempted = make_context(
        has_file_change=False, write_attempted=True, changed_paths=()
    )

    assert select_policy(attempted, prior_rejections=5).name == "standard"


def test_select_policy_never_goes_minimal_without_analysis_evidence() -> None:
    # A model that did nothing at all keeps being asked for real evidence.
    idle = make_context(
        has_file_change=False,
        write_attempted=False,
        changed_paths=(),
        has_analysis_evidence=False,
    )

    assert select_policy(idle, prior_rejections=5).name == "standard"


# --------------------------------------------------------------------------- #
# CompletionGate pacing
# --------------------------------------------------------------------------- #


def test_gate_folds_double_check_into_the_evidence_rejection() -> None:
    gate = CompletionGate()
    context = make_context(verified=False, changed_paths=("a.py", "b.py", "c.py"))

    verdict = gate.evaluate(context, progress_fingerprint=("fp", 1))

    assert verdict.accepted is False
    assert verdict.double_check_requested is True
    assert any("verification" in item for item in verdict.missing_requirements)
    assert any("Double-check" in item for item in verdict.missing_requirements)


def test_gate_releases_reclassified_task_after_one_nudge() -> None:
    gate = CompletionGate()
    context = make_context(
        has_file_change=False, write_attempted=False, changed_paths=()
    )

    first = gate.evaluate(context, progress_fingerprint=("fp", 1))
    assert first.accepted is False

    second = gate.evaluate(context, progress_fingerprint=("fp", 1))
    assert second.accepted is True
    assert second.policy_name == "minimal"
    assert "no change was attempted" in second.acceptance_note


def test_gate_restarts_pacing_when_progress_resumes() -> None:
    gate = CompletionGate()
    context = make_context(
        has_file_change=False, write_attempted=False, changed_paths=()
    )

    assert gate.evaluate(context, progress_fingerprint=("fp", 1)).accepted is False
    # New evidence arrived between the claims: the model is still moving, so it
    # gets the standard guidance again instead of the lenient release.
    verdict = gate.evaluate(context, progress_fingerprint=("fp", 2))

    assert verdict.accepted is False
    assert verdict.policy_name == "standard"


def test_gate_resets_counter_on_acceptance() -> None:
    gate = CompletionGate()
    rejected = make_context(
        has_file_change=False, write_attempted=False, changed_paths=()
    )
    assert gate.evaluate(rejected, progress_fingerprint=("fp", 1)).accepted is False
    assert gate.evaluate(make_context(), progress_fingerprint=("fp", 2)).accepted is True

    # A later claim starts pacing from scratch.
    assert gate.evaluate(rejected, progress_fingerprint=("fp", 3)).accepted is False
    assert gate.evaluate(rejected, progress_fingerprint=("fp", 3)).accepted is True
