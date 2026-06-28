from __future__ import annotations

import re
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
    """Heuristic hint that a request may need current external information.

    Single-word markers match on whole words only, so a marker like ``"atual"``
    does not fire inside an unrelated word such as ``"atualize"`` (update).
    Multi-word markers (e.g. ``"tempo real"``) still match as a phrase.
    """
    value = _normalize(text)
    if not value:
        return False
    tokens = set(re.findall(r"\w+", value, flags=re.UNICODE))
    return any(
        (marker in value) if " " in marker else (marker in tokens)
        for marker in CURRENT_MARKERS
    )


# Backwards-compatible alias kept for the web_search relevance boost and task
# classification. It is intentionally generic now (no domain-specific markers).
def requires_current_web_search(text: str) -> bool:
    return looks_time_sensitive(text)


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())
