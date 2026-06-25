from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from code_ai.app.session import ApplicationSession
from code_ai.context.compression import CompressionResult, ContextCompressor
from code_ai.core.orchestration import AgentOrchestrator, TurnResult
from code_ai.core.planning import PlannerMode
from code_ai.core.state import AgentState
from code_ai.events.bus import AsyncEventBus, EventSubscriber
from code_ai.events.models import EventEnvelope
from code_ai.providers.base import ModelProvider
from code_ai.tools.terminal.manager import PersistentTerminalManager


class CodeAIApplication:
    """Public facade for CLI, TUI, and embedding clients."""

    def __init__(
        self,
        *,
        session: ApplicationSession,
        event_bus: AsyncEventBus,
        orchestrator: AgentOrchestrator,
        provider: ModelProvider,
        compressor: ContextCompressor,
        terminal_manager: PersistentTerminalManager | None = None,
    ) -> None:
        self.session = session
        self.event_bus = event_bus
        self.orchestrator = orchestrator
        self.provider = provider
        self.compressor = compressor
        self.terminal_manager = terminal_manager
        self._current_cancel: asyncio.Event | None = None
        self._current_task: asyncio.Task[TurnResult] | None = None

    async def start(self) -> None:
        self.session.state = AgentState.READY
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit(
            "session.started",
            {
                "model": self.session.config.model,
                "api_mode": self.session.config.api_mode,
                "workspace": str(self.session.config.workspace),
                "permission_mode": self.session.config.permission_mode,
            },
            source="app",
        )
        await self.event_bus.emit("session.ready", {}, source="app")

    async def submit_user_message(self, text: str, *, context: str = "") -> TurnResult:
        if self._current_task and not self._current_task.done():
            raise RuntimeError("A turn is already running.")
        self._current_cancel = asyncio.Event()
        self._current_task = asyncio.create_task(
            self.orchestrator.run_turn(
                text, cancel_event=self._current_cancel, context=context
            )
        )
        try:
            return await self._current_task
        finally:
            self._current_task = None
            self._current_cancel = None

    async def reset_conversation(self) -> None:
        """Start a fresh conversation, keeping the system prompt and tools.

        Powers the embedding "new conversation" action: any running turn is
        cancelled and the transcript is dropped so the next message starts a
        clean thread, while the leading system instructions are preserved.
        """
        if self._current_task and not self._current_task.done():
            await self.cancel_current_turn()
            try:
                await self._current_task
            except Exception:  # a cancelled turn may surface as an error; ignore
                pass
        messages = self.orchestrator.conversation.messages
        preserved = messages[:1] if messages and messages[0].role == "system" else []
        messages[:] = preserved
        self.orchestrator.conversation.reset_remote_state()
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit("conversation.reset", {}, source="app")

    async def cancel_current_turn(self) -> None:
        if self._current_cancel is not None:
            await self.event_bus.emit(
                "status.changed", {"state": AgentState.CANCELLING.value}, source="app"
            )
            self._current_cancel.set()

    async def request_context_compression(self) -> CompressionResult:
        await self.orchestrator.set_state(
            AgentState.COMPRESSING_CONTEXT, phase="manual_compression"
        )
        # force=True: a manual /compact always runs right away, regardless of
        # whether the conversation is already under the auto-compress threshold.
        compression = await self.compressor.ensure_capacity(
            self.orchestrator.conversation,
            self.orchestrator.tool_registry.definitions(),
            force=True,
        )
        # Refresh the context-meter bar immediately; otherwise it would only
        # catch up to the post-compaction token count on the next turn.
        await self.orchestrator.emit_context_usage(compression)
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        return compression

    async def set_planner_mode(self, mode: str | PlannerMode) -> None:
        if not self.orchestrator.planner:
            raise RuntimeError("Planner is not configured.")
        await self.orchestrator.planner.set_mode(PlannerMode(mode))

    async def set_permission_mode(self, mode: str) -> None:
        from code_ai.config.models import SUPPORTED_PERMISSION_MODES

        normalized = mode.strip().lower()
        if normalized not in SUPPORTED_PERMISSION_MODES:
            raise ValueError(
                f"Unsupported permission mode: {mode}. "
                f"Choose one of {sorted(SUPPORTED_PERMISSION_MODES)}."
            )
        # The orchestrator reads config.permission_mode live, so updating it in
        # place takes effect on the next tool call.
        self.session.config.permission_mode = normalized
        await self.event_bus.emit(
            "permission.mode.changed", {"mode": normalized}, source="app"
        )

    async def request_deep_plan(self, *, write_to_workspace: bool = False) -> str:
        if not self.orchestrator.planner:
            raise RuntimeError("Planner is not configured.")
        if write_to_workspace:
            return "command> Writing plan files is not enabled in this build."
        snapshot = self.orchestrator.planner.plan_snapshot()
        await self.event_bus.emit(
            "planning.plan.created",
            snapshot,
            source="app",
        )
        return _render_plan_snapshot(snapshot)

    async def approve_or_start_plan_execution(self) -> None:
        await self.set_planner_mode(PlannerMode.ACT)

    async def request_replan(self, reason: str | None = None) -> str:
        if not self.orchestrator.planner:
            raise RuntimeError("Planner is not configured.")
        await self.event_bus.emit(
            "planning.replan.started",
            {"reason": reason or "manual request"},
            source="app",
        )
        await self.event_bus.emit(
            "planning.replan.completed",
            self.orchestrator.planner.plan_snapshot(),
            source="app",
        )
        return "command> Replan requested. The next turn will classify the current objective again."

    def get_plan_snapshot(self) -> dict[str, object]:
        if not self.orchestrator.planner:
            return {"planner": "not configured"}
        return self.orchestrator.planner.plan_snapshot()

    async def submit_question_answer(self, answer: str) -> None:
        await self.event_bus.emit(
            "interaction.question.answered",
            {"answer": answer},
            source="app",
        )

    def subscribe(self, handler_or_sink: EventSubscriber) -> EventSubscriber:
        return self.event_bus.subscribe(handler_or_sink)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        self.event_bus.unsubscribe(subscriber)

    async def close(self) -> None:
        if self._current_task and not self._current_task.done():
            await self.cancel_current_turn()
            await self._current_task
        if self.terminal_manager:
            self.terminal_manager.close_all()
        await self.provider.close()
        self.session.state = AgentState.CLOSED
        await self.event_bus.emit("session.closed", {}, source="app")


ApplicationEventHandler = Callable[[EventEnvelope], Awaitable[None] | None]


def _render_plan_snapshot(snapshot: dict[str, object]) -> str:
    if "current_step" not in snapshot:
        return "command> No active plan."
    return (
        "command> Plan snapshot\n"
        f"mode: {snapshot.get('mode')}\n"
        f"phase: {snapshot.get('phase')}\n"
        f"progress: {snapshot.get('progress')}\n"
        f"current: {snapshot.get('current_step')}\n"
        f"changed paths: {snapshot.get('changed_paths', [])}\n"
        f"verification passed: {snapshot.get('latest_verification_passed')}"
    )
