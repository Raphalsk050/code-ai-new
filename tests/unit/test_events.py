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
