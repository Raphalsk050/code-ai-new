from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol

from code_ai.providers.models import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)

if TYPE_CHECKING:
    from code_ai.config.models import AppConfig


@asynccontextmanager
async def closing_stream(stream: Any) -> AsyncIterator[Any]:
    """Yield a provider stream and close it on the way out.

    An inference server generates until the client disconnects, so cancelling a
    turn has to *close* the HTTP response rather than merely stop reading it.
    Without this, pressing Ctrl+C stopped the UI while the model kept running,
    burning the GPU on tokens nobody would ever read - and holding the slot
    against the next request.

    The OpenAI SDK's stream closes via ``close()``, a bare async generator via
    ``aclose()``. Both are accepted, so this works against the SDK and any
    stand-in, and an object offering neither is simply left alone.
    """
    try:
        yield stream
    finally:
        closer = getattr(stream, "close", None) or getattr(stream, "aclose", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                await result


def build_openai_http_client(config: AppConfig) -> Any | None:
    """Build the httpx client the OpenAI SDK should use, honoring ssl_verification.

    SSL verification defaults to disabled (``ssl_verification=False``) so that
    self-signed or intercepting proxies in front of OpenAI-compatible endpoints
    work out of the box, mirroring the native Ollama provider. Returns ``None``
    when httpx is unavailable so the SDK falls back to its own default client.
    """

    try:
        import httpx
    except Exception:  # pragma: no cover - httpx ships with the openai package
        return None
    return httpx.AsyncClient(
        verify=config.ssl_verification,
        timeout=config.budgets.model_timeout(),
    )


class ModelProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        raise NotImplementedError

    async def complete(self, request: ModelRequest) -> ModelResponse:
        text_parts: list[str] = []
        completed: ModelResponse | None = None
        async for event in self.stream(request):
            if event.kind == "text_delta":
                text_parts.append(event.text_delta)
            elif event.kind == "completed" and event.response:
                completed = event.response
        if completed is None:
            completed = ModelResponse(text="".join(text_parts))
        elif not completed.text and text_parts:
            completed.text = "".join(text_parts)
        return completed

    async def close(self) -> None:
        return None
