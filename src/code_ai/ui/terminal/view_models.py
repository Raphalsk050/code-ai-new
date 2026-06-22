from __future__ import annotations

from dataclasses import dataclass, field

from code_ai.events.models import EventEnvelope
from code_ai.ui.terminal.widgets import build_plan_steps, plan_is_active


@dataclass(slots=True)
class TerminalViewModel:
    status: str = "STARTING"
    phase: str = "starting"
    activity: str = "idle"
    conversation: list[str] = field(default_factory=list)
    active_context_tokens: str = "tokens unavailable"
    context_used: int | None = None
    context_budget: int | None = None
    context_threshold: float = 0.82
    cumulative_usage: str = "0"
    planner_mode: str = "auto"
    permission_mode: str = "ask"
    plan_progress: str = "-"
    current_step: str = "-"
    latest_verification_status: str = "unknown"
    plan_visible: bool = False
    plan_status: str = ""
    plan_steps: list[dict[str, str]] = field(default_factory=list)

    def apply(self, event: EventEnvelope) -> None:
        if event.event_type == "status.changed":
            self.status = str(event.payload.get("state", self.status))
            # The turn returning to an idle state means the plan is done with
            # (answered, finished, or stopped): collapse the plan panel.
            if self.status in {"READY", "FAILED", "CLOSED"}:
                self.plan_visible = False
        elif event.event_type == "phase.changed":
            self.phase = str(event.payload.get("phase", self.phase))
        elif event.event_type == "planning.mode.changed":
            self.planner_mode = str(event.payload.get("mode", self.planner_mode))
        elif event.event_type == "planning.phase.changed":
            self.phase = str(event.payload.get("phase", self.phase))
            self.planner_mode = str(event.payload.get("mode", self.planner_mode))
        elif event.event_type in {
            "planning.plan.created",
            "planning.plan.revised",
            "planning.step.started",
            "planning.step.completed",
            "planning.step.failed",
            "planning.step.blocked",
        }:
            self._apply_plan_payload(event.payload)
            if event.event_type == "planning.step.started":
                self.conversation.append(f"plan> {self.current_step}")
            elif event.event_type == "planning.step.completed":
                self.conversation.append(f"plan> completed {self.current_step}")
            elif event.event_type == "planning.step.failed":
                self.conversation.append(f"plan> failed {self.current_step}")
        elif event.event_type == "planning.evidence.recorded":
            summary = str(event.payload.get("summary") or "")
            evidence_type = str(event.payload.get("type") or "evidence")
            self.conversation.append(f"evidence> {evidence_type}: {summary[:180]}")
        elif event.event_type == "permission.mode.changed":
            self.permission_mode = str(event.payload.get("mode", self.permission_mode))
            self.conversation.append(f"permission> mode set to {self.permission_mode}")
        elif event.event_type == "tool.approval.requested":
            name = event.payload.get("tool_name")
            self.conversation.append(f"approval> awaiting decision for {name}...")
        elif event.event_type == "tool.approval.resolved":
            name = event.payload.get("tool_name")
            verb = "approved" if event.payload.get("approved") else "denied"
            scope = event.payload.get("scope")
            self.conversation.append(f"approval> {name} {verb} ({scope})")
        elif event.event_type == "planning.policy.denied":
            self.conversation.append(
                f"policy> denied {event.payload.get('tool_name')}: {event.payload.get('reason')}"
            )
        elif event.event_type == "planning.completion.rejected":
            missing = event.payload.get("missing_requirements", [])
            self.conversation.append(f"completion> rejected: {missing}")
        elif event.event_type == "assistant.final":
            # Final evidence reached: collapse the plan panel as the turn closes.
            self.plan_visible = False
            self.conversation.append(f"ai> {event.payload.get('text', '')}")
        elif event.event_type == "user.message":
            self.conversation.append(f"you> {event.payload.get('text', '')}")
        elif event.event_type == "model.request.started":
            step = event.payload.get("step")
            suffix = f" step {step}" if step is not None else ""
            self.conversation.append(f"model> thinking{suffix}...")
        elif event.event_type == "model.stream.delta":
            text = str(event.payload.get("text", ""))
            channel = str(event.payload.get("channel") or "answer")
            prefix = "working> " if channel == "working" else "ai> "
            if self.conversation and self.conversation[-1].startswith(prefix):
                self.conversation[-1] += text
            else:
                self.conversation.append(prefix + text)
        elif event.event_type == "model.thinking.delta":
            text = str(event.payload.get("text", ""))
            if self.conversation and self.conversation[-1].startswith("thinking> "):
                self.conversation[-1] += text
            else:
                self.conversation.append("thinking> " + text)
        elif event.event_type == "tool.calls.recovered":
            # A weak model printed its tool call as text, which already streamed
            # into the chat as the last ai>/working> line. Replace that raw line
            # with the cleaned prose (or drop it entirely) so the chat shows the
            # recovered tool running, not the raw call markup.
            cleaned = str(event.payload.get("text") or "").strip()
            if self.conversation and self.conversation[-1].startswith(("ai> ", "working> ")):
                if cleaned:
                    self.conversation[-1] = f"ai> {cleaned}"
                else:
                    self.conversation.pop()
        elif event.event_type == "model.response.completed":
            tool_calls = event.payload.get("tool_calls")
            if tool_calls:
                self.conversation.append("model> requested tool calls")
        elif event.event_type == "tool.call.started":
            self.conversation.append(f"tool> {event.payload.get('name')} started")
        elif event.event_type == "tool.call.completed":
            name = str(event.payload.get("name") or "")
            result = event.payload.get("result")
            detail = ""
            if isinstance(result, dict):
                if name == "web_search":
                    detail = _web_search_detail(result)
                elif name == "list_files":
                    detail = f": {len(result.get('entries', []))} entries"
                elif name == "search_code":
                    detail = f": {len(result.get('matches', []))} matches"
                elif name in {"write_file", "edit_code"}:
                    detail = f": {result.get('path', '')}"
                stdout = str(result.get("stdout") or "").strip()
                cwd = str(result.get("cwd") or "").strip()
                if not detail and stdout:
                    detail = f": {stdout[:180]}"
                elif cwd:
                    detail = f": cwd {cwd}"
            self.conversation.append(f"tool> {name} completed{detail}")
        elif event.event_type == "tool.call.failed":
            name = event.payload.get("name")
            message = event.payload.get("message", "")
            self.conversation.append(f"tool> {name} failed: {message}")
        elif event.event_type in {"warning", "error"}:
            self.conversation.append(f"{event.event_type}> {event.payload.get('message', '')}")
        elif event.event_type == "usage.updated":
            active = event.payload.get("active_context_tokens")
            estimated = event.payload.get("active_context_estimated")
            if active is not None:
                self.active_context_tokens = f"{'~' if estimated else ''}{active}"
                self.context_used = int(active)
            budget = event.payload.get("context_budget")
            if budget is not None:
                self.context_budget = int(budget)
            threshold = event.payload.get("context_threshold")
            if threshold is not None:
                self.context_threshold = float(threshold)
            cumulative = event.payload.get("cumulative")
            if isinstance(cumulative, dict):
                self.cumulative_usage = str(cumulative.get("total_tokens", "0"))

    def _apply_plan_payload(self, payload: dict[object, object]) -> None:
        self.planner_mode = str(payload.get("mode", self.planner_mode))
        self.phase = str(payload.get("phase", self.phase))
        self.plan_progress = str(payload.get("progress", self.plan_progress))
        self.current_step = str(payload.get("current_step", self.current_step))
        verification = payload.get("latest_verification_passed")
        if verification is not None:
            self.latest_verification_status = "passed" if verification else "not current"
        self.plan_status = str(payload.get("status", self.plan_status))
        steps = build_plan_steps(payload)
        if steps:
            self.plan_steps = steps
        # Show the panel only while a defined plan is actively being worked on.
        self.plan_visible = plan_is_active(payload) and bool(steps)


def _web_search_detail(result: dict[object, object]) -> str:
    raw_results = result.get("results")
    if not isinstance(raw_results, list):
        return ""
    count = len(raw_results)
    if count == 0:
        return ": 0 results"
    titles = []
    for item in raw_results[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if title and url:
            titles.append(f"{title[:80]} <{url[:100]}>")
        elif title:
            titles.append(title[:100])
    if not titles:
        return f": {count} result(s)"
    return f": {count} result(s): " + " | ".join(titles)
