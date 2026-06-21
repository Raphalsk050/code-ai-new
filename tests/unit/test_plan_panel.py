from __future__ import annotations

from code_ai.events.models import EventEnvelope
from code_ai.ui.terminal.view_models import TerminalViewModel
from code_ai.ui.terminal.widgets import build_plan_steps, plan_is_active, render_plan


def _event(event_type: str, payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event_type, session_id="test", sequence=0, payload=payload
    )


def _snapshot(**overrides) -> dict[str, object]:
    payload = {
        "status": "ACTIVE",
        "progress": "1/3",
        "current_step": "Implement reference file",
        "current_step_status": "IN_PROGRESS",
        "completed_steps": ["Discover local context"],
        "remaining_steps": ["Implement reference file", "Verify and report"],
    }
    payload.update(overrides)
    return payload


def test_build_plan_steps_orders_and_flags_states() -> None:
    steps = build_plan_steps(_snapshot())
    assert [s["status"] for s in steps] == ["done", "running", "pending"]
    assert steps[0]["title"] == "Discover local context"
    assert steps[1]["title"] == "Implement reference file"


def test_build_plan_steps_marks_failed_current() -> None:
    steps = build_plan_steps(_snapshot(current_step_status="FAILED"))
    running = [s for s in steps if s["title"] == "Implement reference file"][0]
    assert running["status"] == "failed"


def test_plan_is_active_only_for_active_status() -> None:
    assert plan_is_active(_snapshot()) is True
    assert plan_is_active(_snapshot(status="COMPLETED")) is False
    assert plan_is_active({}) is False


def test_render_plan_includes_markers_and_titles() -> None:
    steps = build_plan_steps(_snapshot())
    rendered = render_plan(steps, "1/3", "ACTIVE", "|", "#ff9f1c").plain
    assert "✓ Discover local context" in rendered
    assert "| Implement reference file" in rendered
    assert "○ Verify and report" in rendered
    assert "executando" in rendered
    assert "1/3" in rendered


def test_view_model_shows_plan_on_created_and_hides_on_final() -> None:
    vm = TerminalViewModel()
    vm.apply(_event("planning.plan.created", _snapshot()))
    assert vm.plan_visible is True
    assert len(vm.plan_steps) == 3

    vm.apply(_event("assistant.final", {"text": "done"}))
    assert vm.plan_visible is False
    # Steps are retained (just hidden) so a re-show needs no rebuild.
    assert len(vm.plan_steps) == 3


def test_view_model_hides_plan_when_turn_returns_to_ready() -> None:
    # Answer-only turns never emit assistant.final; returning to READY must
    # still collapse the panel.
    vm = TerminalViewModel()
    vm.apply(_event("planning.plan.created", _snapshot()))
    assert vm.plan_visible is True
    vm.apply(_event("status.changed", {"state": "READY"}))
    assert vm.plan_visible is False


def test_view_model_hides_plan_when_status_not_active() -> None:
    vm = TerminalViewModel()
    vm.apply(_event("planning.plan.created", _snapshot()))
    vm.apply(_event("planning.step.completed", _snapshot(status="COMPLETED")))
    assert vm.plan_visible is False
