from __future__ import annotations

from code_ai.app.service import CodeAIApplication
from code_ai.events.models import EventEnvelope
from code_ai.ui.terminal.view_models import TerminalViewModel


class TerminalController:
    """Connects application events to a UI-facing view model."""

    def __init__(self, app: CodeAIApplication, view_model: TerminalViewModel) -> None:
        self.app = app
        self.view_model = view_model

    async def handle_event(self, event: EventEnvelope) -> None:
        self.view_model.apply(event)

    async def submit(self, text: str) -> None:
        if text.strip():
            await self.app.submit_user_message(text.strip())

    async def compact(self) -> None:
        await self.app.request_context_compression()

    async def cancel(self) -> None:
        await self.app.cancel_current_turn()

    async def set_planner_mode(self, mode: str) -> None:
        await self.app.set_planner_mode(mode)

    async def deep_plan(self) -> str:
        return await self.app.request_deep_plan(write_to_workspace=False)

    async def replan(self, reason: str | None = None) -> str:
        return await self.app.request_replan(reason=reason)

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
