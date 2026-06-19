from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import ToolDefinition
from code_ai.util.paths import WorkspacePolicy


@dataclass(slots=True)
class ToolContext:
    config: AppConfig
    workspace: WorkspacePolicy
    event_bus: AsyncEventBus
    cancel_event: asyncio.Event | None = None
    review_service: Any = None
    terminal_manager: Any = None


class BaseTool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        raise NotImplementedError
