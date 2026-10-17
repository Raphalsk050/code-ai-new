"""What the desktop avatar is told, read off the session's events.

The panel's avatar works this out from its own store (vscode-extension/webview/
avatar-pose.ts and index.tsx); the desktop one has no panel, so the backend
does it here, the same way, for every front end at once.
"""

from __future__ import annotations

import re
from typing import Any

# Statuses of a turn at work, and of one that is over (store/transcript.ts, store/session.ts).
WORKING = frozenset({"CALLING_MODEL", "EXECUTING_TOOL", "COMPRESSING_CONTEXT", "CANCELLING"})
TURN_OVER = frozenset({"READY", "ERROR", "FAILED", "CANCELLED", "IDLE", "CLOSED", "DISCONNECTED"})

# A tool's kind, by its name (webview/tool-target.ts), and what the avatar does for it.
_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("search", re.compile(r"^(search_|web_search)")),
    (
        "type",
        re.compile(
            r"^(execute_command|start_terminal|send_terminal_text|read_screen|terminal_"
            r"|interrupt_terminal|terminate_terminal)"
        ),
    ),
    ("search", re.compile(r"^(browser_|ui_preview|ui_audit|web_)")),
    ("think", re.compile(r"^(dispatch_agent)")),
    ("think", re.compile(r"^(submit_plan|complete_plan_step|complete_task)")),
    ("type", re.compile(r"(^write_file|^edit_code|_edit$|_create$|_format$|_convert$|^create_)")),
    ("search", re.compile(r"(^read_file|^list_files|_inspect$)")),
)


def tool_pose(name: str) -> str:
    for pose, pattern in _KINDS:
        if pattern.search(name):
            return pose
    return "think"


class Mood:
    """Follows the events and says what changed for the avatar.

    ``feed`` returns the messages for its page: ``{"pose": ...}`` when what
    it does changes, ``{"cue": ...}`` for a moment to react to, and
    ``{"waiting": bool}`` when something starts or stops waiting on the user.
    """

    def __init__(self) -> None:
        self.status = "READY"
        self._tools: list[tuple[str, str]] = []
        self._writing = False
        self._tool_ran = False
        self._in_turn = False
        self._pending: set[str] = set()
        self.pose = "idle"
        self.waiting = False

    def feed(self, event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if event_type == "status.changed":
            self.status = str(payload.get("state") or self.status)
            if self.status in TURN_OVER:
                self._tools.clear()
                self._writing = False
                # No approval outlives its turn. A question does: asking ends the turn.
                self._pending = {item for item in self._pending if not item.startswith("approval:")}
                if self._in_turn:
                    self._in_turn = False
                    mood = self._turn_mood()
                    if mood:
                        out.append({"cue": mood})
            elif self.status in WORKING:
                self._in_turn = True
        elif event_type == "tool.call.started":
            self._tools.append((str(payload.get("tool_call_id", "")), str(payload.get("name", ""))))
            self._writing = False
            self._tool_ran = True
        elif event_type in {"tool.call.completed", "tool.call.failed"}:
            done = str(payload.get("tool_call_id", ""))
            self._tools = [tool for tool in self._tools if tool[0] != done]
        elif event_type == "model.stream.delta":
            self._writing = True
        elif event_type in {"model.request.started", "assistant.final"}:
            self._writing = False
        elif event_type == "user.message":
            self._tools.clear()
            self._writing = False
            self._tool_ran = False
            self._pending.clear()
        elif event_type == "conversation.reset":
            self._pending.clear()
            out.append({"cue": "greet"})
        elif event_type == "tool.approval.requested":
            self._pending.add(f"approval:{payload.get('call_id', '')}")
        elif event_type == "tool.approval.resolved":
            self._pending.discard(f"approval:{payload.get('call_id', '')}")
            out.append({"cue": "answered" if payload.get("approved") else "refused"})
        elif event_type == "interaction.question.requested":
            self._pending.add(f"question:{payload.get('id', '')}")
        elif event_type == "interaction.question.answered":
            self._pending = {item for item in self._pending if not item.startswith("question:")}
            out.append({"cue": "answered"})
        elif event_type == "mcp.elicitation.requested":
            self._pending.add(f"elicitation:{payload.get('id', '')}")
        elif event_type == "mcp.elicitation.resolved":
            self._pending.discard(f"elicitation:{payload.get('id', '')}")
            out.append({"cue": "answered" if payload.get("action") == "accept" else "refused"})
        else:
            return out

        pose = self._pose()
        if pose != self.pose:
            self.pose = pose
            out.insert(0, {"pose": pose})
        waiting = bool(self._pending)
        if waiting != self.waiting:
            self.waiting = waiting
            out.append({"waiting": waiting})
        return out

    def _pose(self) -> str:
        if self.status not in WORKING:
            return "idle"
        if self.status == "EXECUTING_TOOL" and self._tools:
            return tool_pose(self._tools[-1][1])
        return "type" if self._writing else "think"

    def _turn_mood(self) -> str | None:
        """Glad of work that got done, sorry when it broke; a plain answer is nothing to it."""
        if self.status in {"ERROR", "FAILED"}:
            return "went-wrong"
        if self.status in {"READY", "IDLE"} and self._tool_ran:
            return "went-well"
        return None
