from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from code_ai.config.models import AppConfig
from code_ai.core.errors import (
    ImageLimitError,
    ProviderError,
    ToolCallingUnsupportedError,
    TransientProviderError,
)
from code_ai.providers.base import build_openai_http_client, closing_stream
from code_ai.providers.debug import ModelDebugLogger
from code_ai.providers.images import parse_image_limit
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    TokenUsage,
    ToolCall,
)
from code_ai.providers.reasoning import settle_reasoning
from code_ai.providers.translation import (
    messages_to_chat,
    normalize_chat_messages,
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


def _carries_images(request: ModelRequest) -> bool:
    return any(getattr(message, "images", None) for message in request.messages)


def _worth_retrying(exc: Exception, request: ModelRequest) -> bool:
    """Whether re-sending this exact request could plausibly do better.

    A 500 on a request carrying images is the server saying it could not process
    that payload, and the payload does not change between attempts - so the
    retries buy nothing and cost the caller the whole backoff schedule before it
    can react. Overload and timeout codes still retry normally, images or not,
    because those really are about the moment rather than the content.
    """

    if not _is_transient_exception(exc):
        return False
    if getattr(exc, "status_code", None) == 500 and _carries_images(request):
        return False
    return True


# Field names an endpoint may reject when it does not understand a sampling
# control we sent. Used to decide whether a failed request can be retried
# without sampling kwargs rather than surfaced as a hard error.
_SAMPLING_ERROR_HINTS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "frequency_penalty",
    "extra_body",
    "reasoning",
    "unsupported parameter",
    "unsupported value",
    "unknown field",
)


def _looks_like_sampling_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _SAMPLING_ERROR_HINTS)


# What a server says when it was started without the machinery to parse tool
# calls. vLLM is the one that matters here: without --enable-auto-tool-choice
# and --tool-call-parser it refuses the request outright instead of ignoring
# the field, so a perfectly good model reads as a broken one.
_TOOL_SUPPORT_ERROR_HINTS = (
    "tool_choice",
    "tool choice",
    "tool-call-parser",
    "tool_call_parser",
    "enable-auto-tool-choice",
    "tool parser",
    "tool calling",
    "does not support tools",
    "tools are not supported",
    "tools is not supported",
    "function calling is not",
)


def _looks_like_tool_support_error(exc: Exception) -> bool:
    """Whether this refusal is about tools rather than about the request.

    Transient codes are excluded deliberately: an overloaded server that
    happens to mention tools in its message must not cost the session its
    structured tool channel for good.
    """

    if _is_transient_exception(exc):
        return False
    text = str(exc).lower()
    return any(hint in text for hint in _TOOL_SUPPORT_ERROR_HINTS)


def _controls(sampling: dict[str, Any]) -> set[str]:
    """Every sampling control a request carries, extra_body's included."""
    extra = sampling.get("extra_body") or {}
    return {key for key in sampling if key != "extra_body"} | set(extra)


def _named_controls(error: str, sampling: dict[str, Any]) -> set[str]:
    """The controls a refusal names. "reasoning_effort" holds "reasoning", so
    the longest names are matched first and their text taken out."""
    text = error.lower()
    named: set[str] = set()
    for key in sorted(_controls(sampling), key=len, reverse=True):
        if key.lower() in text:
            named.add(key)
            text = text.replace(key.lower(), " ")
    return named


def _without(sampling: dict[str, Any], refused: set[str]) -> dict[str, Any]:
    if not refused:
        return sampling
    kept = {
        key: value
        for key, value in sampling.items()
        if key not in refused and key != "extra_body"
    }
    extra = {
        key: value
        for key, value in (sampling.get("extra_body") or {}).items()
        if key not in refused
    }
    if extra:
        kept["extra_body"] = extra
    return kept


_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")


def _reasoning_delta(value: Any) -> str:
    """Extract reasoning text from an OpenAI-compatible delta/message.

    Reasoning models served through vLLM/SGLang, Ollama, and similar
    OpenAI-compatible backends expose chain-of-thought in a non-standard
    ``reasoning_content``/``reasoning``/``thinking`` field alongside ``content``.
    """
    for key in _REASONING_KEYS:
        text = object_get(value, key, "")
        if text:
            return str(text)
    return ""


def _reasoning_key(value: Any) -> str | None:
    """Which non-standard field this server streams its reasoning under."""
    for key in _REASONING_KEYS:
        if object_get(value, key, ""):
            return key
    return None


# How a schema-strict endpoint words "that message field is not allowed".
_FIELD_REFUSAL_HINTS = (
    "additional properties",
    "extra inputs",
    "extra_forbidden",
    "unrecognized",
    "unexpected",
    "unknown field",
)


def _looks_like_replay_refusal(exc: Exception, field: str) -> bool:
    # A strict schema says 400/422; a template that cannot render the field says
    # 500 (gpt-oss raises on content and thinking together). One retry without
    # the reasoning settles it either way.
    if getattr(exc, "status_code", None) in {400, 422, 500}:
        return True
    text = str(exc).lower()
    return field in text or any(hint in text for hint in _FIELD_REFUSAL_HINTS)


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
            http_client=build_openai_http_client(config),
        )
        self._capabilities = ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            provider_reported_usage=True,
            remote_conversation_state=False,
            native_tokenization=False,
            # Multipart image_url content is standard Chat Completions; vision
            # support ultimately depends on the model behind the endpoint.
            image_support=True,
        )
        self._stream_options_supported = True
        self._sampling_supported = True
        # Sampling controls the endpoint refused by name. Only those are left
        # out: vLLM refusing reasoning_effort "xhigh" took enable_thinking=false
        # down with it, and "none" could no longer switch thinking off.
        self._refused_sampling: set[str] = set()
        # Cleared the first time the endpoint refuses a request for carrying
        # tools. It is a property of how the server was started, so it stays
        # cleared for the session rather than being probed again every step.
        self._tools_supported = True
        # The field the server streams reasoning under, learned from its first
        # reasoning delta; the turn's thinking goes back under that same name.
        self._reasoning_field: str | None = None
        self._reasoning_replay = True

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def retry_sampling(self) -> None:
        """Send the sampling controls again after the endpoint refused them once.

        A refusal switches them off for the rest of the session, which is right
        for values nobody touched and wrong right after the user changed them:
        the new values would never reach the endpoint. The next request tries
        again and, if refused, falls back the same way.
        """

        self._sampling_supported = True
        self._refused_sampling.clear()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        # Every layer needs closing, not just the innermost one: leaving an
        # `async for` early suspends the generator under it rather than closing
        # it, so without aclosing() at each hop the HTTP response at the bottom
        # stays open and the server keeps generating.
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
                text="".join(text_parts), raw_provider_name="openai_chat_completions"
            )
        elif not completed.text:
            completed.text = "".join(text_parts)
        return settle_reasoning(completed)

    async def _stream_with_retry(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        attempts = 0
        while True:
            try:
                async with aclosing(self._stream_once(request)) as events:
                    async for event in events:
                        yield event
                return
            except ImageLimitError as exc:
                # Keeps the limit intact through the wrapping, so the caller can
                # fit the conversation to it rather than guessing.
                raise ImageLimitError(
                    f"Chat Completions request failed: {exc}", limit=exc.limit
                ) from exc
            except Exception as exc:
                if not _worth_retrying(exc, request) or attempts >= 2:
                    raise ProviderError(f"Chat Completions request failed: {exc}") from exc
                attempts += 1
                await asyncio.sleep(min(2.0, 0.25 * (2**attempts)) + random.random() * 0.1)

    async def _stream_once(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        replay_field = self._reasoning_field if self._reasoning_replay else None
        messages = messages_to_chat(
            normalize_chat_messages(request.messages), reasoning_field=replay_field
        )
        replaying = replay_field is not None and any(replay_field in m for m in messages)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
        }
        if request.max_output_tokens:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.tools and self._tools_supported:
            kwargs["tools"] = tools_to_chat(request.tools, strict=self._config.strict_tools)
            kwargs["tool_choice"] = "auto"
        if self._stream_options_supported:
            kwargs["stream_options"] = {"include_usage": True}
        sampling: dict[str, Any] = {}
        if self._sampling_supported:
            sampling = _without(
                self._config.sampling.chat_completion_kwargs(request.reasoning_effort),
                self._refused_sampling,
            )
            kwargs.update(sampling)

        debug = ModelDebugLogger.for_request(self._config, provider="openai_chat_completions")
        if debug:
            debug.log_request(kwargs)

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            limit = parse_image_limit(str(exc))
            if limit is not None:
                # The endpoint just named what it accepts. Remember it, so the
                # rest of the session sizes its requests to fit instead of
                # rediscovering the same refusal on every attachment.
                self._capabilities.max_images_per_request = limit
                raise ImageLimitError(str(exc), limit=limit) from exc
            if request.tools and self._tools_supported and _looks_like_tool_support_error(exc):
                # Checked before the sampling fallback below, whose hints
                # ("unsupported parameter", "unknown field") also match a tools
                # refusal and would have retried forever without the tools ever
                # being the thing that was dropped.
                self._tools_supported = False
                self._capabilities.tool_calling = False
                raise ToolCallingUnsupportedError(str(exc)) from exc
            if replaying and _looks_like_replay_refusal(exc, replay_field):
                # Before the sampling fallback, whose "reasoning" hint would
                # otherwise drop the sampling knobs for a message-field refusal.
                self._reasoning_replay = False
                yield ProviderEvent(
                    kind="warning",
                    warning="Endpoint refused the replayed reasoning; continuing without it.",
                )
                async with aclosing(self._stream_once(request)) as events:
                    async for event in events:
                        yield event
                return
            if self._stream_options_supported and "stream_options" in str(exc):
                self._stream_options_supported = False
                yield ProviderEvent(
                    kind="warning",
                    warning="Endpoint rejected streaming usage options; retrying without them.",
                )
                async with aclosing(self._stream_once(request)) as events:
                    async for event in events:
                        yield event
                return
            if self._sampling_supported and _looks_like_sampling_error(exc):
                named = _named_controls(str(exc), sampling) - self._refused_sampling
                if named:
                    self._refused_sampling |= named
                    warning = (
                        f"Endpoint refused {', '.join(sorted(named))}; retrying without it."
                    )
                else:
                    self._sampling_supported = False
                    warning = "Endpoint rejected sampling parameters; retrying without them."
                yield ProviderEvent(kind="warning", warning=warning)
                async with aclosing(self._stream_once(request)) as events:
                    async for event in events:
                        yield event
                return
            if _is_transient_exception(exc):
                raise TransientProviderError(str(exc)) from exc
            raise

        tool_fragments: dict[int, dict[str, str]] = {}
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: TokenUsage | None = None
        finish = FinishReason.UNKNOWN
        # Held open in a context manager so cancellation actually reaches the
        # server. An inference server keeps generating until the client
        # disconnects, so abandoning the iterator without closing the HTTP
        # response leaves the model running - the user cancels, the UI stops,
        # and the GPU carries on producing tokens nobody will read.
        try:
            async with closing_stream(stream):
                async for chunk in stream:
                    if debug:
                        debug.log_raw_chunk(chunk)
                    usage = _usage_from_object(object_get(chunk, "usage")) or usage
                    choices = object_get(chunk, "choices", []) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish = _finish_reason(object_get(choice, "finish_reason")) or finish
                    delta = object_get(choice, "delta", {})
                    reasoning = _reasoning_delta(delta)
                    if reasoning:
                        if self._reasoning_field is None:
                            self._reasoning_field = _reasoning_key(delta)
                        reasoning_parts.append(reasoning)
                        yield ProviderEvent(kind="reasoning_delta", reasoning_delta=reasoning)
                    content = object_get(delta, "content", "")
                    if content:
                        text_parts.append(content)
                        yield ProviderEvent(kind="text_delta", text_delta=content)

                    for tool_delta in object_get(delta, "tool_calls", []) or []:
                        index = int(object_get(tool_delta, "index", 0) or 0)
                        fragment = tool_fragments.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        fragment["id"] += str(object_get(tool_delta, "id", "") or "")
                        function = object_get(tool_delta, "function", {}) or {}
                        fragment["name"] += str(object_get(function, "name", "") or "")
                        fragment["arguments"] += str(object_get(function, "arguments", "") or "")
                        # Surface streaming progress so the UI isn't frozen while a large
                        # tool call (e.g. write_file's content) accumulates.
                        yield ProviderEvent(
                            kind="tool_call_delta",
                            tool_call_name=fragment["name"],
                            tool_call_arguments=fragment["arguments"],
                            tool_call_index=index,
                        )

        except Exception as exc:
            # A request that dies mid-stream (a read timeout above all) left no
            # trace at all: the transcript simply stopped, so a turn killed by
            # the clock was indistinguishable from one that never answered.
            if debug:
                debug.log_error(exc)
            raise
        tool_calls: list[ToolCall] = []
        for index, fragment in sorted(tool_fragments.items()):
            name = fragment["name"]
            if not name:
                continue
            try:
                arguments = parse_arguments(fragment["arguments"] or "{}")
            except (ValueError, TypeError) as exc:
                # A single malformed tool call must not abort the whole session.
                # Drop it with a warning so the agent can retry instead of the
                # JSONDecodeError surfacing as a fatal "request failed".
                yield ProviderEvent(
                    kind="warning",
                    warning=f"Discarded tool call {name!r} with unparseable arguments: {exc}",
                )
                continue
            tool_calls.append(
                ToolCall(
                    id=fragment["id"] or f"tool_call_{index}",
                    name=name,
                    arguments=arguments,
                )
            )
        response = ModelResponse(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=FinishReason.TOOL_CALLS if tool_calls else finish,
            raw_provider_name="openai_chat_completions",
        )
        if debug:
            debug.log_response(response)
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
                arguments=parse_arguments(item["arguments"] or "{}"),
            )
        )
    return calls
