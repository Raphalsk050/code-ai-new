from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from code_ai.app.session import ApplicationSession
from code_ai.context.compression import ContextCompressor
from code_ai.core.orchestration import AgentOrchestrator, TurnResult
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
            },
            source="app",
        )
        await self.event_bus.emit("session.ready", {}, source="app")

    async def submit_user_message(self, text: str) -> TurnResult:
        if self._current_task and not self._current_task.done():
            raise RuntimeError("A turn is already running.")
        self._current_cancel = asyncio.Event()
        self._current_task = asyncio.create_task(
            self.orchestrator.run_turn(text, cancel_event=self._current_cancel)
        )
        try:
            return await self._current_task
        finally:
            self._current_task = None
            self._current_cancel = None

    async def cancel_current_turn(self) -> None:
        if self._current_cancel is not None:
            await self.event_bus.emit(
                "status.changed", {"state": AgentState.CANCELLING.value}, source="app"
            )
            self._current_cancel.set()

    async def request_context_compression(self) -> None:
        await self.orchestrator.set_state(
            AgentState.COMPRESSING_CONTEXT, phase="manual_compression"
        )
        await self.compressor.ensure_capacity(
            self.orchestrator.conversation,
            self.orchestrator.tool_registry.definitions(),
        )
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")

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
