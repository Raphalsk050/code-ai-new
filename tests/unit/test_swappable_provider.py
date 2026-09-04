"""The handle that lets the model client be replaced without a restart.

The API key, the base URL and the API mode are consumed while the client is
constructed, so changing one means a new client - and the old one is held in
seven places by the time the application is assembled. Everything holds this
instead, so the swap happens in one spot.
"""

from __future__ import annotations

from code_ai.providers.models import ModelRequest, ModelResponse, ProviderCapabilities
from code_ai.providers.swappable import SwappableProvider


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = 0
        self.capabilities = ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest):
        yield f"{self.name}:{request.model}"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=self.name)

    async def close(self) -> None:
        self.closed += 1


def request() -> ModelRequest:
    return ModelRequest(model="m", messages=[])


async def test_every_call_reaches_the_provider_in_force() -> None:
    handle = SwappableProvider(FakeProvider("first"))

    assert (await handle.complete(request())).text == "first"
    assert [chunk async for chunk in handle.stream(request())] == ["first:m"]

    await handle.replace(FakeProvider("second"))

    assert (await handle.complete(request())).text == "second"
    assert [chunk async for chunk in handle.stream(request())] == ["second:m"]


async def test_the_replaced_provider_is_closed() -> None:
    first = FakeProvider("first")
    handle = SwappableProvider(first)

    await handle.replace(FakeProvider("second"))

    assert first.closed == 1


async def test_replacing_a_provider_with_itself_does_not_close_it() -> None:
    only = FakeProvider("only")
    handle = SwappableProvider(only)

    await handle.replace(only)

    assert only.closed == 0
    assert (await handle.complete(request())).text == "only"


async def test_a_provider_that_will_not_close_still_gets_replaced() -> None:
    """Tearing down the old client is not a reason to refuse the new settings."""

    class Stuck(FakeProvider):
        async def close(self) -> None:
            raise RuntimeError("the socket is wedged")

    handle = SwappableProvider(Stuck("stuck"))

    await handle.replace(FakeProvider("fresh"))

    assert (await handle.complete(request())).text == "fresh"


async def test_capabilities_follow_the_swap() -> None:
    """A swap can change what the orchestrator is allowed to ask for."""

    first = FakeProvider("first")
    first.capabilities = ProviderCapabilities(streaming=True, tool_calling=False)
    second = FakeProvider("second")
    second.capabilities = ProviderCapabilities(streaming=True, tool_calling=True)
    handle = SwappableProvider(first)

    assert handle.capabilities.tool_calling is False

    await handle.replace(second)

    assert handle.capabilities.tool_calling is True


async def test_closing_the_handle_closes_what_is_behind_it() -> None:
    only = FakeProvider("only")
    handle = SwappableProvider(only)

    await handle.close()

    assert only.closed == 1
