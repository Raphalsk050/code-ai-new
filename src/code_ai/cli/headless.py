from __future__ import annotations

import sys

from code_ai.app.service import CodeAIApplication
from code_ai.events.models import EventEnvelope
from code_ai.events.sinks import JsonLinesEventSink


async def run_headless(app: CodeAIApplication, task: str, *, events_jsonl: bool = False) -> int:
    if events_jsonl:
        app.subscribe(JsonLinesEventSink(sys.stdout))
    else:

        async def print_event(event: EventEnvelope) -> None:
            if event.event_type == "model.stream.delta":
                sys.stdout.write(str(event.payload.get("text", "")))
                sys.stdout.flush()
            elif event.event_type in {"warning", "error"}:
                print(f"{event.event_type}: {event.payload.get('message', '')}", file=sys.stderr)

        app.subscribe(print_event)

    await app.start()
    result = await app.submit_user_message(task)
    if not events_jsonl and result.text and not result.text.endswith("\n"):
        print()
    await app.close()
    return 130 if result.cancelled else 0
