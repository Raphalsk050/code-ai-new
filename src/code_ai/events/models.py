from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from code_ai.util.redaction import redact_mapping


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    event_version: int
    session_id: str
    sequence: int
    timestamp: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        session_id: str,
        sequence: int,
        payload: dict[str, Any] | None = None,
        source: str = "core",
        event_version: int = 1,
    ) -> EventEnvelope:
        return cls(
            event_id=str(uuid4()),
            event_type=event_type,
            event_version=event_version,
            session_id=session_id,
            sequence=sequence,
            timestamp=utc_now_iso(),
            source=source,
            payload=redact_mapping(payload or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)
