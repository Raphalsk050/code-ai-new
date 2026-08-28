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

    # How many events emitted from inside a delivery are delivered before the
    # bus stops draining. It bounds a subscriber that answers its own events,
    # which would otherwise refill the queue as fast as it empties.
    MAX_NESTED_EVENTS = 100

    def __init__(self, *, session_id: str | None = None) -> None:
        self.session_id = session_id or str(uuid4())
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._subscribers: list[EventSubscriber] = []
        # The task currently delivering, so a re-entrant emit is recognised as
        # one rather than deadlocking on the lock it already holds.
        self._delivering: asyncio.Task[Any] | None = None
        self._nested: list[EventEnvelope] = []

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
        # out of order, scrambling the conversation transcript.
        #
        # The lock is not reentrant, so a subscriber emitting while it handles
        # an event would wait on a lock its own task is holding: the emit never
        # returns, and every future emit queues behind it. Nothing in the
        # process ever speaks again, with no error and no way back - the worst
        # failure this runtime has. Rather than rely on no subscriber ever doing
        # it, such an event is queued and delivered as soon as the current
        # delivery finishes: same order, no deadlock.
        if self._delivering is not None and self._delivering is asyncio.current_task():
            envelope = self._make_envelope(
                event_type=event_type,
                payload=payload,
                source=source,
                event_version=event_version,
            )
            self._nested.append(envelope)
            return envelope

        async with self._lock:
            envelope = self._make_envelope(
                event_type=event_type,
                payload=payload,
                source=source,
                event_version=event_version,
            )
            self._delivering = asyncio.current_task()
            try:
                await self._deliver(envelope)
                # Whatever the subscribers emitted while handling it, in order.
                # Bounded: a subscriber that answers its own events would
                # otherwise refill the queue as fast as it is drained and spin
                # inside this one emit forever - a freeze by another route.
                delivered = 0
                while self._nested and delivered < self.MAX_NESTED_EVENTS:
                    await self._deliver(self._nested.pop(0))
                    delivered += 1
                if self._nested:
                    logger.warning(
                        "Dropping %d event(s) emitted from inside the delivery of "
                        "%s: a subscriber is answering its own events.",
                        len(self._nested),
                        envelope.event_type,
                    )
            finally:
                self._delivering = None
                self._nested.clear()
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
