from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

from code_ai.config.models import AppConfig
from code_ai.context.compression import ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.usage import UsageLedger
from code_ai.core.errors import CancellationError, CodeAIError, ToolExecutionError
from code_ai.core.state import AgentState
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderEvent,
    ToolResult,
)
from code_ai.tools.base import ToolContext
from code_ai.tools.output import bound_text
from code_ai.tools.registry import ToolRegistry

ToolContextFactory = Callable[[asyncio.Event | None], ToolContext]


@dataclass(slots=True)
class TurnResult:
    text: str
    response: ModelResponse | None
    cancelled: bool = False


class AgentOrchestrator:
    """Deterministic provider/tool loop for one user turn at a time."""

    def __init__(
        self,
        *,
        config: AppConfig,
        provider: ModelProvider,
        tool_registry: ToolRegistry,
        conversation: ConversationState,
        usage: UsageLedger,
        event_bus: AsyncEventBus,
        compressor: ContextCompressor,
        tool_context_factory: ToolContextFactory,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tool_registry = tool_registry
        self.conversation = conversation
        self.usage = usage
        self.event_bus = event_bus
        self.compressor = compressor
        self.tool_context_factory = tool_context_factory
        self.state = AgentState.STARTING

    async def set_state(self, state: AgentState, *, phase: str | None = None) -> None:
        self.state = state
        await self.event_bus.emit(
            "status.changed", {"state": state.value}, source="core.orchestrator"
        )
        if phase:
            await self.event_bus.emit("phase.changed", {"phase": phase}, source="core.orchestrator")

    async def run_turn(self, text: str, *, cancel_event: asyncio.Event | None = None) -> TurnResult:
        await self.set_state(AgentState.CALLING_MODEL, phase="accepted_user_message")
        await self.event_bus.emit("user.message", {"text": text}, source="core.orchestrator")
        self.conversation.add_user(text)

        tool_calls_executed = 0
        last_response: ModelResponse | None = None

        try:
            for step in range(self.config.budgets.max_model_steps):
                self._raise_if_cancelled(cancel_event)
                tool_definitions = self.tool_registry.definitions()
                compression = await self.compressor.ensure_capacity(
                    self.conversation, tool_definitions
                )
                await self.event_bus.emit(
                    "usage.updated",
                    {
                        "active_context_tokens": compression.active_tokens,
                        "active_context_estimated": compression.estimated,
                        "cumulative": self.usage.to_dict(),
                    },
                    source="context",
                )
                request = ModelRequest(
                    model=self.config.model,
                    messages=self.conversation.snapshot(),
                    tools=tool_definitions,
                    max_output_tokens=self.config.output_token_reserve,
                    previous_response_id=self.conversation.previous_response_id,
                    use_remote_conversation_state=(
                        self.config.use_remote_conversation_state
                        and self.conversation.remote_state_supported
                        and self.provider.capabilities.remote_conversation_state
                    ),
                    metadata={"step": step},
                )
                await self.event_bus.emit(
                    "model.request.started",
                    {"model": self.config.model, "step": step, "tools": len(tool_definitions)},
                    source="core.orchestrator",
                )
                response = await self._collect_model_response(request, cancel_event)
                last_response = response
                self.usage.add(response.usage)
                if response.response_id:
                    self.conversation.previous_response_id = response.response_id
                await self.event_bus.emit(
                    "model.response.completed",
                    {
                        "finish_reason": response.finish_reason.value,
                        "tool_calls": [call.to_dict() for call in response.tool_calls],
                        "usage": response.usage.to_dict() if response.usage else None,
                    },
                    source="core.orchestrator",
                )
                await self.event_bus.emit(
                    "usage.updated",
                    {"cumulative": self.usage.to_dict()},
                    source="context",
                )

                if response.text:
                    self.conversation.add_assistant(response.text, response.tool_calls)
                elif response.tool_calls:
                    self.conversation.add_assistant("", response.tool_calls)

                if not response.tool_calls:
                    await self.set_state(AgentState.READY, phase="waiting_user")
                    await self.event_bus.emit(
                        "turn.completed",
                        {"text": response.text, "usage": self.usage.to_dict()},
                        source="core.orchestrator",
                    )
                    return TurnResult(text=response.text, response=response)

                await self.set_state(AgentState.EXECUTING_TOOL, phase="executing_tools")
                for call in response.tool_calls:
                    self._raise_if_cancelled(cancel_event)
                    tool_calls_executed += 1
                    if tool_calls_executed > self.config.budgets.max_tool_calls:
                        raise ToolExecutionError("Maximum tool call budget exceeded.")
                    result = await self._execute_tool(
                        call.id, call.name, call.arguments, cancel_event
                    )
                    self.conversation.add_tool_result(result)
                await self.set_state(AgentState.CALLING_MODEL, phase="calling_model_after_tools")

            raise ToolExecutionError("Maximum model step budget exceeded.")
        except CancellationError:
            await self.set_state(AgentState.READY, phase="waiting_user")
            await self.event_bus.emit("turn.cancelled", {}, source="core.orchestrator")
            return TurnResult(text="", response=last_response, cancelled=True)
        except Exception as exc:
            await self.set_state(AgentState.FAILED, phase="failed")
            await self.event_bus.emit(
                "error",
                {"message": str(exc), "type": type(exc).__name__},
                source="core.orchestrator",
            )
            raise

    async def _collect_model_response(
        self,
        request: ModelRequest,
        cancel_event: asyncio.Event | None,
    ) -> ModelResponse:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        completed: ModelResponse | None = None
        try:
            async for event in self.provider.stream(request):
                self._raise_if_cancelled(cancel_event)
                await self._emit_provider_event(event)
                if event.kind == "text_delta":
                    text_parts.append(event.text_delta)
                elif event.kind == "reasoning_delta":
                    reasoning_parts.append(event.reasoning_delta)
                elif event.kind == "completed" and event.response:
                    completed = event.response
        except CancellationError:
            raise
        except Exception as exc:
            await self.event_bus.emit(
                "model.request.failed",
                {"message": str(exc), "type": type(exc).__name__},
                source="core.orchestrator",
            )
            raise

        if completed is None:
            return ModelResponse(
                text="".join(text_parts),
                reasoning="".join(reasoning_parts),
                finish_reason=FinishReason.UNKNOWN,
            )
        if not completed.text and text_parts:
            completed.text = "".join(text_parts)
        if not completed.reasoning and reasoning_parts:
            completed.reasoning = "".join(reasoning_parts)
        return completed

    async def _emit_provider_event(self, event: ProviderEvent) -> None:
        if event.kind == "text_delta":
            await self.event_bus.emit(
                "model.stream.delta",
                {"text": event.text_delta},
                source="provider",
            )
        elif event.kind == "reasoning_delta":
            await self.event_bus.emit(
                "model.thinking.delta",
                {"text": event.reasoning_delta},
                source="provider",
            )
        elif event.kind == "warning" and event.warning:
            await self.event_bus.emit("warning", {"message": event.warning}, source="provider")
        elif event.kind == "usage" and event.usage:
            await self.event_bus.emit(
                "usage.updated",
                {"usage": event.usage.to_dict()},
                source="provider",
            )

    async def _execute_tool(
        self,
        tool_call_id: str,
        name: str,
        arguments: dict[str, object],
        cancel_event: asyncio.Event | None,
    ) -> ToolResult:
        await self.event_bus.emit(
            "tool.call.requested",
            {"tool_call_id": tool_call_id, "name": name, "arguments": arguments},
            source="core.orchestrator",
        )
        await self.event_bus.emit(
            "tool.call.started",
            {"tool_call_id": tool_call_id, "name": name},
            source="core.orchestrator",
        )
        try:
            payload = await self.tool_registry.execute(
                name, arguments, self.tool_context_factory(cancel_event)
            )
            content = bound_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str),
                self.config.budgets.max_tool_output_chars,
            )
            await self.event_bus.emit(
                "tool.call.completed",
                {"tool_call_id": tool_call_id, "name": name, "result": payload},
                source="core.orchestrator",
            )
            return ToolResult(tool_call_id=tool_call_id, name=name, content=content)
        except CancellationError:
            raise
        except CodeAIError as exc:
            await self.event_bus.emit(
                "tool.call.failed",
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "message": str(exc),
                    "type": type(exc).__name__,
                },
                source="core.orchestrator",
            )
            return ToolResult(tool_call_id=tool_call_id, name=name, content=str(exc), is_error=True)

    @staticmethod
    def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
        if cancel_event and cancel_event.is_set():
            raise CancellationError("Turn cancelled.")
