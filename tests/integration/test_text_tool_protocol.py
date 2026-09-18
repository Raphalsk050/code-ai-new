"""An endpoint served without a tool parser must still be able to use tools.

A vLLM started without ``--enable-auto-tool-choice`` answers any request
carrying ``tools`` with a 400, which used to make every model on that server
unusable. The way through is to put the catalog in the prompt and read the
calls back out of the reply.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolCallingUnsupportedError
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)


class RefusingProvider:
    """Refuses any request carrying tools, the way a bare vLLM does."""

    def __init__(self, reply: Callable[[int], ModelResponse], *, native: bool = True) -> None:
        self.reply = reply
        self.requests: list[ModelRequest] = []
        self.refusals = 0
        self._capabilities = ProviderCapabilities(
            streaming=True, tool_calling=native, provider_reported_usage=False
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        if request.tools:
            self.refusals += 1
            self._capabilities.tool_calling = False
            raise ToolCallingUnsupportedError(
                '"auto" tool choice requires --enable-auto-tool-choice and '
                "--tool-call-parser to be set"
            )
        self.requests.append(request)
        response = self.reply(len(self.requests) - 1)
        if response.text:
            yield ProviderEvent(kind="text_delta", text_delta=response.text)
        yield ProviderEvent(kind="completed", response=response)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


def prose(text: str) -> ModelResponse:
    return ModelResponse(text=text, finish_reason=FinishReason.STOP)


def call_markup(name: str, **arguments) -> str:
    import json

    payload = json.dumps({"name": name, "arguments": arguments})
    return f"<tool_call>\n{payload}\n</tool_call>"


def app_for(tmp_path, provider, **settings):
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake", **settings}
    )
    return build_application(config=config, provider=provider)


async def run(app, message: str):
    await app.start()
    try:
        return await app.submit_user_message(message)
    finally:
        await app.close()


def protocol_text(request: ModelRequest) -> str:
    return "\n".join(
        message.content or "" for message in request.messages if message.role == "user"
    )


async def test_a_refused_request_comes_back_with_the_catalog_in_the_prompt(tmp_path) -> None:
    provider = RefusingProvider(lambda _step: prose("ok"))

    await run(app_for(tmp_path, provider), "oi")

    assert provider.refusals == 1
    # The retry carried no tools field at all...
    assert provider.requests[0].tools == []
    # ...but the model was still told what it can call, and how.
    text = protocol_text(provider.requests[0])
    assert "Tool protocol for this session" in text
    assert "<tool_call>" in text
    assert "read_file" in text


async def test_the_endpoint_is_only_probed_once(tmp_path) -> None:
    def reply(step: int) -> ModelResponse:
        return prose(call_markup("list_files", path=".")) if step == 0 else prose("pronto")

    provider = RefusingProvider(reply)

    await run(app_for(tmp_path, provider), "liste o diretorio")

    assert provider.refusals == 1
    assert all(request.tools == [] for request in provider.requests)


async def test_a_call_written_as_text_actually_runs(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("segredo\n", encoding="utf-8")

    def reply(step: int) -> ModelResponse:
        if step == 0:
            return prose(call_markup("read_file", path="note.txt"))
        return prose("O arquivo tem um segredo.")

    provider = RefusingProvider(reply)

    result = await run(app_for(tmp_path, provider), "leia note.txt")

    results = [m for m in provider.requests[-1].messages if m.role == "tool"]
    assert results and "segredo" in results[-1].content
    assert "segredo" in (result.text or "")


async def test_text_mode_can_be_forced_without_waiting_for_a_refusal(tmp_path) -> None:
    provider = RefusingProvider(lambda _step: prose("ok"), native=True)

    await run(app_for(tmp_path, provider, tool_calling="text"), "oi")

    assert provider.refusals == 0
    assert "Tool protocol for this session" in protocol_text(provider.requests[0])


async def test_native_mode_keeps_the_structured_channel(tmp_path) -> None:
    class Native(RefusingProvider):
        async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
            self.requests.append(request)
            yield ProviderEvent(kind="completed", response=prose("ok"))

    provider = Native(lambda _step: prose("ok"))

    await run(app_for(tmp_path, provider, tool_calling="native"), "oi")

    assert provider.requests[0].tools
    assert "Tool protocol for this session" not in protocol_text(provider.requests[0])


async def test_an_unknown_tool_calling_mode_is_refused(tmp_path) -> None:
    with pytest.raises(Exception, match="Unsupported tool_calling"):
        AppConfig.from_mapping(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "model": "fake",
                "tool_calling": "sometimes",
            }
        )
