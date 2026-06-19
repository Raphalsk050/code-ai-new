from __future__ import annotations

import inspect
from typing import TextIO

from code_ai.events.models import EventEnvelope


class JsonLinesEventSink:
    """Writes event envelopes as JSON Lines for headless integrations."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    async def __call__(self, event: EventEnvelope) -> None:
        self._stream.write(event.to_json() + "\n")
        flush = self._stream.flush
        result = flush()
        if inspect.isawaitable(result):
            await result
