from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import ToolDefinition
from code_ai.util.paths import WorkspacePolicy


class ToolCapability(StrEnum):
    LOCAL_READ = "local_read"
    LOCAL_WRITE = "local_write"
    PROCESS = "process"
    INTERACTIVE_TERMINAL = "interactive_terminal"
    COMPUTER_CONTROL = "computer_control"
    REVIEW = "review"
    WEB = "web"
    INTERACTION = "interaction"
    INTERNAL_TRANSITION = "internal_transition"
    INTERNAL_COMPLETION = "internal_completion"
    MEMORY = "memory"
    DELEGATE = "delegate"


@dataclass(slots=True)
class ToolContext:
    config: AppConfig
    workspace: WorkspacePolicy
    event_bus: AsyncEventBus
    cancel_event: asyncio.Event | None = None
    review_service: Any = None
    terminal_manager: Any = None
    desktop_controller: Any = None
    memory: Any = None
    # Dispatcher used by the delegation tool to run sub-agents. ``None`` on
    # sub-agent contexts (they cannot delegate further) and whenever delegation
    # is not wired. ``subagent_depth`` is the caller's depth in the delegation
    # tree (0 for the main agent), forwarded so depth limits are enforced.
    subagent_coordinator: Any = None
    subagent_depth: int = 0


class BaseTool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    capabilities: frozenset[ToolCapability]

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        raise NotImplementedError
