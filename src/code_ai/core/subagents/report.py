from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from code_ai.core.subagents.evidence import SubagentEvidenceItem, compact_evidence_items
from code_ai.tools.output import bound_text, fence_untrusted

# The summary is free text a sub-agent wrote, and a sub-agent's own context was
# filled with files, command output, and web pages it did not control. Fencing it
# keeps the parent reading the report as a finding rather than as a turn in its
# own conversation.
_SUMMARY_TAG = "subagent_report"

# The serialized report is read by the orchestrating model, so every free-text
# field is bounded: the parent wrote the task prompt itself, so a short echo is
# enough to correlate a report with its request.
_TASK_PREVIEW_CHARS = 300
_ERROR_PREVIEW_CHARS = 1000
DEFAULT_SUMMARY_CHARS = 4000
# Evidence items are compact (paths, hashes, argv), but a long-running coder can
# accumulate many; the digest is bounded so a report never dwarfs its summary.
_MAX_EVIDENCE_ITEMS = 80


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
    # Human-friendly genius-style name (e.g. "Turing") assigned at creation and
    # used in every log/reference to this agent. Defaults to empty for the few
    # call sites that predate a name (e.g. a bare rejection).
    name: str = ""
    summary: str = ""
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    # Ordered digest of the workspace actions the sub-agent actually performed
    # (files read/created/changed, commands run). Filled for every terminal
    # status - a timed-out coder may still have changed real files on disk, and
    # the parent must learn about those changes either way.
    evidence: list[SubagentEvidenceItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is SubagentStatus.COMPLETED

    def to_dict(
        self, *, max_summary_chars: int = DEFAULT_SUMMARY_CHARS
    ) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "name": self.name,
            "task": bound_text(self.task, _TASK_PREVIEW_CHARS),
            "status": self.status.value,
            # Bound first, then fence: truncating afterwards could cut the
            # closing delimiter off and leave the payload open-ended.
            "summary": fence_untrusted(
                bound_text(self.summary, max_summary_chars), tag=_SUMMARY_TAG
            ),
            "error": (
                None
                if self.error is None
                else bound_text(self.error, _ERROR_PREVIEW_CHARS)
            ),
            "usage": dict(self.usage),
            "evidence": [
                item.to_dict()
                for item in compact_evidence_items(
                    self.evidence, max_items=_MAX_EVIDENCE_ITEMS
                )
            ],
        }
