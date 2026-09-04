"""A provider handle whose implementation can be replaced while the app runs.

The settings that decide which service to talk to - the API key, the base URL,
the API mode - are all consumed while the client is being constructed, not read
per request the way the model name is. Changing one therefore means building a
new provider, which is why they were the settings that asked for a restart.

Building it is the easy half. By the time the application is assembled the
provider is held in seven places - the orchestrator, the context compressor,
the review service, the sub-agent runtime, the review-service factory, the
facade, and a closure that generates failure lessons - and a change that has to
rebind all of them is complete on the day it is written and wrong a release
later, when the eighth holder appears.

So nothing holds a provider directly. Everything holds one of these, and the
swap is a single assignment behind it.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from code_ai.providers.base import ModelProvider, ProviderCapabilities
from code_ai.providers.models import ModelRequest, ModelResponse, ProviderEvent


class SwappableProvider:
    """Forwards every call to the provider currently in force.

    Implements the :class:`ModelProvider` protocol by delegation, so callers
    cannot tell the difference - which is the point. ``capabilities`` is a
    property rather than a stored value because the answer changes with the
    provider behind it: a swap from Ollama to the Responses API changes what
    the orchestrator is allowed to ask for.
    """

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    @property
    def current(self) -> ModelProvider:
        """The provider in force right now. For display and for tests."""

        return self._provider

    async def replace(self, provider: ModelProvider) -> None:
        """Take ``provider`` into use, then close the one it replaced.

        In that order: the new provider is in force before the old one is torn
        down, so a turn starting during the swap gets a working client rather
        than a closed one. Closing is best-effort - an HTTP client that will
        not shut down cleanly is not a reason to refuse the new settings.
        """

        replaced = self._provider
        if replaced is provider:
            return
        self._provider = provider
        with contextlib.suppress(Exception):
            await replaced.close()

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._provider.capabilities

    def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        # Not ``async def``: the underlying stream is an async generator, and
        # returning it directly keeps it one - wrapping it in a coroutine here
        # would break every ``async for`` over it.
        return self._provider.stream(request)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await self._provider.complete(request)

    async def close(self) -> None:
        await self._provider.close()
