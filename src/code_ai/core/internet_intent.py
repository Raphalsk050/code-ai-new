from __future__ import annotations

from datetime import datetime

# Generic, locale-neutral signals that a question may depend on current or
# time-sensitive information. This is only a hint: the decision to call
# web_search belongs to the model and the normal tool flow, not to hardcoded
# host-side heuristics.
CURRENT_MARKERS = {
    "agora",
    "atual",
    "atuais",
    "current",
    "hoje",
    "latest",
    "news",
    "noticia",
    "noticias",
    "recent",
    "recente",
    "today",
    "tempo real",
}


def current_local_date() -> str:
    return datetime.now().astimezone().date().isoformat()


def looks_time_sensitive(text: str) -> bool:
    """Heuristic hint that a request may need current external information."""
    value = _normalize(text)
    if not value:
        return False
    return any(marker in value for marker in CURRENT_MARKERS)


# Backwards-compatible alias kept for the web_search relevance boost and task
# classification. It is intentionally generic now (no domain-specific markers).
def requires_current_web_search(text: str) -> bool:
    return looks_time_sensitive(text)


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())
