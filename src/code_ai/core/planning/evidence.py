from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from code_ai.core.planning.models import EvidenceType, ExecutionPlan
from code_ai.events.models import utc_now_iso
from code_ai.tools.output import bound_text


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    plan_id: str | None = None
    plan_revision: int | None = None
    step_id: str | None = None
    tool_call_id: str
    tool_name: str
    evidence_type: EvidenceType
    timestamp: str = Field(default_factory=utc_now_iso)
    success: bool
    summary: str
    affected_paths: list[str] = Field(default_factory=list)
    old_hashes: dict[str, str | None] = Field(default_factory=dict)
    new_hashes: dict[str, str] = Field(default_factory=dict)
    command_argv: list[str] | str | None = None
    command_exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    review_severity_summary: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def compact(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "type": self.evidence_type.value,
            "tool": self.tool_name,
            "success": self.success,
            "summary": self.summary,
            "affected_paths": self.affected_paths,
            "command_exit_code": self.command_exit_code,
        }


class EvidenceLedger:
    """Records evidence derived from executed tool results for one session."""

    def __init__(self, *, session_id: str, max_summary_chars: int = 800) -> None:
        self.session_id = session_id
        self.max_summary_chars = max_summary_chars
        self.records: list[EvidenceRecord] = []
        self.changed_hashes: dict[str, str] = {}
        self.latest_verification_passed = False
        self.latest_verification_evidence_id: str | None = None
        self.verification_hashes: dict[str, str] = {}
        # Distinct knowledge accumulated this session. Each set grows only when the
        # agent observes something genuinely new, so repeating an identical
        # observation does not register as progress. See ``progress_fingerprint``.
        self._read_keys: set[str] = set()
        self._listed_keys: set[str] = set()
        self._search_keys: set[str] = set()
        self._web_keys: set[str] = set()

    def record_tool_result(
        self,
        *,
        plan: ExecutionPlan | None,
        step_id: str | None,
        tool_call_id: str,
        tool_name: str,
        payload: dict[str, Any],
        success: bool,
    ) -> list[EvidenceRecord]:
        records = _records_from_payload(
            session_id=self.session_id,
            plan=plan,
            step_id=step_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload=payload,
            success=success,
            max_summary_chars=self.max_summary_chars,
        )
        for record in records:
            self._append(record)
        return records

    def record_policy_denial(
        self,
        *,
        plan: ExecutionPlan | None,
        step_id: str | None,
        tool_call_id: str,
        tool_name: str,
        reason: str,
    ) -> EvidenceRecord:
        record = EvidenceRecord(
            session_id=self.session_id,
            plan_id=plan.plan_id if plan else None,
            plan_revision=plan.revision if plan else None,
            step_id=step_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            evidence_type=EvidenceType.COMPLETION_REQUESTED,
            success=False,
            summary=bound_text(f"Policy denied {tool_name}: {reason}", self.max_summary_chars),
        )
        self._append(record)
        return record

    def has_success(self, *types: EvidenceType) -> bool:
        wanted = set(types)
        return any(record.success and record.evidence_type in wanted for record in self.records)

    def success_records(self, *types: EvidenceType) -> list[EvidenceRecord]:
        wanted = set(types)
        return [
            record
            for record in self.records
            if record.success and record.evidence_type in wanted
        ]

    def current_changed_paths(self) -> list[str]:
        return sorted(self.changed_hashes)

    def compact_recent(self, *, limit: int = 12) -> list[dict[str, Any]]:
        return [record.compact() for record in self.records[-limit:]]

    def progress_fingerprint(self) -> tuple[object, ...]:
        """Snapshot of distinct knowledge and workspace state gathered so far.

        The orchestrator compares this across model steps to tell genuine forward
        motion (a new file read, a new listing, a fresh search/web result, a file
        change, a verification outcome) apart from a non-advancing tool-call loop.
        It is intentionally stable when the same observation is repeated.
        """
        return (
            len(self._read_keys),
            len(self._listed_keys),
            len(self._search_keys),
            len(self._web_keys),
            tuple(sorted(self.changed_hashes.items())),
            self.latest_verification_passed,
            tuple(sorted(self.verification_hashes.items())),
        )

    def _append(self, record: EvidenceRecord) -> None:
        self.records.append(record)
        if record.success and record.evidence_type in {
            EvidenceType.FILE_CREATED,
            EvidenceType.FILE_CHANGED,
        }:
            self.changed_hashes.update(record.new_hashes)
            self.latest_verification_passed = False
            self.latest_verification_evidence_id = None
            self.verification_hashes = {}
        elif record.evidence_type == EvidenceType.VERIFICATION_PASSED and record.success:
            self.latest_verification_passed = True
            self.latest_verification_evidence_id = record.evidence_id
            self.verification_hashes = dict(self.changed_hashes)
        elif record.evidence_type == EvidenceType.VERIFICATION_FAILED:
            self.latest_verification_passed = False
            self.latest_verification_evidence_id = None
        if record.success:
            self._record_knowledge(record)

    def _record_knowledge(self, record: EvidenceRecord) -> None:
        if record.evidence_type == EvidenceType.FILE_READ:
            self._read_keys.update(record.affected_paths)
        elif record.evidence_type == EvidenceType.WORKSPACE_LISTED:
            self._listed_keys.add(record.summary)
        elif record.evidence_type in {
            EvidenceType.LOCAL_SEARCH_MATCH,
            EvidenceType.LOCAL_SEARCH_COMPLETED,
        }:
            self._search_keys.add(record.summary)
        elif record.evidence_type == EvidenceType.WEB_RESULT:
            self._web_keys.add(record.summary)


def _records_from_payload(
    *,
    session_id: str,
    plan: ExecutionPlan | None,
    step_id: str | None,
    tool_call_id: str,
    tool_name: str,
    payload: dict[str, Any],
    success: bool,
    max_summary_chars: int,
) -> list[EvidenceRecord]:
    common = {
        "session_id": session_id,
        "plan_id": plan.plan_id if plan else None,
        "plan_revision": plan.revision if plan else None,
        "step_id": step_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "success": success,
    }
    summary = _summary(payload, max_summary_chars)
    if tool_name == "list_files":
        entry_count = len(payload.get("entries", []))
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.WORKSPACE_LISTED,
                summary=f"Listed {payload.get('path', '.')} ({entry_count} entries).",
                truncated=bool(payload.get("truncated")),
                metadata={
                    "entry_count": entry_count,
                    "skipped_count": payload.get("skipped_count", 0),
                },
            )
        ]
    if tool_name == "read_file":
        path = str(payload.get("path") or "")
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.FILE_READ,
                summary=f"Read {path}.",
                affected_paths=[path] if path else [],
                new_hashes={path: str(payload.get("sha256"))} if path else {},
                truncated=bool(payload.get("truncated")),
            )
        ]
    if tool_name == "search_code":
        matches = payload.get("matches")
        match_count = len(matches) if isinstance(matches, list) else 0
        evidence_type = (
            EvidenceType.LOCAL_SEARCH_MATCH if match_count else EvidenceType.LOCAL_SEARCH_COMPLETED
        )
        paths = sorted(
            {
                str(item.get("path"))
                for item in matches or []
                if isinstance(item, dict) and item.get("path")
            }
        )
        return [
            EvidenceRecord(
                **common,
                evidence_type=evidence_type,
                summary=f"Local search completed with {match_count} match(es).",
                affected_paths=paths,
                truncated=bool(payload.get("truncated")),
                metadata={"engine": payload.get("engine"), "match_count": match_count},
            )
        ]
    if tool_name == "write_file":
        path = str(payload.get("path") or "")
        old_hash = payload.get("old_sha256")
        new_hash = str(payload.get("new_sha256") or "")
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.FILE_CREATED
                if old_hash in {None, ""}
                else EvidenceType.FILE_CHANGED,
                summary=f"Wrote {path}.",
                affected_paths=[path] if path else [],
                old_hashes={path: old_hash} if path else {},
                new_hashes={path: new_hash} if path and new_hash else {},
            )
        ]
    if tool_name == "edit_code":
        path = str(payload.get("path") or "")
        changed = bool(payload.get("changed"))
        new_hash = str(payload.get("new_sha256") or "")
        edit_common = dict(common)
        edit_common["success"] = success and changed
        return [
            EvidenceRecord(
                **edit_common,
                evidence_type=EvidenceType.FILE_CHANGED,
                summary=f"Edited {path}." if changed else f"Edit produced no change in {path}.",
                affected_paths=[path] if path else [],
                old_hashes={path: payload.get("old_sha256")} if path else {},
                new_hashes={path: new_hash} if path and new_hash else {},
                truncated=bool(
                    payload.get("diff", "")
                    and len(str(payload.get("diff"))) > max_summary_chars
                ),
            )
        ]
    if tool_name == "execute_command":
        exit_code = payload.get("exit_code")
        command_success = success and exit_code == 0
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.VERIFICATION_PASSED
                if command_success
                else EvidenceType.VERIFICATION_FAILED,
                summary=_command_summary(payload, max_summary_chars),
                command_argv=payload.get("argv"),
                command_exit_code=exit_code if isinstance(exit_code, int) else None,
                timed_out=bool(payload.get("timed_out")),
                cancelled=bool(payload.get("cancelled")),
                metadata={"cwd": payload.get("cwd")},
            )
        ]
    if tool_name in {"architecture_review", "code_review", "build_review"}:
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.REVIEW_COMPLETED,
                summary=summary,
                review_severity_summary=_review_summary(payload),
            )
        ]
    if tool_name == "web_search":
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.WEB_RESULT,
                summary=f"Web search returned {len(payload.get('results', []))} result(s).",
                metadata={"query": payload.get("query")},
            )
        ]
    if tool_name in {
        "control_terminal",
        "start_terminal",
        "send_terminal_text",
        "terminal_enter",
        "interrupt_terminal",
        "terminate_terminal",
        "read_screen",
    }:
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.TERMINAL_OBSERVED,
                summary=summary,
                metadata={"session_id": payload.get("session_id")},
            )
        ]
    if tool_name in {"finish_discovery", "request_external_gap"}:
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.DISCOVERY_COMPLETED,
                summary=str(payload.get("summary") or summary),
                affected_paths=[str(path) for path in payload.get("relevant_paths", [])],
                metadata={
                    "external_knowledge_gaps": payload.get("external_knowledge_gaps", [])
                },
            )
        ]
    if tool_name == "complete_task":
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.COMPLETION_REQUESTED,
                summary=str(payload.get("summary") or summary),
                affected_paths=[str(path) for path in payload.get("changed_paths", [])],
            )
        ]
    if tool_name == "ask_user":
        return [
            EvidenceRecord(
                **common,
                evidence_type=EvidenceType.USER_ANSWER,
                summary=summary,
            )
        ]
    return []


def _summary(payload: dict[str, Any], max_chars: int) -> str:
    return bound_text(json.dumps(payload, sort_keys=True, default=str), max_chars)


def _command_summary(payload: dict[str, Any], max_chars: int) -> str:
    argv = payload.get("argv")
    exit_code = payload.get("exit_code")
    stdout = str(payload.get("stdout") or "").strip()
    stderr = str(payload.get("stderr") or "").strip()
    details = stderr or stdout
    return bound_text(f"Command {argv!r} exited with {exit_code}. {details}", max_chars)


def _review_summary(payload: dict[str, Any]) -> dict[str, int]:
    raw_findings = payload.get("findings")
    summary: dict[str, int] = {}
    if not isinstance(raw_findings, list):
        return summary
    for finding in raw_findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "unknown").lower()
        summary[severity] = summary.get(severity, 0) + 1
    return summary
