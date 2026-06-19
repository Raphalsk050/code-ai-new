from __future__ import annotations

import asyncio
import json
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
from code_ai.providers.translation import (
    messages_to_chat,
    object_get,
    parse_arguments,
    tools_to_chat,
)


def _finish_reason(value: Any) -> FinishReason:
    text = str(value or "").lower()
    if text in {"stop", "tool_calls", "length"}:
        return FinishReason(text)
    return FinishReason.UNKNOWN


def _usage_from_object(value: Any, *, source: str = "openai") -> TokenUsage | None:
    if not value:
        return None
    prompt = object_get(value, "prompt_tokens", object_get(value, "input_tokens", 0))
    completion = object_get(value, "completion_tokens", object_get(value, "output_tokens", 0))
    return TokenUsage.from_counts(
        input_tokens=int(prompt or 0),
        output_tokens=int(completion or 0),
        exact=True,
        source=source,
    )


def _is_transient_exception(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status in {408, 409, 429, 500, 502, 503, 504}


class OpenAIChatCompletionsProvider:
    """OpenAI-compatible Chat Completions adapter."""

    def __init__(self, config: AppConfig) -> None:
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # pragma: no cover - dependency availability
            raise ProviderError(
                "The openai package is required for Chat Completions mode."
            ) from exc

        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.provider_api_key(),
            base_url=config.base_url,
            timeout=config.budgets.model_timeout(),
            max_retries=0,
        )
        self._capabilities = ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            provider_reported_usage=True,
            remote_conversation_state=False,
            native_tokenization=False,
            image_support=False,
        )
        self._stream_options_supported = True

    @property
    def capabilities(self) -> ProviderCapabilities:
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
                text="".join(text_parts), raw_provider_name="openai_chat_completions"
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
                if not _is_transient_exception(exc) or attempts >= 2:
                    raise ProviderError(f"Chat Completions request failed: {exc}") from exc
                attempts += 1
                await asyncio.sleep(min(2.0, 0.25 * (2**attempts)) + random.random() * 0.1)

    async def _stream_once(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages_to_chat(request.messages),
            "stream": True,
        }
        if request.max_output_tokens:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.tools:
            kwargs["tools"] = tools_to_chat(request.tools)
            kwargs["tool_choice"] = "auto"
        if self._stream_options_supported:
            kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if self._stream_options_supported and "stream_options" in str(exc):
                self._stream_options_supported = False
                yield ProviderEvent(
                    kind="warning",
                    warning="Endpoint rejected streaming usage options; retrying without them.",
                )
                async for event in self._stream_once(request):
                    yield event
                return
            if _is_transient_exception(exc):
                raise TransientProviderError(str(exc)) from exc
            raise

        tool_fragments: dict[int, dict[str, str]] = {}
        text_parts: list[str] = []
        usage: TokenUsage | None = None
        finish = FinishReason.UNKNOWN
        async for chunk in stream:
            usage = _usage_from_object(object_get(chunk, "usage")) or usage
            choices = object_get(chunk, "choices", []) or []
            if not choices:
                continue
            choice = choices[0]
            finish = _finish_reason(object_get(choice, "finish_reason")) or finish
            delta = object_get(choice, "delta", {})
            content = object_get(delta, "content", "")
            if content:
                text_parts.append(content)
                yield ProviderEvent(kind="text_delta", text_delta=content)

            for tool_delta in object_get(delta, "tool_calls", []) or []:
                index = int(object_get(tool_delta, "index", 0) or 0)
                fragment = tool_fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                fragment["id"] += str(object_get(tool_delta, "id", "") or "")
                function = object_get(tool_delta, "function", {}) or {}
                fragment["name"] += str(object_get(function, "name", "") or "")
                fragment["arguments"] += str(object_get(function, "arguments", "") or "")

        tool_calls: list[ToolCall] = []
        for index, fragment in sorted(tool_fragments.items()):
            name = fragment["name"]
            if not name:
                continue
            tool_calls.append(
                ToolCall(
                    id=fragment["id"] or f"tool_call_{index}",
                    name=name,
                    arguments=parse_arguments(fragment["arguments"] or "{}"),
                )
            )
        response = ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=FinishReason.TOOL_CALLS if tool_calls else finish,
            raw_provider_name="openai_chat_completions",
        )
        yield ProviderEvent(kind="completed", response=response, usage=usage)

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result


def assemble_streamed_tool_call_fragments(fragments: list[dict[str, Any]]) -> list[ToolCall]:
    """Testable helper for Chat Completions streamed tool-call deltas."""

    merged: dict[int, dict[str, str]] = {}
    for item in fragments:
        index = int(item.get("index", 0))
        target = merged.setdefault(index, {"id": "", "name": "", "arguments": ""})
        target["id"] += str(item.get("id", "") or "")
        function = item.get("function") or {}
        target["name"] += str(function.get("name", "") or "")
        target["arguments"] += str(function.get("arguments", "") or "")
    calls: list[ToolCall] = []
    for index, item in sorted(merged.items()):
        calls.append(
            ToolCall(
                id=item["id"] or f"tool_call_{index}",
                name=item["name"],
                arguments=json.loads(item["arguments"] or "{}"),
            )
        )
    return calls
