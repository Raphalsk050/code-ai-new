from __future__ import annotations

from collections.abc import Sequence

from code_ai.app.service import CodeAIApplication
from code_ai.core.errors import GoalStateError, TerminalSessionError
from code_ai.core.interaction import Answer, render_answers
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import ImageContent
from code_ai.ui.terminal.view_models import TerminalViewModel


class TerminalController:
    """Connects application events to a UI-facing view model."""

    def __init__(self, app: CodeAIApplication, view_model: TerminalViewModel) -> None:
        self.app = app
        self.view_model = view_model

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
    # share one live terminal instead of the user being locked out of it.

    async def terminal_start(self, command: str | None = None) -> str:
        manager = getattr(self.app, "terminal_manager", None)
        if manager is None:
            return "term> Terminal indisponível nesta sessão."
        try:
            session_id = manager.create(
                cwd=self.app.session.config.workspace, command=command
            )
        except TerminalSessionError as exc:
            return f"term> {exc}"
        await self._emit_terminal_screen(manager, session_id)
        return (
            f"term> Sessão {session_id[:8]} iniciada. Digite /term <texto> para "
            "enviar comandos; /term ctrl c interrompe; /term kill encerra."
        )

    async def terminal_send(self, text: str) -> str:
        def type_and_run(manager, session_id: str) -> None:
            manager.send_text(session_id, text)
            manager.send_enter(session_id)

        return await self._terminal_action(type_and_run)

    async def terminal_enter(self) -> str:
        return await self._terminal_action(
            lambda manager, session_id: manager.send_enter(session_id)
        )

    async def terminal_control(self, key: str) -> str:
        return await self._terminal_action(
            lambda manager, session_id: manager.send_control(session_id, key)
        )

    async def terminal_kill(self) -> str:
        manager = getattr(self.app, "terminal_manager", None)
        session_id = manager.latest_session_id() if manager else None
        if manager is None or session_id is None:
            return "term> Nenhuma sessão de terminal ativa."
        try:
            manager.terminate(session_id)
        except TerminalSessionError as exc:
            return f"term> {exc}"
        self.view_model.terminal_closed = True
        await self.app.event_bus.emit(
            "terminal.screen.updated",
            {"session_id": session_id, "screen": self.view_model.terminal_screen,
             "closed": True},
            source="ui.term",
        )
        return f"term> Sessão {session_id[:8]} encerrada."

    def terminal_status(self) -> str:
        manager = getattr(self.app, "terminal_manager", None)
        session_id = manager.latest_session_id() if manager else None
        if manager is None or session_id is None:
            return (
                "term> Nenhuma sessão ativa. Use /term start [comando] para abrir "
                "um terminal interativo."
            )
        screen = manager.read_screen(session_id)
        return (
            f"term> Sessão {session_id[:8]} · {screen.get('columns')}x"
            f"{screen.get('rows')}"
            f"{' · encerrada' if screen.get('closed') else ''}\n"
            f"{screen.get('screen') or '(tela vazia)'}"
        )

    async def _terminal_action(self, action) -> str:
        """Run one manager action against the latest session and emit its screen."""
        manager = getattr(self.app, "terminal_manager", None)
        session_id = manager.latest_session_id() if manager else None
        if manager is None or session_id is None:
            return "term> Nenhuma sessão ativa. Use /term start [comando] primeiro."
        try:
            action(manager, session_id)
        except TerminalSessionError as exc:
            return f"term> {exc}"
        await self._emit_terminal_screen(manager, session_id)
        return ""

    async def _emit_terminal_screen(self, manager, session_id: str) -> None:
        screen = manager.read_screen(session_id)
        await self.app.event_bus.emit(
            "terminal.screen.updated", screen, source="ui.term"
        )

    # -- persistent goal (/goal) -------------------------------------------

    async def define_goal(self, objective: str) -> str:
        try:
            snapshot = await self.app.define_goal(objective)
        except GoalStateError as exc:
            return f"goal> {exc}"
        lines = self._format_goal_criteria(snapshot)
        if snapshot.get("started"):
            return (
                "goal> Objetivo definido e loop iniciado (critérios derivados "
                "automaticamente):\n" + lines
            )
        return (
            "goal> Objetivo definido. Critérios de aceitação propostos:\n"
            + lines
            + "\n\nUse /goal start para começar (o agente só para quando todos "
            "os critérios passarem), ou /goal stop para descartar."
        )

    async def start_goal(self) -> str:
        try:
            snapshot = await self.app.start_goal()
        except GoalStateError as exc:
            return f"goal> {exc}"
        return (
            "goal> Loop iniciado. O agente vai iterar até todos os critérios "
            f"passarem ({snapshot.get('criteria_progress')} no momento). "
            "Use /goal stop para interromper."
        )

    async def stop_goal(self) -> str:
        try:
            snapshot = await self.app.stop_goal()
        except GoalStateError as exc:
            return f"goal> {exc}"
        return f"goal> Parada solicitada. Status: {snapshot.get('status')}."

    def goal_status(self) -> str:
        snapshot = self.app.goal_snapshot()
        if snapshot.get("status") == "none":
            return "goal> Nenhum objetivo definido. Use /goal <objetivo>."
        header = (
            "goal> Status do objetivo\n"
            f"objetivo: {snapshot.get('objective')}\n"
            f"status: {snapshot.get('status')}"
            f"{' (loop rodando)' if snapshot.get('loop_running') else ''}\n"
            f"iterações: {snapshot.get('iterations')}\n"
            f"critérios: {snapshot.get('criteria_progress')}"
        )
        reason = str(snapshot.get("stop_reason") or "")
        if reason:
            header += f"\nmotivo: {reason}"
        return header + "\n" + self._format_goal_criteria(snapshot)

    @staticmethod
    def _format_goal_criteria(snapshot: dict[str, object]) -> str:
        criteria = snapshot.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            return "  (nenhum critério)"
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
