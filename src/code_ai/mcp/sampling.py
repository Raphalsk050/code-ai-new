"""A server asking for a model completion (``sampling/createMessage``).

Some servers need an LLM of their own for a step - summarising what they
fetched, drafting a query - and MCP lets them borrow the client's instead of
shipping an API key. It runs on the session's model, and in "ask" mode the user
sees the prompt first, on the same card a tool call gets: tokens are spent and
text leaves for the server, so it is not something to do behind their back.

Nothing of the conversation is sent: ``includeContext`` is answered with none.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from code_ai.core.approval import ApprovalDecision, ApprovalRequest
from code_ai.mcp.jsonrpc import McpError
from code_ai.providers.models import FinishReason, ImageContent, Message, ModelRequest

if TYPE_CHECKING:
    from code_ai.config.models import AppConfig
    from code_ai.core.approval import ApprovalGateway
    from code_ai.events.bus import AsyncEventBus
    from code_ai.providers.base import ModelProvider

# The spec's code for "the user rejected the sampling request".
USER_REJECTED = -1
# Whatever a server asks for, one answer stays a paragraph, not a book.
_MAX_TOKENS = 4096
_PREVIEW_CHARS = 1500


class SamplingBroker:
    def __init__(
        self,
        *,
        config: AppConfig,
        provider: ModelProvider,
        event_bus: AsyncEventBus,
        gateway: Callable[[], ApprovalGateway],
    ) -> None:
        self._config = config
        self._provider = provider
        self._bus = event_bus
        # Read per request: the UI attaches its gateway after the session is built.
        self._gateway = gateway
        self._allowed: set[str] = set()
        self._ids = itertools.count(1)

    async def __call__(self, server: str, params: dict[str, Any]) -> dict[str, Any]:
        system = str(params.get("systemPrompt") or "").strip()
        messages = [
            _message(item) for item in params.get("messages") or [] if isinstance(item, dict)
        ]
        if not messages:
            raise McpError(-32602, "sampling/createMessage needs at least one message.")
        wanted = params.get("maxTokens")
        max_tokens = (
            min(int(wanted), _MAX_TOKENS) if isinstance(wanted, int) and wanted > 0 else 1024
        )
        await self._authorize(server, system, messages, max_tokens)

        request = ModelRequest(
            model=self._config.model,
            messages=[*([Message(role="system", content=system)] if system else []), *messages],
            max_output_tokens=max_tokens,
            reasoning_effort=self._config.sampling.light_reasoning_effort(),
            metadata={"purpose": "mcp_sampling", "server": server},
        )
        response = await self._provider.complete(request)
        await self._bus.emit(
            "mcp.sampling.completed",
            {"server": server, "chars": len(response.text or "")},
            source="mcp",
        )
        return {
            "role": "assistant",
            "content": {"type": "text", "text": response.text or ""},
            "model": self._config.model,
            "stopReason": "maxTokens"
            if response.finish_reason is FinishReason.LENGTH
            else "endTurn",
        }

    async def _authorize(
        self, server: str, system: str, messages: list[Message], max_tokens: int
    ) -> None:
        mode = self._config.permission_mode
        if mode in ("auto", "bypass") or server in self._allowed:
            return
        call_id = f"mcp-sampling-{next(self._ids)}"
        request = ApprovalRequest(
            call_id=call_id,
            tool_name=f"mcp_sampling ({server})",
            arguments={
                "server": server,
                "system_prompt": system[:_PREVIEW_CHARS],
                "messages": [
                    {"role": message.role, "text": message.content[:_PREVIEW_CHARS]}
                    for message in messages[-4:]
                ],
                "max_tokens": max_tokens,
            },
            signature=f"mcp_sampling:{server}",
            reason=f"The MCP server {server!r} asks your model for a completion.",
            capabilities=("mcp",),
        )
        await self._bus.emit("tool.approval.requested", request.to_dict(), source="mcp")
        try:
            decision = await self._gateway().request_approval(request)
        except Exception as exc:  # noqa: BLE001 - a broken prompt is a refusal, not a crash
            decision = ApprovalDecision.unavailable(f"Approval prompt failed: {exc}")
        await self._bus.emit(
            "tool.approval.resolved",
            {
                "call_id": call_id,
                "tool_name": request.tool_name,
                "scope": decision.scope.value,
                "approved": decision.approved,
            },
            source="mcp",
        )
        if not decision.approved:
            raise McpError(USER_REJECTED, decision.reason or "The user declined the request.")
        if decision.remember:
            self._allowed.add(server)


def _message(item: dict[str, Any]) -> Message:
    role = "assistant" if item.get("role") == "assistant" else "user"
    content = item.get("content")
    parts = content if isinstance(content, list) else [content]
    texts: list[str] = []
    images: list[ImageContent] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            texts.append(str(part.get("text") or ""))
        elif part.get("type") == "image" and part.get("data"):
            images.append(
                ImageContent(
                    data=str(part["data"]), media_type=str(part.get("mimeType") or "image/png")
                )
            )
        elif part.get("type") == "audio":
            texts.append("[audio omitted]")
    return Message(role=role, content="\n\n".join(texts), images=images)
