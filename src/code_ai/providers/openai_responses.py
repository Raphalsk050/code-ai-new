from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from code_ai.config.models import AppConfig
from code_ai.core.errors import ProviderError, TransientProviderError
from code_ai.providers.base import build_openai_http_client, closing_stream
from code_ai.providers.debug import ModelDebugLogger
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    TokenUsage,
    ToolCall,
)
from code_ai.providers.openai_completions import (
    _is_transient_exception,
    _looks_like_sampling_error,
    _usage_from_object,
)
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
            continue
        if message.content or message.images:
            # The Responses API discriminates content parts by role: model output
            # (assistant) must be "output_text", while user/system input is
            # "input_text". Sending "input_text" for an assistant turn fails schema
            # validation with invalid_union on strict servers (e.g. LM Studio).
            part_type = "output_text" if message.role == "assistant" else "input_text"
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"type": part_type, "text": message.content})
            parts.extend(
                {"type": "input_image", "image_url": image.to_data_url()}
                for image in message.images
            )
            items.append({"role": message.role, "content": parts})
        # Replay tool calls as structured function_call items so the model keeps
        # invoking tools instead of echoing them as text in its next answer.
        for call in message.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, default=str),
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


def _reasoning_delta_from_event(event_type: str, event: Any) -> str:
    """Reasoning-summary text streamed by the Responses API.

    Raw reasoning tokens are never exposed; the API streams a *summary* of the
    model's reasoning through ``response.reasoning_summary_text.delta`` events
    (older snapshots used ``response.reasoning_summary.delta``).
    """
    if event_type not in {
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary.delta",
    }:
        return ""
    return str(object_get(event, "delta", "") or "")


def _reasoning_from_output_item(item: Any) -> str:
    """Concatenated summary text from a completed ``type: "reasoning"`` item."""
    if object_get(item, "type") != "reasoning":
        return ""
    parts: list[str] = []
    for entry in object_get(item, "summary", []) or []:
        text = object_get(entry, "text", object_get(entry, "summary_text", ""))
        if text:
            parts.append(str(text))
    return "".join(parts)


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
            http_client=build_openai_http_client(config),
        )
        self._remote_state_supported = config.use_remote_conversation_state
        self._sampling_supported = True
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
        # Closed at every layer: leaving an `async for` early suspends the
        # generator under it rather than closing it, so the HTTP response at the
        # bottom would stay open and the server would keep generating.
        async with aclosing(self._stream_with_retry(request)) as events:
            async for event in events:
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
                async with aclosing(self._stream_once(request)) as events:
                    async for event in events:
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
            kwargs["tools"] = tools_to_responses(
                request.tools, strict=self._config.strict_tools
            )
        if (
            self._remote_state_supported
            and request.use_remote_conversation_state
            and request.previous_response_id
        ):
            kwargs["previous_response_id"] = request.previous_response_id
        if self._sampling_supported:
            kwargs.update(self._config.sampling.responses_kwargs())

        debug = ModelDebugLogger.for_request(self._config, provider="openai_responses")
        if debug:
            debug.log_request(kwargs)

        try:
            stream = await self._client.responses.create(**kwargs)
        except Exception as exc:
            if self._sampling_supported and _looks_like_sampling_error(exc):
                self._sampling_supported = False
                yield ProviderEvent(
                    kind="warning",
                    warning="Endpoint rejected sampling parameters; retrying without them.",
                )
                async with aclosing(self._stream_once(request)) as events:
                    async for event in events:
                        yield event
                return
            if _is_transient_exception(exc):
                raise TransientProviderError(str(exc)) from exc
            raise

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        response_id: str | None = None
        usage: TokenUsage | None = None
        # Track streaming function-call items so we can surface live progress:
        # the name arrives on ``output_item.added`` and the arguments dribble in
        # via ``function_call_arguments.delta`` before the final completion event.
        fc_names: dict[str, str] = {}
        fc_args: dict[str, str] = {}
        fc_index: dict[str, int] = {}

        # Closed deterministically so a cancelled turn disconnects: an
        # inference server keeps generating until the client goes away.
        async with closing_stream(stream):
            async for event in stream:
                if debug:
                    debug.log_raw_chunk(event)
                event_type = str(object_get(event, "type", ""))
                is_completion_event = event_type in {"response.completed", "response.done"}
                delta = _text_delta_from_event(event_type, event)
                reasoning_delta = _reasoning_delta_from_event(event_type, event)
                if delta:
                    text_parts.append(delta)
                    yield ProviderEvent(kind="text_delta", text_delta=delta)
                elif reasoning_delta:
                    reasoning_parts.append(reasoning_delta)
                    yield ProviderEvent(kind="reasoning_delta", reasoning_delta=reasoning_delta)
                elif event_type == "response.output_item.added":
                    item = object_get(event, "item", {}) or {}
                    if str(object_get(item, "type", "")) in {"function_call", "tool_call"}:
                        item_id = str(
                            object_get(item, "id", "") or object_get(event, "item_id", "") or ""
                        )
                        if item_id:
                            fc_names[item_id] = str(object_get(item, "name", "") or "")
                            fc_index.setdefault(item_id, len(fc_index))
                elif event_type == "response.function_call_arguments.delta":
                    item_id = str(object_get(event, "item_id", "") or "")
                    fc_args[item_id] = fc_args.get(item_id, "") + str(
                        object_get(event, "delta", "") or ""
                    )
                    fc_index.setdefault(item_id, len(fc_index))
                    yield ProviderEvent(
                        kind="tool_call_delta",
                        tool_call_name=fc_names.get(item_id, ""),
                        tool_call_arguments=fc_args[item_id],
                        tool_call_index=fc_index.get(item_id, 0),
                    )
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
                        item_reasoning = _reasoning_from_output_item(item)
                        if item_reasoning and not reasoning_parts:
                            reasoning_parts.append(item_reasoning)
                        normalized = normalize_responses_output_item(item)
                        if isinstance(normalized, ToolCall):
                            tool_calls.append(normalized)
                        elif isinstance(normalized, str) and normalized and not text_parts:
                            text_parts.append(normalized)

        finish = FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP
        response = ModelResponse(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish,
            response_id=response_id,
            raw_provider_name="openai_responses",
        )
        if debug:
            debug.log_response(response)
        yield ProviderEvent(
            kind="completed",
            response=response,
            usage=usage,
        )

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
