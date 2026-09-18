"""A session with every tool switched off must still answer, end to end.

The bug this pins down: with the tools off the model was still told to call
list_files, write_file and complete_task, so it announced the call, printed the
markup as text because there was no structured channel, and the runtime burned
its retries correcting it toward a format it could not use. The user saw "I'll
check the directory" and then nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)

_MARKUP = '<tool_call>{"name": "list_files", "arguments": {"path": "."}}</tool_call>'
# Every tool the prompt used to name unconditionally.
_TOOL_NAMES = (
    "list_files",
    "read_file",
    "search_index",
    "write_file",
    "edit_code",
    "execute_command",
    "submit_plan",
    "complete_task",
    "web_search",
    "dispatch_agent",
)


class ScriptedProvider:
    """Replays one scripted step per request and keeps every request it got."""

    def __init__(self, reply: Callable[[int], ModelResponse]) -> None:
        self.reply = reply
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
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


async def run_turn(tmp_path, provider, message: str, *, disable_all: bool = True):
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    app = build_application(config=config, provider=provider)
    if disable_all:
        registry = app.orchestrator.tool_registry
        registry.set_disabled(registry.registered_names())
    await app.start()
    try:
        return await app.submit_user_message(message)
    finally:
        await app.close()


def prompt_of(provider: ScriptedProvider) -> str:
    return provider.requests[0].messages[0].content


async def test_the_prompt_stops_naming_tools_the_session_does_not_have(tmp_path) -> None:
    provider = ScriptedProvider(lambda _step: prose("ok"))

    await run_turn(tmp_path, provider, "oi")

    prompt = prompt_of(provider)
    assert "Every tool is switched off in this session" in prompt
    for name in _TOOL_NAMES:
        assert name not in prompt, name


async def test_the_full_prompt_comes_back_when_the_tools_do(tmp_path) -> None:
    provider = ScriptedProvider(lambda _step: prose("ok"))

    await run_turn(tmp_path, provider, "oi", disable_all=False)

    prompt = prompt_of(provider)
    assert "Every tool is switched off" not in prompt
    assert "write_file" in prompt


async def test_asking_for_the_directory_ends_in_one_step_with_an_answer(tmp_path) -> None:
    provider = ScriptedProvider(lambda _step: prose("Sem ferramentas nao consigo listar."))

    result = await run_turn(tmp_path, provider, "veja o que tem no diretorio atual")

    assert len(provider.requests) == 1
    assert provider.requests[0].tools == []
    assert result.error is None
    assert result.wind_down_reason is None
    assert "Sem ferramentas" in (result.text or "")


async def test_printed_tool_markup_is_corrected_once_not_twice(tmp_path) -> None:
    def reply(step: int) -> ModelResponse:
        return prose(_MARKUP) if step == 0 else prose("Nao tenho ferramentas aqui.")

    result = await run_turn(tmp_path, provider := ScriptedProvider(reply), "liste o diretorio")

    assert len(provider.requests) == 2
    correction = provider.requests[1].messages[-1].content
    assert "no tools in this session" in correction
    # The old correction asked for a channel that does not exist here.
    assert "function-calling interface" not in correction
    assert result.text == "Nao tenho ferramentas aqui."


async def test_a_reply_that_is_only_markup_never_reaches_the_user(tmp_path) -> None:
    result = await run_turn(
        tmp_path, ScriptedProvider(lambda _step: prose(_MARKUP)), "liste o diretorio"
    )

    assert "<tool_call>" not in (result.text or "")
    assert "list_files" not in (result.text or "")
    assert "/doctor tools" in (result.text or "")


async def test_prose_around_the_markup_survives(tmp_path) -> None:
    def reply(step: int) -> ModelResponse:
        return prose(f"Vou checar o diretorio.\n{_MARKUP}")

    result = await run_turn(tmp_path, ScriptedProvider(reply), "liste o diretorio")

    assert "<tool_call>" not in (result.text or "")
    assert "Vou checar o diretorio." in (result.text or "")


async def test_a_change_request_is_not_nudged_toward_tools_that_are_gone(tmp_path) -> None:
    provider = ScriptedProvider(lambda _step: prose("Aqui vai o codigo: print('hi')"))

    result = await run_turn(tmp_path, provider, "crie um arquivo hello.py com um hello world")

    assert len(provider.requests) == 1
    # No "Runtime task state" block either: it only names tools to call.
    assert all(
        "Runtime task state" not in (message.content or "")
        for message in provider.requests[0].messages
    )
    assert "print('hi')" in (result.text or "")


async def test_the_markup_retry_still_asks_for_the_proper_format_when_tools_exist(
    tmp_path,
) -> None:
    def reply(step: int) -> ModelResponse:
        return prose(_MARKUP) if step == 0 else prose("pronto")

    # A tool the markup does *not* name, so recovery cannot execute the call.
    provider = ScriptedProvider(reply)
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "disabled_tools": ["list_files"],
        }
    )
    app = build_application(config=config, provider=provider)
    await app.start()
    try:
        await app.submit_user_message("liste o diretorio")
    finally:
        await app.close()

    assert any(
        "function-calling interface" in (message.content or "")
        for message in provider.requests[1].messages
    )
