from __future__ import annotations

from dataclasses import dataclass, field

from code_ai.events.models import EventEnvelope


@dataclass(slots=True)
class TerminalViewModel:
    status: str = "STARTING"
    phase: str = "starting"
    activity: str = "idle"
    conversation: list[str] = field(default_factory=list)
    active_context_tokens: str = "tokens unavailable"
    cumulative_usage: str = "0"

    def apply(self, event: EventEnvelope) -> None:
        if event.event_type == "status.changed":
            self.status = str(event.payload.get("state", self.status))
        elif event.event_type == "phase.changed":
            self.phase = str(event.payload.get("phase", self.phase))
        elif event.event_type == "user.message":
            self.conversation.append(f"you> {event.payload.get('text', '')}")
        elif event.event_type == "model.request.started":
            step = event.payload.get("step")
            suffix = f" step {step}" if step is not None else ""
            self.conversation.append(f"model> thinking{suffix}...")
        elif event.event_type == "model.stream.delta":
            text = str(event.payload.get("text", ""))
            if self.conversation and self.conversation[-1].startswith("ai> "):
                self.conversation[-1] += text
            else:
                self.conversation.append("ai> " + text)
        elif event.event_type == "model.thinking.delta":
            text = str(event.payload.get("text", ""))
            if self.conversation and self.conversation[-1].startswith("thinking> "):
                self.conversation[-1] += text
            else:
                self.conversation.append("thinking> " + text)
        elif event.event_type == "model.response.completed":
            tool_calls = event.payload.get("tool_calls")
            if tool_calls:
                self.conversation.append("model> requested tool calls")
        elif event.event_type == "tool.call.started":
            self.conversation.append(f"tool> {event.payload.get('name')} started")
        elif event.event_type == "tool.call.completed":
            result = event.payload.get("result")
            detail = ""
            if isinstance(result, dict):
                stdout = str(result.get("stdout") or "").strip()
                cwd = str(result.get("cwd") or "").strip()
                if stdout:
                    detail = f": {stdout[:180]}"
                elif cwd:
                    detail = f": cwd {cwd}"
            self.conversation.append(f"tool> {event.payload.get('name')} completed{detail}")
        elif event.event_type in {"warning", "error"}:
            self.conversation.append(f"{event.event_type}> {event.payload.get('message', '')}")
        elif event.event_type == "usage.updated":
            active = event.payload.get("active_context_tokens")
            estimated = event.payload.get("active_context_estimated")
            if active is not None:
                self.active_context_tokens = f"{'~' if estimated else ''}{active}"
            cumulative = event.payload.get("cumulative")
            if isinstance(cumulative, dict):
                self.cumulative_usage = str(cumulative.get("total_tokens", "0"))
