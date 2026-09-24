"""Server-sent events, parsed one line at a time off an HTTP stream."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SseEvent:
    event: str
    data: str
    id: str = ""


class SseParser:
    """Turns ``text/event-stream`` lines into events; a blank line dispatches one."""

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []
        self._id = ""

    @property
    def last_event_id(self) -> str:
        return self._id

    def feed(self, line: str) -> SseEvent | None:
        line = line.rstrip("\r")
        if not line:
            if not self._data and not self._event:
                return None
            dispatched = SseEvent(
                event=self._event or "message", data="\n".join(self._data), id=self._id
            )
            self._event = ""
            self._data = []
            return dispatched
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        elif field == "id" and "\x00" not in value:
            self._id = value
        return None
