from __future__ import annotations

from types import SimpleNamespace

from code_ai.app.service import CodeAIApplication


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.request = None

    async def complete(self, request):
        self.request = request
        return SimpleNamespace(text=self.text)


def _app(
    provider: _FakeProvider, *, model: str = "main", inline_model: str = ""
) -> CodeAIApplication:
    return CodeAIApplication(
        session=SimpleNamespace(config=SimpleNamespace(model=model, inline_model=inline_model)),
        event_bus=None,
        orchestrator=None,
        provider=provider,
        compressor=None,
    )


async def test_inline_complete_strips_markdown_fence() -> None:
    provider = _FakeProvider("```python\n    return a + b\n```")
    app = _app(provider)
    out = await app.inline_complete(prefix="def add(a, b):\n", language="python")
    assert out == "    return a + b"


async def test_inline_complete_uses_inline_model_when_set() -> None:
    provider = _FakeProvider("x")
    app = _app(provider, model="big", inline_model="small-fast")
    await app.inline_complete(prefix="foo")
    assert provider.request.model == "small-fast"


async def test_inline_complete_falls_back_to_main_model() -> None:
    provider = _FakeProvider("x")
    app = _app(provider, model="big", inline_model="   ")
    await app.inline_complete(prefix="foo")
    assert provider.request.model == "big"


async def test_inline_complete_skips_empty_prefix() -> None:
    provider = _FakeProvider("should not be used")
    app = _app(provider)
    assert await app.inline_complete(prefix="   \n  ") == ""
    assert provider.request is None  # provider never called


async def test_inline_complete_windows_large_context() -> None:
    provider = _FakeProvider("done")
    app = _app(provider)
    await app.inline_complete(prefix="A" * 12000, suffix="B" * 6000, language="python")
    content = provider.request.messages[-1].content
    # Only the trailing 8000 chars of prefix and leading 3000 of suffix are sent.
    assert content.count("A") == 8000
    assert content.count("B") == 3000
    assert "<CURSOR>" in content


async def test_inline_complete_strips_reasoning_block() -> None:
    provider = _FakeProvider("<think>let me consider the types</think>\n    return a + b")
    app = _app(provider)
    out = await app.inline_complete(prefix="def add(a, b):\n", language="python")
    assert out == "    return a + b"


async def test_inline_complete_uses_large_output_budget() -> None:
    provider = _FakeProvider("x")
    app = _app(provider)
    await app.inline_complete(prefix="foo")
    # Reasoning models need room to finish thinking before emitting the snippet.
    assert provider.request.max_output_tokens == 32768
