from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(slots=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class WebSearchBackend(Protocol):
    async def search(
        self,
        query: str,
        *,
        max_results: int,
        region: str | None = None,
        time_filter: str | None = None,
        timeout: float,
    ) -> list[WebSearchResult]:
        raise NotImplementedError


class DDGSWebSearchBackend:
    """Small ddgs-backed search backend kept behind a replaceable protocol."""

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        region: str | None = None,
        time_filter: str | None = None,
        timeout: float,
    ) -> list[WebSearchResult]:
        try:
            from ddgs import DDGS  # type: ignore
        except Exception as exc:
            raise RuntimeError("ddgs is not installed; web_search backend is unavailable.") from exc

        def run() -> list[WebSearchResult]:
            with DDGS(timeout=timeout) as ddgs:
                raw = ddgs.text(
                    query,
                    region=region,
                    timelimit=time_filter,
                    max_results=max_results,
                )
                return [
                    WebSearchResult(
                        title=str(item.get("title", ""))[:300],
                        url=str(item.get("href") or item.get("url") or ""),
                        snippet=str(item.get("body") or item.get("snippet") or "")[:1000],
                        source="ddgs",
                    )
                    for item in raw
                ]

        return await asyncio.wait_for(asyncio.to_thread(run), timeout=timeout + 1.0)
