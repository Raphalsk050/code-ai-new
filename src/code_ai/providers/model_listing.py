from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit

from code_ai.config.models import AppConfig
from code_ai.core.errors import ProviderError
from code_ai.providers.ollama import normalize_native_ollama_base_url

# A catalog is one quick GET: a server that cannot answer it in this long is
# not going to, and the settings screen is waiting on it.
_LIST_TIMEOUT_S = 60.0
# Anthropic's own catalog wants its API version and the key as x-api-key: with a
# bare Bearer header it answers 400, though its chat completions take Bearer.
ANTHROPIC_VERSION = "2023-06-01"


def models_endpoint(config: AppConfig) -> str:
    """Resolve the URL that lists the models the configured provider serves.

    Each api_mode advertises its catalog where its chat endpoint lives: native
    Ollama exposes ``/api/tags`` while OpenAI-compatible servers (responses and
    completions) expose ``/v1/models`` relative to ``base_url``.
    """
    if config.api_mode == "ollama":
        return urljoin(normalize_native_ollama_base_url(config.base_url), "api/tags")
    return urljoin(config.base_url.rstrip("/") + "/", "models")


def _is_anthropic(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "anthropic.com" or host.endswith(".anthropic.com")


def listing_request(config: AppConfig) -> tuple[str, dict[str, str], dict[str, int]]:
    """Where the configured provider's catalog is, and the headers and query it wants."""
    url = models_endpoint(config)
    if config.api_mode == "ollama":
        return url, {}, {}
    key = config.provider_api_key()
    if _is_anthropic(url):
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if key:
            headers["x-api-key"] = key
        # Twenty to a page unless asked: the whole catalog fits in one.
        return url, headers, {"limit": 1000}
    return url, ({"Authorization": f"Bearer {key}"} if key else {}), {}


def extract_model_ids(data: Any) -> list[str]:
    """Pull model identifiers out of an OpenAI ``/models`` or Ollama ``/api/tags``
    payload, tolerating both shapes and deduplicating case-insensitively."""
    if isinstance(data, dict):
        entries = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        entries = data
    else:
        entries = []

    names: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = entry.get("id") or entry.get("name") or entry.get("model")
        else:
            name = None
        if name:
            names.add(str(name))
    return sorted(names, key=str.lower)


async def list_available_models(config: AppConfig) -> list[str]:
    """Ask the configured provider which models it currently serves.

    Returns the model identifiers sorted case-insensitively. Raises
    ``ProviderError`` when the endpoint is unreachable or rejects the request so
    callers can surface a single, actionable message.
    """
    try:
        import httpx
    except Exception as exc:  # pragma: no cover - httpx ships with the deps
        raise ProviderError("The httpx package is required to list models.") from exc

    url, headers, params = listing_request(config)
    try:
        async with httpx.AsyncClient(
            timeout=_LIST_TIMEOUT_S,
            verify=config.ssl_verification,
        ) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        raise ProviderError(f"Could not list models from {url}: {exc}") from exc

    models = extract_model_ids(data)
    if not models:
        raise ProviderError(f"The provider at {url} returned no models.")
    return models
