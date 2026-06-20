from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from typing import Any

from code_ai.config.models import AppConfig
from code_ai.core.errors import ProviderError, TransientProviderError
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    TokenUsage,
    ToolCall,
)
from code_ai.providers.openai_completions import _is_transient_exception, _usage_from_object
from code_ai.providers.translation import object_get, parse_arguments, tools_to_responses


def _responses_input(request: ModelRequest) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
        else:
            items.append(
                {
                    "role": message.role,
                    "content": [{"type": "input_text", "text": message.content}],
                }
            )
    return items


def normalize_responses_output_item(item: Any) -> ToolCall | str | None:
    item_type = object_get(item, "type")
    if item_type in {"message", "output_text"}:
        content = object_get(item, "content", [])
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for part in content or []:
            text = object_get(part, "text", object_get(part, "output_text", ""))
            if text:
                parts.append(str(text))
        return "".join(parts) if parts else None
    if item_type in {"function_call", "tool_call"}:
        return ToolCall(
            id=str(object_get(item, "call_id", object_get(item, "id", "")) or ""),
            name=str(object_get(item, "name", "")),
            arguments=parse_arguments(object_get(item, "arguments", "{}")),
        )
    return None


def _text_delta_from_event(event_type: str, event: Any) -> str:
    if event_type not in {"response.output_text.delta", "response.text.delta"}:
        return ""
    return str(object_get(event, "delta", "") or "")


class OpenAIResponsesProvider:
    """OpenAI Responses adapter with local-state fallback for unsupported remote state."""

    def __init__(self, config: AppConfig) -> None:
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # pragma: no cover - dependency availability
            raise ProviderError("The openai package is required for Responses mode.") from exc

        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.provider_api_key(),
            base_url=config.base_url,
            timeout=config.budgets.model_timeout(),
            max_retries=0,
        )
        self._remote_state_supported = config.use_remote_conversation_state
        self._capabilities = ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            provider_reported_usage=True,
            remote_conversation_state=config.use_remote_conversation_state,
            native_tokenization=False,
            image_support=True,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        self._capabilities.remote_conversation_state = self._remote_state_supported
        return self._capabilities

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        async for event in self._stream_with_retry(request):
            yield event

    async def complete(self, request: ModelRequest) -> ModelResponse:
        text_parts: list[str] = []
        completed: ModelResponse | None = None
        async for event in self.stream(request):
            if event.kind == "text_delta":
                text_parts.append(event.text_delta)
            elif event.kind == "completed" and event.response:
                completed = event.response
        if completed is None:
            completed = ModelResponse(
                text="".join(text_parts), raw_provider_name="openai_responses"
            )
        elif not completed.text:
            completed.text = "".join(text_parts)
        return completed

    async def _stream_with_retry(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        attempts = 0
        while True:
            try:
                async for event in self._stream_once(request):
                    yield event
                return
            except Exception as exc:
                text = str(exc).lower()
                if self._remote_state_supported and "previous_response" in text:
                    self._remote_state_supported = False
                    yield ProviderEvent(
                        kind="warning",
                        warning=(
                            "Endpoint rejected remote response state; retrying with local "
                            "conversation state."
                        ),
                    )
                    request.previous_response_id = None
                    request.use_remote_conversation_state = False
                    continue
                if not _is_transient_exception(exc) or attempts >= 2:
                    raise ProviderError(f"Responses request failed: {exc}") from exc
                attempts += 1
                await asyncio.sleep(min(2.0, 0.25 * (2**attempts)) + random.random() * 0.1)

    async def _stream_once(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "input": _responses_input(request),
            "stream": True,
        }
        if request.max_output_tokens:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.tools:
            kwargs["tools"] = tools_to_responses(request.tools)
        if (
            self._remote_state_supported
            and request.use_remote_conversation_state
            and request.previous_response_id
        ):
            kwargs["previous_response_id"] = request.previous_response_id

        try:
            stream = await self._client.responses.create(**kwargs)
        except Exception as exc:
            if _is_transient_exception(exc):
                raise TransientProviderError(str(exc)) from exc
            raise

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        response_id: str | None = None
        usage: TokenUsage | None = None

        async for event in stream:
            event_type = str(object_get(event, "type", ""))
            is_completion_event = event_type in {"response.completed", "response.done"}
            delta = _text_delta_from_event(event_type, event)
            if delta:
                text_parts.append(delta)
                yield ProviderEvent(kind="text_delta", text_delta=delta)
            elif is_completion_event:
                response = object_get(event, "response", event)
                response_id = (
                    str(object_get(response, "id", response_id or "") or "") or response_id
                )
                usage = (
                    _usage_from_object(object_get(response, "usage"), source="openai_responses")
                    or usage
                )
                for item in object_get(response, "output", []) or []:
                    normalized = normalize_responses_output_item(item)
                    if isinstance(normalized, ToolCall):
                        tool_calls.append(normalized)
                    elif isinstance(normalized, str) and normalized and not text_parts:
                        text_parts.append(normalized)

        finish = FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="".join(text_parts),
                tool_calls=tool_calls,
                usage=usage,
                finish_reason=finish,
                response_id=response_id,
                raw_provider_name="openai_responses",
            ),
            usage=usage,
        )

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
