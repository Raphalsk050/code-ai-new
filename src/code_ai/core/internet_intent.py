from __future__ import annotations

import re
from datetime import datetime

from code_ai.providers.models import Message

CURRENT_MARKERS = {
    "agora",
    "atual",
    "atuais",
    "current",
    "hoje",
    "latest",
    "noticia",
    "noticias",
    "news",
    "recent",
    "tempo real",
    "today",
}

SEARCH_TOPIC_MARKERS = {
    "copa",
    "fifa",
    "joga",
    "jogam",
    "jogo",
    "jogos",
    "partida",
    "partidas",
}

AFFIRMATIONS = {"sim", "s", "isso", "yes", "y", "ok", "claro"}

STOPWORDS = {
    "about",
    "agora",
    "como",
    "com",
    "das",
    "de",
    "del",
    "do",
    "dos",
    "em",
    "for",
    "hoje",
    "jogo",
    "jogos",
    "no",
    "na",
    "the",
    "uma",
    "vai",
    "voce",
    "você",
}


def current_local_date() -> str:
    return datetime.now().astimezone().date().isoformat()


def should_force_web_search_for_turn(text: str, messages: list[Message]) -> bool:
    value = _normalize(text)
    if requires_current_web_search(value):
        return True
    return _is_current_search_followup(value) and any(
        requires_current_web_search(message.content)
        for message in _recent_user_messages(messages, limit=4, skip_latest=True)
    )


def requires_current_web_search(text: str) -> bool:
    value = _normalize(text)
    if not value:
        return False
    if any(marker in value for marker in CURRENT_MARKERS):
        return True
    return bool(
        re.search(
            r"\b(quem|qual|quais|quando|onde)\b.*\b(joga|jogam|jogo|partida|partidas)\b",
            value,
        )
    )


def assistant_promised_search_without_tool(text: str) -> bool:
    value = _normalize(text)
    if not value:
        return False
    search_promises = (
        "busca rapida",
        "busca rápida",
        "buscar agora",
        "buscar essa informacao",
        "buscar essa informação",
        "vou buscar",
        "vou fazer uma busca",
        "vou pesquisar",
    )
    missing_access_claims = (
        "nao tenho acesso",
        "não tenho acesso",
        "nao consigo acessar",
        "não consigo acessar",
        "nao tenho acesso a um calendario",
        "não tenho acesso a um calendário",
        "tempo real",
    )
    return any(marker in value for marker in search_promises + missing_access_claims)


def build_web_search_query(
    text: str,
    messages: list[Message],
    *,
    today: str | None = None,
) -> str:
    today = today or current_local_date()
    parts: list[str] = []
    for message in _recent_user_messages(messages, limit=4):
        content = message.content.strip()
        if not content:
            continue
        if content not in parts and (
            requires_current_web_search(content)
            or _shares_topic(content, text)
            or _is_current_search_followup(content)
        ):
            parts.append(content)
    if text.strip() and text.strip() not in parts:
        parts.append(text.strip())
    if not parts:
        parts.append(text.strip() or "current information")

    query = " ".join(parts)
    normalized = _normalize(query)
    if requires_current_web_search(query) or any(
        marker in normalized for marker in SEARCH_TOPIC_MARKERS
    ):
        query = f"{query} {today} {today[:4]} hoje"
    if "copa do mundo" in normalized or "world cup" in normalized:
        query = f"{query} Copa do Mundo FIFA 2026 jogos de hoje"
    return _compact_query(query)


def enrich_web_search_arguments(
    arguments: dict[str, object],
    messages: list[Message],
    *,
    today: str | None = None,
) -> dict[str, object]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return arguments
    recent_users = _recent_user_messages(messages, limit=4)
    has_current_context = any(
        requires_current_web_search(message.content) for message in recent_users
    )
    should_enrich = (
        requires_current_web_search(query)
        or (
            has_current_context
            and (_is_current_search_followup(query) or _shares_any_topic(query, recent_users))
        )
    )
    if not should_enrich:
        return arguments
    enriched = dict(arguments)
    enriched["query"] = build_web_search_query(query, messages, today=today)
    enriched.setdefault("max_results", 5)
    enriched.setdefault("region", "br-pt")
    return enriched


def _recent_user_messages(
    messages: list[Message],
    *,
    limit: int,
    skip_latest: bool = False,
) -> list[Message]:
    users = [
        message
        for message in messages
        if message.role == "user" and not message.content.startswith("Host-executed web_search")
    ]
    if skip_latest and users:
        users = users[:-1]
    return users[-limit:]


def _is_current_search_followup(text: str) -> bool:
    value = _normalize(text)
    if value in AFFIRMATIONS:
        return True
    tokens = _tokens(value)
    return len(tokens) <= 5 and any(marker in value for marker in SEARCH_TOPIC_MARKERS)


def _shares_any_topic(query: str, messages: list[Message]) -> bool:
    return any(_shares_topic(query, message.content) for message in messages)


def _shares_topic(left: str, right: str) -> bool:
    left_tokens = set(_tokens(_normalize(left)))
    right_tokens = set(_tokens(_normalize(right)))
    return bool(left_tokens and right_tokens and left_tokens.intersection(right_tokens))


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[\wÀ-ÿ]+", text.lower())
        if len(token) >= 3 and token not in STOPWORDS
    ]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _compact_query(query: str) -> str:
    text = re.sub(r"\s+", " ", query).strip()
    if len(text) <= 450:
        return text
    return text[:450].rsplit(" ", 1)[0]
