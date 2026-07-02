from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from code_ai.tools.output import bound_text

# The serialized report is read by the orchestrating model, so every free-text
# field is bounded: the parent wrote the task prompt itself, so a short echo is
# enough to correlate a report with its request.
_TASK_PREVIEW_CHARS = 300
_ERROR_PREVIEW_CHARS = 1000
DEFAULT_SUMMARY_CHARS = 4000


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

    def to_dict(
        self, *, max_summary_chars: int = DEFAULT_SUMMARY_CHARS
    ) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "task": bound_text(self.task, _TASK_PREVIEW_CHARS),
            "status": self.status.value,
            "summary": bound_text(self.summary, max_summary_chars),
            "error": (
                None
                if self.error is None
                else bound_text(self.error, _ERROR_PREVIEW_CHARS)
            ),
            "usage": dict(self.usage),
        }
