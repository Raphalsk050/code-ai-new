from __future__ import annotations

from code_ai.events.models import EventEnvelope
from code_ai.ui.terminal.view_models import TerminalViewModel
from code_ai.ui.terminal.widgets import render_subagents


def _event(event_type: str, payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event_type, session_id="test", sequence=0, payload=payload
    )


def _started(vm: TerminalViewModel, agent_id: str, agent_type: str, task: str) -> None:
    vm.apply(
        _event(
            "subagent.started",
            {"agent_id": agent_id, "agent_type": agent_type, "task": task},
        )
    )


def test_started_event_adds_running_agent_and_shows_panel() -> None:
    vm = TerminalViewModel()
    _started(vm, "a1", "explorer", "find X")

    agents = vm.subagents_list()
    assert vm.subagents_visible is True
    assert len(agents) == 1
    assert agents[0]["agent_type"] == "explorer"
    assert agents[0]["status"] == "running"
    assert agents[0]["task"] == "find X"


def test_progress_updates_current_activity() -> None:
    vm = TerminalViewModel()
    _started(vm, "a1", "coder", "edit")
    vm.apply(
        _event(
            "subagent.progress",
            {
                "agent_id": "a1",
                "agent_type": "coder",
                "event": "tool.call.started",
                "tool": "write_file",
            },
        )
    )
    assert vm.subagents["a1"]["detail"] == "running write_file"


def test_completed_and_failed_settle_status() -> None:
    vm = TerminalViewModel()
    _started(vm, "a1", "explorer", "t")
    _started(vm, "a2", "coder", "t2")
    vm.apply(
        _event(
            "subagent.completed",
            {"agent_id": "a1", "agent_type": "explorer", "summary": "done well"},
        )
    )
    vm.apply(
        _event(
            "subagent.failed",
            {"agent_id": "a2", "agent_type": "coder", "error": "provider down"},
        )
    )

    assert vm.subagents["a1"]["status"] == "completed"
    assert vm.subagents["a1"]["detail"] == "done well"
    assert vm.subagents["a2"]["status"] == "failed"
    assert vm.subagents["a2"]["detail"] == "provider down"
    # Terminal agents stay visible until the next turn.
    assert vm.subagents_visible is True


def test_rejected_event_records_a_row() -> None:
    vm = TerminalViewModel()
    vm.apply(
        _event(
            "subagent.rejected",
            {"agent_id": "a1", "agent_type": "wizard", "reason": "Unknown sub-agent type."},
        )
    )
    row = vm.subagents_list()[0]
    assert row["status"] == "rejected"
    assert "Unknown" in row["task"]


def test_new_user_turn_clears_prior_agents() -> None:
    vm = TerminalViewModel()
    _started(vm, "a1", "explorer", "t")
    assert vm.subagents_list()

    vm.apply(_event("user.message", {"text": "next question"}))
    assert vm.subagents_list() == []
    assert vm.subagents_visible is False


def test_render_subagents_shows_marker_task_and_activity() -> None:
    agents = [
        {
            "agent_type": "explorer",
            "task": "map the loader",
            "status": "running",
            "detail": "running read_file",
        },
        {"agent_type": "coder", "task": "add flag", "status": "completed", "detail": "done"},
        {
            "agent_type": "reviewer",
            "task": "review diff",
            "status": "failed",
            "detail": "timeout",
        },
    ]
    text = render_subagents(agents, running_glyph="*", running_color="#ffffff").plain

    assert "3 agent(s)" in text
    assert "1 running" in text
    assert "explorer" in text and "map the loader" in text
    assert "running read_file" in text
    # Terminal markers for the settled agents.
    assert "✓" in text
    assert "✗" in text


def test_render_subagents_empty_is_just_header() -> None:
    text = render_subagents([], running_glyph="*", running_color="#ffffff").plain
    assert "0 agent(s)" in text


def test_agents_and_plan_coexist_and_persist_at_turn_end() -> None:
    # Regression: a turn that delegates (agents first) and then plans must show
    # BOTH panels, and both must stay up when the turn returns to READY - the
    # plan used to collapse at turn end, leaving only the AGENTS panel.
    vm = TerminalViewModel()
    _started(vm, "a1", "explorer", "investigate")
    vm.apply(
        _event(
            "planning.plan.created",
            {
                "status": "ACTIVE",
                "progress": "1/2",
                "current_step": "Do the thing",
                "current_step_status": "IN_PROGRESS",
                "completed_steps": [],
                "remaining_steps": ["Do the thing", "Verify"],
            },
        )
    )
    assert vm.plan_visible is True
    assert vm.subagents_visible is True

    vm.apply(_event("status.changed", {"state": "READY"}))
    assert vm.plan_visible is True
    assert vm.subagents_visible is True
