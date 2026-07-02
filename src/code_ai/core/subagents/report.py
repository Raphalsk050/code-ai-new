from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SubagentStatus(StrEnum):
    """Terminal outcome of a single sub-agent run.

    ``COMPLETED`` means the sub-agent produced a final answer; it does not assert
    the subtask succeeded on its own terms (the summary text carries that). The
    other states are runtime failures the coordinator maps deterministically.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    REJECTED = "rejected"  # refused before running (circuit open, limits hit)


@dataclass(slots=True)
class SubagentReport:
    """Immutable-by-convention result a sub-agent hands back to its dispatcher.

    This is the entire communication channel from child to parent: no shared
    conversation, no mutable state - just this value. The dispatcher serializes
    it into the tool result the orchestrating model reads.
    """

    agent_id: str
    agent_type: str
    task: str
    status: SubagentStatus
    summary: str = ""
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is SubagentStatus.COMPLETED

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "task": self.task,
            "status": self.status.value,
            "summary": self.summary,
            "error": self.error,
            "usage": dict(self.usage),
        }
