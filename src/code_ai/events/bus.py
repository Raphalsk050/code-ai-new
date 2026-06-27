from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from code_ai.events.models import EventEnvelope

EventSubscriber = Callable[[EventEnvelope], Awaitable[None] | None]

logger = logging.getLogger(__name__)


class AsyncEventBus:
    """In-process async event bus with ordered envelopes and isolated subscribers."""

    def __init__(self, *, session_id: str | None = None) -> None:
        self.session_id = session_id or str(uuid4())
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, subscriber: EventSubscriber) -> EventSubscriber:
        self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        self._subscribers = [item for item in self._subscribers if item is not subscriber]

    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "core",
        event_version: int = 1,
    ) -> EventEnvelope:
        # Sequence assignment and delivery happen under one lock so subscribers
        # always observe events in emission order. Without this, concurrent
        # emitters (e.g. tools running in a parallel batch) could be delivered
        # out of order, scrambling the conversation transcript. Subscribers must
        # therefore not emit back onto this bus while handling an event, or they
        # would deadlock; today none do.
        async with self._lock:
            envelope = self._make_envelope(
                event_type=event_type,
                payload=payload,
                source=source,
                event_version=event_version,
            )
            await self._deliver(envelope)
            return envelope

    def _make_envelope(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None,
        source: str,
        event_version: int,
    ) -> EventEnvelope:
        # Caller must hold ``self._lock``.
        self._sequence += 1
        return EventEnvelope.create(
            event_type=event_type,
            session_id=self.session_id,
            sequence=self._sequence,
            payload=payload,
            source=source,
            event_version=event_version,
        )

    async def _deliver(self, envelope: EventEnvelope) -> None:
        # Caller must hold ``self._lock``.
        failures: list[str] = []
        for subscriber in list(self._subscribers):
            try:
                result = subscriber(envelope)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # pragma: no cover - defensive logging branch
                logger.exception("Event subscriber failed for %s", envelope.event_type)
                failures.append(type(exc).__name__)

        if failures and envelope.event_type != "warning":
            detail = ", ".join(sorted(set(failures)))
            warning = self._make_envelope(
                event_type="warning",
                payload={
                    "message": (
                        f"Event subscriber failed while handling "
                        f"{envelope.event_type} ({detail})."
                    ),
                    "failures": failures,
                    "event_type": envelope.event_type,
                },
                source="events",
                event_version=1,
            )
            for subscriber in list(self._subscribers):
                try:
                    result = subscriber(warning)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("Event subscriber failed while reporting a warning.")
