from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from code_ai.config.models import AppConfig
from code_ai.core.errors import ProviderError
from code_ai.providers.ollama import normalize_native_ollama_base_url


def models_endpoint(config: AppConfig) -> str:
    """Resolve the URL that lists the models the configured provider serves.

    Each api_mode advertises its catalog where its chat endpoint lives: native
    Ollama exposes ``/api/tags`` while OpenAI-compatible servers (responses and
    completions) expose ``/v1/models`` relative to ``base_url``.
    """
    if config.api_mode == "ollama":
        return urljoin(normalize_native_ollama_base_url(config.base_url), "api/tags")
    return urljoin(config.base_url.rstrip("/") + "/", "models")


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

    url = models_endpoint(config)
    headers: dict[str, str] = {}
    if config.api_mode != "ollama":
        key = config.provider_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"

    try:
        async with httpx.AsyncClient(
            timeout=config.budgets.model_timeout(),
            verify=config.ssl_verification,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        raise ProviderError(f"Could not list models from {url}: {exc}") from exc

    models = extract_model_ids(data)
    if not models:
        raise ProviderError(f"The provider at {url} returned no models.")
    return models
