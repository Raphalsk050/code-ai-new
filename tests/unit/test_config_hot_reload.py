"""Settings that used to need a restart, applied to the running application.

The model name reaches the provider as request data on every call, which is why
changing it always worked live. The API key, the base URL and the API mode are
read once, while the client is being constructed - so applying them means
building a new client and putting it behind the handle everything holds.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from code_ai.app.service import CodeAIApplication
from code_ai.config.models import AppConfig
from code_ai.providers.ollama import NativeOllamaProvider
from code_ai.providers.openai_completions import OpenAIChatCompletionsProvider
from code_ai.providers.swappable import SwappableProvider


def build(tmp_path: Path, **overrides) -> CodeAIApplication:
    settings = {"api_mode": "ollama", "workspace": str(tmp_path), "model": "m"}
    settings.update(overrides)
    config = AppConfig.from_mapping(settings)
    return CodeAIApplication(
        session=SimpleNamespace(config=config),
        event_bus=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        provider=NativeOllamaProvider(config),
        compressor=SimpleNamespace(max_context_tokens=0),
    )


def test_the_provider_is_always_behind_a_swappable_handle(tmp_path) -> None:
    # Even built directly, rather than through bootstrap: a provider held
    # bare would be one nothing could replace.
    assert isinstance(build(tmp_path).provider, SwappableProvider)


async def test_a_new_base_url_reaches_a_new_client(tmp_path) -> None:
    app = build(tmp_path, base_url="http://localhost:11434")
    before = app.provider.current

    app.session.config.base_url = "http://elsewhere:9999"
    await app.reload_provider()

    after = app.provider.current
    assert after is not before
    assert "elsewhere:9999" in after._base_url


async def test_a_new_api_mode_reaches_a_different_kind_of_client(tmp_path) -> None:
    app = build(tmp_path)
    assert isinstance(app.provider.current, NativeOllamaProvider)

    app.session.config.api_mode = "completions"
    await app.reload_provider()

    assert isinstance(app.provider.current, OpenAIChatCompletionsProvider)


async def test_the_context_window_is_repointed_at_the_compressor(tmp_path) -> None:
    app = build(tmp_path)
    app.session.config.budgets.max_context_tokens = 128000

    app.apply_context_window()

    # The compressor recomputes its budget from this on every check, so the
    # next turn plans against the new window without anything else changing.
    assert app.compressor.max_context_tokens == 128000
