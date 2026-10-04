"""The runtime's yes/no questions about a turn, answered by a decision engine."""

from __future__ import annotations

import time
from typing import Any

from code_ai.config.models import AppConfig
from code_ai.decisions.final_answer import OpenJevFinalAnswerJudge
from code_ai.decisions.openjev import (
    OpenJevClient,
    OpenJevError,
    noul,
    noul_probability,
)
from code_ai.decisions.task import OpenJevTaskClassifier

__all__ = [
    "OpenJevClient",
    "OpenJevError",
    "OpenJevFinalAnswerJudge",
    "OpenJevTaskClassifier",
    "build_decision_engine",
    "probe_decision_engine",
]

# A cold server can take far longer over its first question than any turn is
# allowed to wait; the probe is the one caller with patience.
_PROBE_TIMEOUT_S = 120.0


def build_decision_engine(
    config: AppConfig,
) -> tuple[OpenJevTaskClassifier | None, OpenJevFinalAnswerJudge | None]:
    """The classifier and the final-answer judge, sharing one client so a
    server that is down is noticed once for both."""
    decisions = config.decisions
    if not decisions.enabled:
        return None, None
    client = OpenJevClient(
        base_url=decisions.base_url,
        api=decisions.api,
        api_key=decisions.api_key,
        timeout_s=decisions.timeout_s,
        retry_after_s=decisions.retry_after_s,
        ssl_verification=config.ssl_verification,
    )
    classifier = OpenJevTaskClassifier(client, confidence=decisions.confidence)
    if not decisions.max_final_answer_nudges:
        return classifier, None
    judge = OpenJevFinalAnswerJudge(client, nudge_confidence=decisions.nudge_confidence)
    return classifier, judge


async def probe_decision_engine(
    base_url: str,
    *,
    api: str = "auto",
    api_key: str = "",
    ssl_verification: bool = False,
    transport: Any = None,
) -> dict[str, Any]:
    """Ask the engine one real question and time it.

    A health check would pass on a server whose model cannot load; a question
    proves the whole path and leaves the model warm for the first turn.
    """
    client = OpenJevClient(
        base_url=base_url,
        api=api,
        api_key=api_key,
        timeout_s=_PROBE_TIMEOUT_S,
        ssl_verification=ssl_verification,
        transport=transport,
    )
    started = time.monotonic()
    try:
        answers = await client.system_one(
            "create a file named hello.py",
            {"probe": noul("Is the user asking to create or change a file?")},
        )
        probability = noul_probability(answers, "probe")
    except OpenJevError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "probability": probability,
        # Which kind of server answered: the helper, or a bare llama.cpp.
        "api": client.api,
    }
