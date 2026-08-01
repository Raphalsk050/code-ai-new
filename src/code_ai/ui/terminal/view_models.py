from __future__ import annotations

from dataclasses import dataclass, field

from code_ai.events.models import EventEnvelope
from code_ai.ui.terminal.widgets import build_plan_steps

# Character budget for the live "cmd~" line that streams execute_command output.
# Only the tail matters while it runs; the full stdout/stderr still arrives in
# the tool.call.completed summary and the tool result itself.
_COMMAND_TAIL_MAX_CHARS = 2000

# States that mean the agent is no longer working on a turn. The live code
# window belongs to a write in progress, so it closes on any of them.
_TURN_OVER_STATES = frozenset({"READY", "FAILED", "CLOSED"})


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
    # Live sub-agent activity, keyed by agent_id and kept in dispatch order, so
    # the AGENTS panel can show what each delegated agent is doing right now.
    # Reset at the start of every user turn so a prior turn's agents never linger.
    subagents: dict[str, dict[str, str]] = field(default_factory=dict)
    subagents_visible: bool = False
    # Live interactive-terminal state, driven by ``terminal.screen.updated``
    # events (from the tools, the poller, or /term). Session-scoped, not
    # turn-scoped: a dev server keeps running across turns, so the panel is
    # NOT cleared on user.message — only when its session closes.
    terminal_visible: bool = False
    terminal_session_id: str = ""
    terminal_screen: str = ""
    terminal_rows: int = 24
    terminal_cols: int = 80
    terminal_closed: bool = False
    # The file the model is writing right now, decoded from its streaming
    # tool-call arguments (see ``tool.call.progress``). Turn-scoped like the
    # plan and sub-agent panels: it stays up after the write lands so the user
    # can still read what was written, and is cleared at the next user message.
    code_stream_visible: bool = False
    code_stream_tool: str = ""
    code_stream_path: str = ""
    code_stream_key: str = ""
    code_stream_reason: str = ""
    code_stream_code: str = ""
    code_stream_complete: bool = False

    def subagents_list(self) -> list[dict[str, str]]:
        return list(self.subagents.values())

    def apply(self, event: EventEnvelope) -> None:
        if event.event_type == "status.changed":
            self.status = str(event.payload.get("state", self.status))
            if self.status in _TURN_OVER_STATES:
                # The turn is over, so nothing is being written any more. Leaving
                # the window up parked a finished file over the conversation
                # until the next prompt, pushing the answer the user was waiting
                # for off screen. Covers every ending - answered, failed or
                # cancelled - since all of them land in one of these states.
                self.clear_code_stream()
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
            "planning.plan.completed",
            "planning.plan.waiting",
            "planning.plan.blocked",
            "planning.plan.failed",
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
            elif event.event_type == "planning.plan.completed":
                self.conversation.append(f"plan> plan completed ({self.plan_progress})")
            elif event.event_type == "planning.plan.waiting":
                self.conversation.append(
                    f"plan> paused, waiting for you ({self.plan_progress})"
                )
            elif event.event_type in {"planning.plan.blocked", "planning.plan.failed"}:
                self.conversation.append(f"plan> plan {self.plan_status.lower()}")
        elif event.event_type == "planning.evidence.recorded":
            summary = str(event.payload.get("summary") or "")
            evidence_type = str(event.payload.get("type") or "evidence")
            # COMPLETION_REQUESTED carries the full final report, which already
            # renders in full via assistant.final. Echoing a hard 180-char slice
            # of it here only dangles a half-word ("- Sw...") as the last line, so
            # record it without the redundant (and mangled) summary.
            if evidence_type == "COMPLETION_REQUESTED":
                self.conversation.append(f"evidence> {evidence_type} recorded")
            else:
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
        elif event.event_type.startswith("goal."):
            self._apply_goal_event(event)
        elif event.event_type == "assistant.final":
            text = str(event.payload.get("text", ""))
            # On tool-required tasks the answer prose streams on the "working"
            # channel first, then arrives here as the announced final. Replace
            # the trailing dim duplicate so the answer renders once, as the
            # chipped message - mirroring the tool.calls.recovered rewrite.
            if (
                self.conversation
                and self.conversation[-1].startswith("working> ")
                and self.conversation[-1][len("working> ") :].strip() == text.strip()
            ):
                self.conversation.pop()
            self.conversation.append(f"ai> {text}")
        elif event.event_type == "user.message":
            # A new turn starts: drop the previous turn's artifacts so the PLAN and
            # AGENTS panels only ever show this turn's work. Both are turn-scoped and
            # persist through the turn's end (unlike before, when the plan collapsed
            # at turn end while agents lingered - the two are now symmetric), so a
            # turn that both planned and delegated shows both panels side by side.
            self.subagents.clear()
            self.subagents_visible = False
            self.plan_visible = False
            self.plan_steps = []
            self.clear_code_stream()
            self.conversation.append(f"you> {event.payload.get('text', '')}")
        elif event.event_type == "user.message.queued":
            # Typed while the agent was working. Shown as the user's line right
            # away - it is what they said - with a note that it has not reached
            # the model yet. Deliberately does NOT reset the plan, sub-agent and
            # code panels the way user.message does: this joins the running turn
            # instead of starting a new one.
            self.conversation.append(f"you> {event.payload.get('text', '')}")
            self.conversation.append("queued> waiting for the current step to finish...")
        elif event.event_type == "user.message.delivered":
            # It reached the model. Replace the waiting note rather than adding
            # a line, so steering costs one line in the transcript, not two.
            if self.conversation and self.conversation[-1].startswith("queued> "):
                self.conversation[-1] = "queued> delivered to the model"
            else:
                self.conversation.append("queued> delivered to the model")
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
                # Name each requested tool explicitly so the transcript shows
                # *which* tool the model invoked, not just that it invoked one.
                # The name is rendered as a chip downstream (see widgets.py).
                for call in tool_calls:
                    name = call.get("name") if isinstance(call, dict) else None
                    self.conversation.append(f"model> requested {name or 'tool'} tool")
        elif event.event_type == "tool.call.progress":
            self._apply_tool_progress(event.payload)
        elif event.event_type == "command.output":
            self._apply_command_output(event.payload)
        elif event.event_type == "terminal.screen.updated":
            self._apply_terminal_screen(event.payload)
        elif event.event_type == "tool.call.interrupted":
            # The call never arrived, so the window that was filling up with its
            # source is showing a write that is not going to happen: close it
            # rather than leave it spinning on a file nobody is writing.
            self.clear_code_stream()
            attempt = event.payload.get("attempt")
            total = event.payload.get("max_attempts")
            self.conversation.append(
                f"tool> call was cut off mid-stream, asking again ({attempt}/{total})"
            )
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
            if name == self.code_stream_tool:
                # The write landed: stop the live window claiming it is still
                # being typed, even if the stream never marked it complete.
                self.code_stream_complete = True
            self.conversation.append(f"tool> {name} completed{detail}")
        elif event.event_type == "tool.call.failed":
            name = event.payload.get("name")
            message = event.payload.get("message", "")
            if name == self.code_stream_tool:
                self.code_stream_complete = True
            self.conversation.append(f"tool> {name} failed: {message}")
        elif event.event_type.startswith("subagent."):
            self._apply_subagent_event(event)
        elif event.event_type == "model.request.failed":
            # Nothing rendered this before, so a request killed by the clock
            # simply stopped mid-turn and left the transcript looking frozen -
            # the one failure the user most needs named, and the only one that
            # said nothing at all.
            message = str(event.payload.get("message", ""))
            line = f"error> model request failed: {message}"
            if "timeout" in message.lower() or "timed out" in message.lower():
                line += (
                    " — the model needed longer than max_model_call_s /"
                    " max_model_step_seconds allow"
                )
            self.conversation.append(line)
        elif event.event_type == "learning.cancelled":
            self.conversation.append("memory> reflection postponed so this turn gets the model")
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

    def _apply_goal_event(self, event: EventEnvelope) -> None:
        """Reflect the /goal loop's lifecycle in the transcript.

        Per-criterion evaluations are deliberately not echoed one line each —
        the iteration summary carries the met/total count, and /goal status
        shows the full breakdown on demand.
        """
        kind = event.event_type
        payload = event.payload
        if kind == "goal.defined":
            self.conversation.append(
                f"goal> objetivo definido: {payload.get('objective', '')}"
            )
        elif kind == "goal.activated":
            self.conversation.append(
                "goal> loop iniciado "
                f"(máx. {payload.get('max_iterations')} iterações)"
            )
        elif kind == "goal.resumed":
            self.conversation.append("goal> objetivo retomado")
        elif kind == "goal.iteration.started":
            self.conversation.append(
                f"goal> iteração {payload.get('iteration')}/"
                f"{payload.get('max_iterations')} iniciada"
            )
        elif kind == "goal.iteration.completed":
            self.conversation.append(
                f"goal> iteração {payload.get('iteration')} avaliada: "
                f"{payload.get('criteria_met')}/{payload.get('criteria_total')} "
                "critérios atendidos"
            )
        elif kind == "goal.satisfied":
            self.conversation.append(
                "goal> ✔ objetivo cumprido em "
                f"{payload.get('iterations')} iteração(ões)"
            )
        elif kind == "goal.blocked":
            self.conversation.append(
                f"goal> bloqueado: {payload.get('reason', '')} — use /goal resume "
                "para continuar ou /goal stop para encerrar"
            )
        elif kind == "goal.exhausted":
            self.conversation.append(
                f"goal> limite de segurança atingido: {payload.get('reason', '')}"
            )
        elif kind == "goal.stopped":
            self.conversation.append(f"goal> parado: {payload.get('reason', '')}")

    def _apply_tool_progress(self, payload: dict[object, object]) -> None:
        """Render live 'writing code' feedback while a tool call streams in.

        The line updates in place as the arguments grow, so a large write_file
        shows the file and how much has been written so far instead of the UI
        sitting idle until the finished diff appears. A distinct 'tool~' prefix
        keeps this transient line apart from the started/completed 'tool>' lines.
        """

        name = str(payload.get("name") or "tool")
        path = str(payload.get("path") or "").strip()
        lines = payload.get("lines")
        chars = payload.get("chars")
        if path and isinstance(lines, int):
            detail = f"writing {path} ({lines} lines)"
        elif path:
            detail = f"writing {path}"
        else:
            detail = f"building call ({chars} chars)" if chars is not None else "building call"
        prefix = f"tool~ {name}: "
        line = prefix + detail
        if self.conversation and self.conversation[-1].startswith(prefix):
            self.conversation[-1] = line
        else:
            self.conversation.append(line)
        self._apply_code_stream(name, path, payload)

    def _apply_code_stream(
        self, name: str, path: str, payload: dict[object, object]
    ) -> None:
        """Drive the live code window from one streamed tool-call fragment.

        The window opens on ``call_started`` - the first word about a writing
        call, before any source exists - so the user gets the frame, the target
        and the model's reason first and watches the code fill in underneath.
        Waiting for the first line of code instead would put the window up only
        once there was already something to hide behind it.

        Source then arrives as an append-only slice (``code_offset`` +
        ``code_delta``) rather than the whole file, so a large write costs the
        same per update as a small one.
        """

        writes = bool(payload.get("writes"))
        if payload.get("call_started"):
            # A new call is starting. Whatever the window is showing belongs to
            # the previous one, which has already finished - so it closes here
            # unless this call is itself a write that will refill it. Leaving it
            # up stranded the last written file on screen while an unrelated
            # tool ran underneath it.
            self.clear_code_stream()
            if not writes:
                return
            self.code_stream_tool = name
            self.code_stream_visible = True
        elif not self.code_stream_visible:
            return
        if path:
            self.code_stream_path = path
        reason = payload.get("reason")
        if isinstance(reason, str) and reason.strip():
            self.code_stream_reason = reason.strip()

        delta = payload.get("code_delta")
        if not isinstance(delta, str):
            return
        self.code_stream_key = str(payload.get("code_key") or self.code_stream_key)
        offset = payload.get("code_offset")
        offset = offset if isinstance(offset, int) else 0
        if offset == 0:
            self.code_stream_code = ""
        elif offset != len(self.code_stream_code):
            # An update went missing, so appending here would splice the file
            # together wrongly. Leave the window on the last coherent state
            # rather than showing code that was never written.
            return
        self.code_stream_code += delta
        self.code_stream_complete = bool(payload.get("code_complete"))

    def clear_code_stream(self) -> None:
        self.code_stream_visible = False
        self.code_stream_tool = ""
        self.code_stream_path = ""
        self.code_stream_key = ""
        self.code_stream_reason = ""
        self.code_stream_code = ""
        self.code_stream_complete = False

    def _apply_command_output(self, payload: dict[object, object]) -> None:
        """Stream one execute_command output chunk into a live ``cmd~`` line.

        Chunks append to a single in-place line (like the ``tool~`` progress
        line) so the user watches the command's output as it happens instead of
        the UI sitting idle until the process exits. Only the tail is kept —
        the full output still lands in the tool result when the command ends.
        """
        text = str(payload.get("text") or "")
        if not text:
            return
        prefix = "cmd~ "
        if self.conversation and self.conversation[-1].startswith(prefix):
            merged = self.conversation[-1] + text
            self.conversation[-1] = self._bound_command_tail(merged, prefix)
        else:
            self.conversation.append(self._bound_command_tail(prefix + text, prefix))

    @staticmethod
    def _bound_command_tail(line: str, prefix: str) -> str:
        if len(line) <= _COMMAND_TAIL_MAX_CHARS:
            return line
        return prefix + "…" + line[-_COMMAND_TAIL_MAX_CHARS:]

    def _apply_terminal_screen(self, payload: dict[object, object]) -> None:
        """Reflect an interactive terminal's emulated screen in the panel state."""
        self.terminal_session_id = str(payload.get("session_id") or self.terminal_session_id)
        self.terminal_screen = str(payload.get("screen") or "")
        rows = payload.get("rows")
        cols = payload.get("columns")
        if isinstance(rows, int):
            self.terminal_rows = rows
        if isinstance(cols, int):
            self.terminal_cols = cols
        self.terminal_closed = bool(payload.get("closed"))
        self.terminal_visible = True

    def _apply_subagent_event(self, event: EventEnvelope) -> None:
        """Fold a ``subagent.*`` event into the live AGENTS panel state.

        Each dispatched agent is one row keyed by its id: it starts running, its
        current tool is reflected as it works, and it settles into a terminal
        marker (completed / failed / rejected). Terminal rows stay on screen -
        the panel is cleared at the next user turn, not when an agent finishes -
        so the user sees the outcome of the whole fan-out.
        """
        kind = event.event_type
        payload = event.payload
        agent_id = str(payload.get("agent_id") or "")
        agent_type = str(payload.get("agent_type") or "agent")
        # The Claude-style name assigned at creation; label rows by it and fall
        # back to the type only for older events that carry no name.
        name = str(payload.get("name") or "")
        label = name or agent_type

        if kind == "subagent.started":
            self.subagents[agent_id] = {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "name": name,
                "task": str(payload.get("task") or ""),
                "status": "running",
                "detail": "dispatched",
            }
            self.subagents_visible = True
            self.conversation.append(f"subagent> {label} ({agent_type}) started")
        elif kind == "subagent.progress":
            record = self.subagents.get(agent_id)
            if record is not None:
                record["detail"] = _subagent_progress_detail(payload)
        elif kind == "subagent.completed":
            self._settle_subagent(agent_id, agent_type, name, "completed", payload)
            self.conversation.append(f"subagent> {label} completed")
        elif kind == "subagent.failed":
            self._settle_subagent(agent_id, agent_type, name, "failed", payload)
            self.conversation.append(f"subagent> {label} failed")
        elif kind == "subagent.rejected":
            self.subagents[agent_id or f"rej-{len(self.subagents)}"] = {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "name": name,
                "task": str(payload.get("reason") or ""),
                "status": "rejected",
                "detail": "not dispatched",
            }
            self.subagents_visible = True
        elif kind == "subagent.circuit.open":
            self.conversation.append(
                f"subagent> {agent_type} temporarily disabled (repeated failures)"
            )

    def _settle_subagent(
        self,
        agent_id: str,
        agent_type: str,
        name: str,
        status: str,
        payload: dict[object, object],
    ) -> None:
        record = self.subagents.get(agent_id)
        detail = str(payload.get("error") or payload.get("summary") or "").strip()
        detail = detail.replace("\n", " ")[:80] or status
        if record is None:
            record = {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "name": name,
                "task": "",
            }
            self.subagents[agent_id or f"{status}-{len(self.subagents)}"] = record
        record["status"] = status
        record["detail"] = detail
        self.subagents_visible = True

    def _apply_plan_payload(self, payload: dict[object, object]) -> None:
        self.planner_mode = str(payload.get("mode", self.planner_mode))
        self.phase = str(payload.get("phase", self.phase))
        self.plan_progress = str(payload.get("progress", self.plan_progress))
        # A settled (completed) plan has no current step; show a dash instead of
        # the stringified None or the stale last step.
        current = payload.get("current_step", self.current_step)
        self.current_step = str(current) if current is not None else "-"
        verification = payload.get("latest_verification_passed")
        if verification is not None:
            self.latest_verification_status = "passed" if verification else "not current"
        self.plan_status = str(payload.get("status", self.plan_status))
        steps = build_plan_steps(payload)
        if steps:
            self.plan_steps = steps
        # Show the panel whenever the model has authored steps, and keep it up
        # through the turn's end (it is cleared on the next user message). This
        # mirrors the AGENTS panel's lifecycle so both stay visible together.
        self.plan_visible = bool(self.plan_steps)


def _subagent_progress_detail(payload: dict[object, object]) -> str:
    """A short 'what is this agent doing now' line from a progress event."""
    event = str(payload.get("event") or "")
    # ``tool`` is the tool the sub-agent is running now; ``name`` on a progress
    # event is the agent's own name, not the tool.
    tool = str(payload.get("tool") or "").strip()
    if event == "tool.call.started" and tool:
        return f"running {tool}"
    if event == "tool.call.completed" and tool:
        return f"{tool} done"
    if event == "tool.call.failed" and tool:
        return f"{tool} failed"
    if event == "model.response.completed":
        return "thinking"
    return tool or "working"


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
