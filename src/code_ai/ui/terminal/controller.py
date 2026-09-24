from __future__ import annotations

from collections.abc import Sequence

from code_ai.app.commands import SessionCommands
from code_ai.app.service import CodeAIApplication
from code_ai.core.interaction import Answer, render_answers
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import ImageContent
from code_ai.ui.terminal.view_models import TerminalViewModel


class TerminalController:
    """Connects application events to a UI-facing view model."""

    def __init__(self, app: CodeAIApplication, view_model: TerminalViewModel) -> None:
        self.app = app
        self.view_model = view_model
        # Plan, goal, terminal and index behaviour is shared with the bridge;
        # this class only phrases the outcome for the terminal.
        self.commands = SessionCommands(app)

    async def handle_event(self, event: EventEnvelope) -> None:
        self.view_model.apply(event)

    async def submit(self, text: str, images: list[ImageContent] | None = None) -> None:
        if text.strip():
            await self.app.submit_user_message(text.strip(), images=list(images or []))

    async def answer_questions(self, answers: Sequence[Answer]) -> None:
        """Send the answered cards back as the reply to the blocking question.

        The cards collapse into one message, each line naming the question it
        answers, and go through the same path a typed reply takes - so the
        paused plan resumes exactly as it does today and the model needs to
        know nothing about how the answer was collected.
        """

        text = render_answers(answers)
        if text.strip():
            await self.app.submit_question_answer(text)

    async def compact(self) -> str:
        result = await self.app.request_context_compression()
        if not result.compressed:
            return "command> Nothing to compact — conversation is already short."
        return (
            "command> Compacted conversation context: "
            f"{result.previous_tokens} → {result.active_tokens} tokens."
        )

    async def cancel(self) -> None:
        await self.app.cancel_current_turn()

    # -- code index (/index) -------------------------------------------------
    async def index_refresh(self, *, full: bool = False, subtree: str = "") -> str:
        result = await self.commands.index_refresh(full=full, subtree=subtree)
        return f"index> {result.message}"

    # -- MCP servers (/mcp) --------------------------------------------------

    async def mcp(self, argument: str) -> str:
        """``/mcp`` lists the servers; ``/mcp <reconnect|approve|on|off> <name>`` acts on one."""

        action, _, name = argument.strip().partition(" ")
        if not action or action == "status":
            result = self.commands.mcp_status()
            return "\n".join(f"mcp> {line}" for line in result.message.splitlines())
        if action == "add":
            return f"mcp> {(await self.commands.mcp_add(name)).message}"
        if action == "answer":
            return f"mcp> {self.commands.mcp_answer(name).message}"
        if action in ("decline", "cancel"):
            return f"mcp> {self.commands.mcp_answer(action).message}"
        result = await self.commands.mcp_action(action, name)
        return f"mcp> {result.message}"

    async def mcp_prompt(self, token: str, argument: str) -> tuple[bool, str]:
        """The text an MCP prompt command submits, or the reason it cannot run."""

        result = await self.commands.resolve_mcp_prompt(token, argument)
        if not result.ok:
            return False, f"mcp> {result.message}"
        return True, str(result.data.get("prompt") or "")

    def index_status(self) -> str:
        status = self.app.code_index_status()
        if status is None:
            return "index> The code index is disabled (config: index.enabled)."
        semantic = (
            f"{status.embedding_model} ({status.embedded_chunks}/{status.chunks} chunks embedded)"
            if status.embedding_model
            else "off (set index.embedding_model to enable)"
        )
        lines = [
            "index> Code index status",
            f"files: {status.files}",
            f"chunks: {status.chunks}",
            f"lexical: {status.lexical_engine}",
            f"semantic: {semantic}",
            f"last refresh: {status.last_refresh or 'never'}"
            + (" (full)" if status.last_refresh_full else ""),
            f"path: {status.index_path}",
        ]
        if status.refreshing:
            lines.append("refreshing: yes")
        if status.pending_touches:
            lines.append(f"pending: {status.pending_touches} file(s) queued")
        if status.last_error:
            lines.append(f"last error: {status.last_error}")
        if status.empty:
            lines.append("hint: run /index to build it")
        return "\n".join(lines)

    async def set_planner_mode(self, mode: str) -> None:
        await self.app.set_planner_mode(mode)

    def has_active_plan(self) -> bool:
        return self.app.has_active_plan()

    async def start_plan_execution(self) -> bool:
        return await self.app.start_plan_execution()

    async def set_permission_mode(self, mode: str) -> None:
        await self.app.set_permission_mode(mode)

    async def replan(self, reason: str | None = None) -> str:
        return await self.app.request_replan(reason=reason)

    def plan_snapshot(self) -> dict[str, object]:
        """The backend's authoritative plan snapshot (see PlannerService)."""
        return self.app.get_plan_snapshot()

    def plan_status(self) -> str:
        snapshot = self.app.get_plan_snapshot()
        return (
            "command> Plan status\n"
            f"mode: {snapshot.get('mode')}\n"
            f"phase: {snapshot.get('phase')}\n"
            f"progress: {snapshot.get('progress')}\n"
            f"current: {snapshot.get('current_step')}\n"
            f"verification passed: {snapshot.get('latest_verification_passed')}"
        )

    # -- interactive terminal (/term) ---------------------------------------
    # The same PTY sessions the model drives through its terminal tools, now
    # reachable by the human: /term types into the session, so user and agent
    # share one live terminal instead of the user being locked out of it. The
    # sessions themselves are driven by SessionCommands, which the bridge uses
    # as well - only the wording below is the terminal's.

    async def terminal_start(self, command: str | None = None) -> str:
        result = await self.commands.terminal_start(command or "")
        if not result.ok:
            return f"term> {result.message}"
        session_id = str(result.data.get("session_id") or "")
        return (
            f"term> Session {session_id[:8]} started. Type /term <text> to "
            "enviar comandos; /term ctrl c interrompe; /term kill encerra."
        )

    async def terminal_send(self, text: str) -> str:
        return self._term_line(await self.commands.terminal_write(text))

    async def terminal_enter(self) -> str:
        return self._term_line(await self.commands.terminal_write("", enter=True))

    async def terminal_control(self, key: str) -> str:
        return self._term_line(await self.commands.terminal_control(key))

    async def terminal_kill(self) -> str:
        result = await self.commands.terminal_kill()
        if not result.ok:
            return f"term> {result.message}"
        self.view_model.terminal_closed = True
        screen = result.data.get("screen") or {}
        session_id = str(screen.get("session_id") or "")
        return f"term> Session {session_id[:8]} closed."

    async def terminal_status(self) -> str:
        result = await self.commands.terminal_status()
        if not result.ok:
            return (
                "term> No active session. Use /term start [command] to open "
                "um terminal interativo."
            )
        screen = result.data.get("screen") or {}
        session_id = str(result.data.get("session_id") or "")
        return (
            f"term> Session {session_id[:8]} · {screen.get('columns')}x"
            f"{screen.get('rows')}"
            f"{' · encerrada' if screen.get('closed') else ''}\n"
            f"{screen.get('screen') or '(tela vazia)'}"
        )

    @staticmethod
    def _term_line(result: object) -> str:
        """An action that worked says nothing: the screen event is the answer."""
        ok = getattr(result, "ok", False)
        return "" if ok else f"term> {getattr(result, 'message', '')}"

    # -- persistent goal (/goal) -------------------------------------------

    async def define_goal(self, objective: str) -> str:
        result = await self.commands.goal_define(objective)
        if not result.ok:
            return f"goal> {result.message}"
        snapshot = dict(result.data.get("goal") or {})
        lines = self._format_goal_criteria(snapshot)
        if snapshot.get("started"):
            return (
                "goal> Objective set and the loop started (criteria derived "
                "automaticamente):\n" + lines
            )
        return (
            "goal> Objective set. Acceptance criteria proposed:\n"
            + lines
            + "\n\nUse /goal start to begin (the agent only stops once every "
            "criterion passes), or /goal stop to drop it."
        )

    async def start_goal(self) -> str:
        result = await self.commands.goal_start()
        if not result.ok:
            return f"goal> {result.message}"
        snapshot = dict(result.data.get("goal") or {})
        return (
            "goal> Loop started. The agent iterates until every criterion "
            f"passarem ({snapshot.get('criteria_progress')} no momento). "
            "Use /goal stop para interromper."
        )

    async def stop_goal(self) -> str:
        result = await self.commands.goal_stop()
        if not result.ok:
            return f"goal> {result.message}"
        snapshot = dict(result.data.get("goal") or {})
        return f"goal> Parada solicitada. Status: {snapshot.get('status')}."

    def goal_status(self) -> str:
        snapshot = self.app.goal_snapshot()
        if snapshot.get("status") == "none":
            return "goal> No objective set. Use /goal <objective>."
        header = (
            "goal> Objective status\n"
            f"objective: {snapshot.get('objective')}\n"
            f"status: {snapshot.get('status')}"
            f"{' (loop running)' if snapshot.get('loop_running') else ''}\n"
            f"iterations: {snapshot.get('iterations')}\n"
            f"criteria: {snapshot.get('criteria_progress')}"
        )
        reason = str(snapshot.get("stop_reason") or "")
        if reason:
            header += f"\nreason: {reason}"
        return header + "\n" + self._format_goal_criteria(snapshot)

    @staticmethod
    def _format_goal_criteria(snapshot: dict[str, object]) -> str:
        criteria = snapshot.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            return "  (no criteria)"
        lines = []
        for item in criteria:
            if not isinstance(item, dict):
                continue
            met = item.get("met")
            mark = "✓" if met else ("✗" if met is not None else "•")
            line = f"  {mark} {item.get('label')}"
            detail = str(item.get("detail") or "")
            if detail and met is False:
                line += f" — {detail}"
            lines.append(line)
        return "\n".join(lines)
