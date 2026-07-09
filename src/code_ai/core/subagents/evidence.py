from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from code_ai.events.models import EventEnvelope

# Item kinds the collector can observe. They mirror the parent evidence ledger's
# vocabulary so a digest converts 1:1 into ledger records on the parent side.
KIND_FILE_READ = "file_read"
KIND_FILE_CREATED = "file_created"
KIND_FILE_CHANGED = "file_changed"
KIND_COMMAND = "command"

# When a digest must be truncated, plain reads are dropped before mutations and
# commands: the parent needs the workspace-state items to gate completion, while
# reads only enrich its knowledge of covered paths.
_LOW_PRIORITY_KINDS = frozenset({KIND_FILE_READ})


@dataclass(frozen=True, slots=True)
class SubagentEvidenceItem:
    """One workspace-observable action a sub-agent performed.

    Sub-agents run without a planner, so the parent cannot inherit a ledger from
    them; these items are the wire format through which the child's real actions
    (files read/written, commands run) travel back inside its report.
    """

    kind: str
    path: str = ""
    old_sha256: str | None = None
    new_sha256: str = ""
    argv: list[str] | str | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind}
        if self.path:
            data["path"] = self.path
        if self.old_sha256 is not None:
            data["old_sha256"] = self.old_sha256
        if self.new_sha256:
            data["new_sha256"] = self.new_sha256
        if self.argv is not None:
            data["argv"] = self.argv
        if self.exit_code is not None:
            data["exit_code"] = self.exit_code
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubagentEvidenceItem | None:
        kind = str(data.get("kind") or "")
        if kind not in {KIND_FILE_READ, KIND_FILE_CREATED, KIND_FILE_CHANGED, KIND_COMMAND}:
            return None
        exit_code = data.get("exit_code")
        return cls(
            kind=kind,
            path=str(data.get("path") or ""),
            old_sha256=data.get("old_sha256"),
            new_sha256=str(data.get("new_sha256") or ""),
            argv=data.get("argv"),
            exit_code=exit_code if isinstance(exit_code, int) else None,
        )


def compact_evidence_items(
    items: list[SubagentEvidenceItem], *, max_items: int
) -> list[SubagentEvidenceItem]:
    """Bound a digest for serialization while keeping the items that matter.

    Mutations and commands are always kept (they drive the parent's completion
    gate); when the total still exceeds the budget, the most recent reads win
    because they are likelier to concern the files the sub-agent acted on.
    """
    if len(items) <= max_items:
        return list(items)
    essential = [item for item in items if item.kind not in _LOW_PRIORITY_KINDS]
    budget = max(0, max_items - len(essential))
    reads = [item for item in items if item.kind in _LOW_PRIORITY_KINDS]
    kept_reads = set(map(id, reads[-budget:])) if budget else set()
    return [
        item
        for item in items
        if item.kind not in _LOW_PRIORITY_KINDS or id(item) in kept_reads
    ]


class SubagentEvidenceCollector:
    """Observes a sub-agent's event bus and accumulates its evidence digest.

    Subscribed to the child bus for the duration of one run; every successful
    tool completion that changed or observed workspace state becomes an ordered
    item. Order is preserved so the parent ledger can replay it chronologically
    (a verification that ran *after* the last change must still count).
    """

    def __init__(self) -> None:
        self._items: list[SubagentEvidenceItem] = []

    def items(self) -> list[SubagentEvidenceItem]:
        return list(self._items)

    def __call__(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != "tool.call.completed":
            return
        name = str(envelope.payload.get("name") or "")
        result = envelope.payload.get("result")
        if not isinstance(result, dict):
            return
        item = _item_from_tool_result(name, result)
        if item is not None:
            self._items.append(item)


def _item_from_tool_result(
    name: str, result: dict[str, Any]
) -> SubagentEvidenceItem | None:
    if name == "read_file":
        path = str(result.get("path") or "")
        return SubagentEvidenceItem(kind=KIND_FILE_READ, path=path) if path else None
    if name == "write_file":
        path = str(result.get("path") or "")
        if not path:
            return None
        old_hash = result.get("old_sha256")
        return SubagentEvidenceItem(
            kind=KIND_FILE_CREATED if old_hash in {None, ""} else KIND_FILE_CHANGED,
            path=path,
            old_sha256=old_hash,
            new_sha256=str(result.get("new_sha256") or ""),
        )
    if name == "edit_code":
        path = str(result.get("path") or "")
        if not path or not result.get("changed"):
            return None
        return SubagentEvidenceItem(
            kind=KIND_FILE_CHANGED,
            path=path,
            old_sha256=result.get("old_sha256"),
            new_sha256=str(result.get("new_sha256") or ""),
        )
    if name == "execute_command":
        exit_code = result.get("exit_code")
        return SubagentEvidenceItem(
            kind=KIND_COMMAND,
            argv=result.get("argv"),
            exit_code=exit_code if isinstance(exit_code, int) else None,
        )
    return None
