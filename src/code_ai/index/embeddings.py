"""Embedding clients for semantic retrieval over the code index.

Two wire formats cover every provider this project talks to: Ollama's native
``/api/embed`` and the OpenAI-compatible ``/v1/embeddings`` that vLLM,
LM Studio, llama.cpp, OpenAI itself and most gateways expose. Both take a
batch of texts and return one vector per text, in order.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urljoin

from code_ai.config.models import AppConfig
from code_ai.core.errors import ProviderError


class EmbeddingClient(Protocol):
    @property
    def model(self) -> str:
        raise NotImplementedError

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def close(self) -> None:
        return None


def _openai_embeddings_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if not trimmed.endswith("/v1"):
        trimmed += "/v1"
    return trimmed + "/embeddings"


def _ollama_embed_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        trimmed = trimmed[:-3]
    return urljoin(trimmed.rstrip("/") + "/", "api/embed")


class HttpEmbeddingClient:
    """Embeddings over HTTP, speaking Ollama's or OpenAI's embedding endpoint."""

    def __init__(
        self,
        *,
        api_mode: str,
        base_url: str,
        model: str,
        api_key: str = "",
        ssl_verification: bool = False,
        timeout_s: float = 120.0,
    ) -> None:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - httpx is a hard dependency
            raise ProviderError("The httpx package is required for embeddings.") from exc
        if api_mode not in {"ollama", "openai"}:
            raise ProviderError(f"Unsupported embedding api_mode: {api_mode}")
        self._api_mode = api_mode
        self._model = model
        self._url = (
            _ollama_embed_url(base_url)
            if api_mode == "ollama"
            else _openai_embeddings_url(base_url)
        )
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            timeout=timeout_s, verify=ssl_verification, headers=headers
        )
        self._httpx = httpx

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        try:
            response = await self._client.post(self._url, json=payload)
            response.raise_for_status()
            data = response.json()
        except self._httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300].strip()
            raise ProviderError(
                f"Embedding request failed ({exc.response.status_code}) at {self._url}: {detail}"
            ) from exc
        except self._httpx.HTTPError as exc:
            raise ProviderError(f"Embedding request failed at {self._url}: {exc}") from exc
        except ValueError as exc:
            raise ProviderError("Embedding endpoint returned a non-JSON body.") from exc
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"Embedding endpoint error: {data['error']}")
        vectors = _parse_vectors(data, expected=len(texts))
        return vectors

    async def close(self) -> None:
        await self._client.aclose()


def _parse_vectors(data: Any, *, expected: int) -> list[list[float]]:
    vectors: list[list[float]] = []
    if isinstance(data, dict) and isinstance(data.get("embeddings"), list):
        # Ollama /api/embed
        vectors = [[float(v) for v in vector] for vector in data["embeddings"]]
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        # OpenAI /v1/embeddings: items carry an index; order by it defensively.
        items = sorted(
            (item for item in data["data"] if isinstance(item, dict)),
            key=lambda item: int(item.get("index", 0)),
        )
        vectors = [[float(v) for v in item.get("embedding") or []] for item in items]
    elif isinstance(data, dict) and isinstance(data.get("embedding"), list):
        # Legacy Ollama /api/embeddings (single input)
        vectors = [[float(v) for v in data["embedding"]]]
    if len(vectors) != expected or any(not vector for vector in vectors):
        raise ProviderError(
            f"Embedding endpoint returned {len(vectors)} vector(s) for {expected} input(s)."
        )
    return vectors


def build_embedding_client(config: AppConfig) -> EmbeddingClient | None:
    """The embedding client the config asks for, or ``None`` for lexical-only."""

    index = config.index
    if not index.semantic_enabled:
        return None
    api_mode = index.embedding_api_mode or ("ollama" if config.api_mode == "ollama" else "openai")
    base_url = index.embedding_base_url or config.base_url
    return HttpEmbeddingClient(
        api_mode=api_mode,
        base_url=base_url,
        model=index.embedding_model,
        api_key=config.provider_api_key() if not index.embedding_base_url else config.api_key,
        ssl_verification=config.ssl_verification,
        timeout_s=float(config.budgets.max_model_call_s),
    )
