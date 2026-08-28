from __future__ import annotations

import asyncio
import io

from code_ai.events.bus import AsyncEventBus
from code_ai.events.sinks import JsonLinesEventSink


async def test_event_order_and_subscriber_isolation() -> None:
    bus = AsyncEventBus(session_id="session")
    seen: list[tuple[int, str]] = []

    async def failing(event):
        if event.event_type == "first":
            raise RuntimeError("boom")

    async def collecting(event):
        seen.append((event.sequence, event.event_type))

    bus.subscribe(failing)
    bus.subscribe(collecting)
    await bus.emit("first", {"api_key": "secret"})
    await bus.emit("second", {})

    assert seen[0] == (1, "first")
    assert (2, "warning") in seen
    assert seen[-1] == (3, "second")


async def test_concurrent_emits_are_delivered_in_sequence_order() -> None:
    # Tools running in a parallel batch emit concurrently; the subscriber must
    # still observe events strictly in order, never with the final answer (or
    # any line) ahead of an earlier one.
    bus = AsyncEventBus(session_id="session")
    received: list[int] = []

    async def collecting(event):
        # Yield control so out-of-order delivery would surface if the bus did
        # not serialize publication.
        await asyncio.sleep(0)
        received.append(event.sequence)

    bus.subscribe(collecting)

    await asyncio.gather(*(bus.emit("event", {"index": index}) for index in range(50)))

    assert received == list(range(1, 51))


async def test_json_lines_sink_serializes_redacted_payload() -> None:
    bus = AsyncEventBus(session_id="session")
    stream = io.StringIO()
    bus.subscribe(JsonLinesEventSink(stream))
    event = await bus.emit("config", {"token": "secret"})
    assert event.payload["token"] == "<redacted>"
    rendered = stream.getvalue()
    assert '"event_type": "config"' in rendered
    assert "secret" not in rendered


async def test_a_subscriber_that_emits_does_not_deadlock_the_bus() -> None:
    # The lock is not reentrant, so a subscriber emitting while handling an
    # event used to wait forever on a lock its own task already held: the emit
    # never returned and every later emit queued behind it. Nothing in the
    # process spoke again - no error, no way back. The event is queued and
    # delivered after the current one instead.
    bus = AsyncEventBus(session_id="session")
    seen: list[str] = []

    async def echoing(event):
        seen.append(event.event_type)
        if event.event_type == "first":
            await bus.emit("second", {}, source="test")

    bus.subscribe(echoing)

    emit = asyncio.create_task(bus.emit("first", {}, source="test"))
    done, _pending = await asyncio.wait({emit}, timeout=5)
    assert emit in done, "the bus deadlocked on a re-entrant emit"

    # Delivered after the event that produced it, and the sequence is still
    # assigned in emission order.
    assert seen == ["first", "second"]
    assert (await emit).sequence == 1


async def test_a_nested_emit_is_delivered_to_every_subscriber() -> None:
    bus = AsyncEventBus(session_id="session")
    other: list[str] = []

    async def emitter(event):
        if event.event_type == "trigger":
            await bus.emit("derived", {}, source="test")

    bus.subscribe(emitter)
    bus.subscribe(lambda event: other.append(event.event_type))

    await bus.emit("trigger", {}, source="test")
    assert other == ["trigger", "derived"]


async def test_a_subscriber_answering_its_own_events_is_bounded() -> None:
    # A subscriber that emits in response to what it just emitted would spin
    # inside one delivery forever. It is cut off instead of hanging the process.
    bus = AsyncEventBus(session_id="session")
    seen: list[str] = []

    async def loop_forever(event):
        seen.append(event.event_type)
        await bus.emit("again", {}, source="test")

    bus.subscribe(loop_forever)

    emit = asyncio.create_task(bus.emit("start", {}, source="test"))
    done, _pending = await asyncio.wait({emit}, timeout=5)
    assert emit in done, "a self-feeding subscriber hung the bus"
    assert len(seen) <= AsyncEventBus.MAX_NESTED_EVENTS + 1
