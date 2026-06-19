from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from code_ai.providers.models import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
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
