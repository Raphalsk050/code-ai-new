from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

from code_ai.config.models import AppConfig
from code_ai.core.errors import ProviderError, UnsupportedProviderCapability
from code_ai.providers.debug import ModelDebugLogger
from code_ai.providers.models import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    TokenUsage,
    ToolCall,
)
from code_ai.providers.translation import parse_arguments, tools_to_chat


def normalize_native_ollama_base_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        trimmed = trimmed[:-3]
    return trimmed.rstrip("/") + "/"


def _ollama_usage(data: dict[str, Any]) -> TokenUsage | None:
    prompt = int(data.get("prompt_eval_count") or 0)
    output = int(data.get("eval_count") or 0)
    if prompt or output:
        return TokenUsage.from_counts(
            input_tokens=prompt,
            output_tokens=output,
            exact=True,
            source="ollama",
        )
    return None


def _ollama_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or call
        name = function.get("name") or call.get("name")
        if not name:
            continue
        calls.append(
            ToolCall(
                id=str(call.get("id") or f"ollama_tool_{index}"),
                name=str(name),
                arguments=parse_arguments(function.get("arguments") or {}),
            )
        )
    return calls


def _ollama_reasoning_delta(data: dict[str, Any], message: dict[str, Any]) -> str:
    for source in (message, data):
        for key in ("thinking", "reasoning", "reasoning_content"):
            value = source.get(key)
            if value:
                return str(value)
    return ""


def messages_to_ollama(messages: list[Message]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            normalized.append(
                {
                    "role": "user",
                    "content": (
                        f"Tool result from {message.name or 'tool'} "
                        f"({message.tool_call_id or 'unknown call'}):\n"
                        f"{message.content}\n\n"
                        "Use this tool result exactly when answering."
                    ),
                }
            )
            continue
        entry: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            # Replay tool calls structurally so the model keeps invoking tools
            # instead of echoing them back as text in its next turn.
            entry["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]
        normalized.append(entry)
    return normalized


class NativeOllamaProvider:
    """Native Ollama `/api/chat` adapter."""

    def __init__(self, config: AppConfig) -> None:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - dependency availability
            raise ProviderError("The httpx package is required for native Ollama mode.") from exc

        self._httpx = httpx
        self._config = config
        self._base_url = normalize_native_ollama_base_url(config.base_url)
        self._client = httpx.AsyncClient(
            timeout=config.budgets.model_timeout(),
            verify=config.ssl_verification,
        )
        self._capabilities = ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            provider_reported_usage=True,
            remote_conversation_state=False,
            native_tokenization=False,
            image_support=False,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        url = urljoin(self._base_url, "api/chat")
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages_to_ollama(request.messages),
            "stream": True,
        }
        if request.tools:
            payload["tools"] = tools_to_chat(request.tools)
        options = self._config.sampling.ollama_options()
        if request.max_output_tokens:
            options["num_predict"] = request.max_output_tokens
        if options:
            payload["options"] = options

        debug = ModelDebugLogger.for_request(self._config, provider="ollama")
        if debug:
            debug.log_request(payload)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage: TokenUsage | None = None
        try:
            async with self._client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if debug:
                        debug.log_raw_chunk(line)
                    data = json.loads(line)
                    if "error" in data:
                        message = str(data["error"])
                        if "tool" in message.lower():
                            raise UnsupportedProviderCapability(message)
                        raise ProviderError(message)
                    message = data.get("message") or {}
                    content = str(message.get("content") or "")
                    if content:
                        text_parts.append(content)
                        yield ProviderEvent(kind="text_delta", text_delta=content)
                    reasoning = _ollama_reasoning_delta(data, message)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield ProviderEvent(kind="reasoning_delta", reasoning_delta=reasoning)
                    tool_calls.extend(_ollama_tool_calls(message))
                    usage = _ollama_usage(data) or usage
        except UnsupportedProviderCapability:
            raise
        except Exception as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc

        response = ModelResponse(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP,
            raw_provider_name="ollama",
        )
        if debug:
            debug.log_response(response)
        yield ProviderEvent(
            kind="completed",
            response=response,
            usage=usage,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        text_parts: list[str] = []
        completed: ModelResponse | None = None
        async for event in self.stream(request):
            if event.kind == "text_delta":
                text_parts.append(event.text_delta)
            elif event.kind == "completed" and event.response:
                completed = event.response
        if completed is None:
            completed = ModelResponse(text="".join(text_parts), raw_provider_name="ollama")
        elif not completed.text:
            completed.text = "".join(text_parts)
        return completed

    async def close(self) -> None:
        await self._client.aclose()
