from __future__ import annotations

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.web import backend, web_search
from code_ai.tools.web.backend import SearchRequest, WebPageText, WebSearchResult
from code_ai.tools.web.web_search import WebSearchTool
from code_ai.util.paths import WorkspacePolicy


def test_web_search_cascade_uses_direct_provider_before_ddgs(monkeypatch) -> None:
    calls: list[str] = []

    def failing_provider(request: SearchRequest) -> list[WebSearchResult]:
        calls.append("duckduckgo_curl")
        raise RuntimeError("blocked")

    def successful_provider(request: SearchRequest) -> list[WebSearchResult]:
        calls.append("duckduckgo_post")
        return [
            WebSearchResult(
                title="Official schedule",
                url="https://www.fifa.com/en/tournaments/mens/worldcup",
                snippet="Match schedule",
                source="duckduckgo",
            )
        ]

    def unused_provider(request: SearchRequest) -> list[WebSearchResult]:
        calls.append("ddgs")
        return []

    monkeypatch.setattr(backend.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(backend, "_search_duckduckgo_curl", failing_provider)
    monkeypatch.setattr(backend, "_search_duckduckgo_post", successful_provider)
    monkeypatch.setattr(backend, "_search_searxng", unused_provider)
    monkeypatch.setattr(backend, "_search_google", unused_provider)
    monkeypatch.setattr(backend, "_search_ddgs_package", unused_provider)

    results = backend._search_with_cascade(
        SearchRequest(
            query="world cup schedule today",
            max_results=5,
            region="br-pt",
            time_filter=None,
            timeout=5,
        )
    )

    assert calls == ["duckduckgo_curl", "duckduckgo_post"]
    assert results[0].source == "duckduckgo"


def test_google_result_parser_extracts_redirect_target() -> None:
    body = """
    <html><body>
      <a href="/url?q=https%3A%2F%2Fwww.fifa.com%2Fen%2Ftournaments%2Fmens%2Fworldcup">
        <h3>FIFA World Cup schedule</h3>
      </a>
    </body></html>
    """
    entries = backend._parse_google_results_fallback(body, 5)
    assert entries == [
        {
            "title": "FIFA World Cup schedule",
            "url": "https://www.fifa.com/en/tournaments/mens/worldcup",
            "snippet": "",
        }
    ]


async def test_web_search_tool_rejects_empty_results(tmp_path) -> None:
    class EmptyBackend:
        async def search(self, *args, **kwargs) -> list[WebSearchResult]:
            return []

    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    context = ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(config.workspace),
        event_bus=AsyncEventBus(),
    )

    with pytest.raises(ToolExecutionError, match="no usable results"):
        await WebSearchTool(backend=EmptyBackend()).execute({"query": "anything"}, context)


async def test_web_search_fetches_pages_for_current_queries(tmp_path, monkeypatch) -> None:
    class ResultBackend:
        async def search(self, *args, **kwargs) -> list[WebSearchResult]:
            return [
                WebSearchResult(
                    title="Official schedule",
                    url="https://www.fifa.com/en/tournaments/mens/worldcup",
                    snippet="Schedule",
                    source="test",
                )
            ]

    def fake_fetch_page_text(url: str, *, timeout: float, max_chars: int) -> WebPageText:
        return WebPageText(
            url=url,
            title="Official schedule",
            text="Brazil vs France - 16:00",
        )

    monkeypatch.setattr(web_search, "fetch_page_text", fake_fetch_page_text)
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    context = ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(config.workspace),
        event_bus=AsyncEventBus(),
    )

    result = await WebSearchTool(backend=ResultBackend()).execute(
        {"query": "quem joga hoje na copa do mundo"},
        context,
    )

    assert result["pages"][0]["text"] == "Brazil vs France - 16:00"
