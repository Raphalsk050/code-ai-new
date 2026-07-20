from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from code_ai.core.planning.models import EvidenceType, ExecutionPlan
from code_ai.core.verification import CommandKind, strongest_kind
from code_ai.events.models import utc_now_iso
from code_ai.tools.output import bound_text

# What a command's verification classifier looks like: argv in (list | str |
# None), returning the kind of check it performs or ``None`` for a command that
# verifies nothing. ``None`` as the callable itself means "treat every command
# as a test run" (the permissive legacy default used by direct ledger tests).
VerificationClassifier = Callable[[list[str] | str | None], CommandKind | None]

# The review tools whose results become REVIEW_COMPLETED evidence.
_REVIEW_TOOLS = frozenset(
    {"architecture_review", "code_review", "build_review", "test_review"}
)

# Finding severities serious enough that completing without addressing (or at
# least honestly disclosing) them would hand the user known-broken work.
_SEVERE_FINDING_SEVERITIES = frozenset(
    {"critical", "high", "blocker", "severe", "major", "error"}
)


def count_severe_findings(severity_summary: dict[str, int]) -> int:
    """How many findings in a review's severity summary are serious."""
    return sum(
        count
        for severity, count in severity_summary.items()
        if severity.lower() in _SEVERE_FINDING_SEVERITIES and count > 0
    )


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
        # Kinds of verification (test/build/typecheck/lint) that passed against
        # the *current* change set. Reset whenever a file changes, so the
        # completion gate can demand the project's strongest check and a stale
        # or weaker pass (e.g. lint-only) cannot stand in for it.
        self._verification_kinds_passed: set[CommandKind] = set()
        # Review state for the *current* change set, reset whenever a file
        # changes (a fix may have addressed the findings, and a review of the
        # old state says nothing about the new one). Severe findings that are
        # still open must be fixed or honestly disclosed before completion.
        self.review_ran_after_last_change = False
        self.open_severe_review_findings = 0
        # Distinct knowledge accumulated this session. Each set grows only when the
        # agent observes something genuinely new, so repeating an identical
        # observation does not register as progress. See ``progress_fingerprint``.
        self._read_keys: set[str] = set()
        self._listed_keys: set[str] = set()
        self._search_keys: set[str] = set()
        self._web_keys: set[str] = set()
        self._command_keys: set[str] = set()
        self._review_keys: set[str] = set()

    def record_tool_result(
        self,
        *,
        plan: ExecutionPlan | None,
        step_id: str | None,
        tool_call_id: str,
        tool_name: str,
        payload: dict[str, Any],
        success: bool,
        classify_verification: VerificationClassifier | None = None,
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
            classify_verification=classify_verification,
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

    def has_record(self, *types: EvidenceType) -> bool:
        """Any record of these types, successful or not."""
        wanted = set(types)
        return any(record.evidence_type in wanted for record in self.records)

    def mutation_was_attempted(self) -> bool:
        """Whether any write-shaped action was tried this turn, even a failed one.

        Its absence, on a mutation-labelled task, is the reclassification signal
        the completion gate uses: the model never treated the task as a
        mutation, whatever the surface classifier said.
        """
        return any(
            record.tool_name in {"write_file", "edit_code"}
            or record.evidence_type
            in {EvidenceType.FILE_CREATED, EvidenceType.FILE_CHANGED}
            for record in self.records
        )

    def success_records(self, *types: EvidenceType) -> list[EvidenceRecord]:
        wanted = set(types)
        return [
            record
            for record in self.records
            if record.success and record.evidence_type in wanted
        ]

    def current_changed_paths(self) -> list[str]:
        return sorted(self.changed_hashes)

    def strongest_verification_kind_passed(self) -> CommandKind | None:
        """The most trusted kind of check that passed for the current change set."""
        return strongest_kind(self._verification_kinds_passed)

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
            len(self._command_keys),
            len(self._review_keys),
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
            self._verification_kinds_passed = set()
            self.review_ran_after_last_change = False
            self.open_severe_review_findings = 0
        elif record.evidence_type == EvidenceType.REVIEW_COMPLETED and record.success:
            self.review_ran_after_last_change = True
            self.open_severe_review_findings += count_severe_findings(
                record.review_severity_summary
            )
        elif record.evidence_type == EvidenceType.VERIFICATION_PASSED and record.success:
            self.latest_verification_passed = True
            self.latest_verification_evidence_id = record.evidence_id
            self.verification_hashes = dict(self.changed_hashes)
            kind = record.metadata.get("verification_kind")
            if kind is not None:
                self._verification_kinds_passed.add(CommandKind(kind))
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
        elif record.evidence_type in {
            EvidenceType.COMMAND_SUCCEEDED,
            EvidenceType.COMMAND_FAILED,
        }:
            # A non-verification command still moves the task forward when it is a
            # new observation, so distinct runs count as progress while a repeated
            # identical command does not.
            self._command_keys.add(record.summary)
        elif record.evidence_type == EvidenceType.REVIEW_COMPLETED:
            # A fresh review is genuine progress: without this, a completion
            # rejection that asked for a review would keep counting toward the
            # gate's fail-open pacing even after the model complied. Keyed by
            # summary so repeating an identical review is not progress.
            self._review_keys.add(record.summary)


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
    classify_verification: VerificationClassifier | None = None,
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
        # A command only counts as verification when it actually exercises the
        # code (a test/build/typecheck/lint runner). A trivial exit-0 command
        # (echo, ls, cat ...) is recorded as a neutral command run, so it can
        # never satisfy the completion gate's verification requirement. The
        # classified kind travels in the record's metadata so the ledger can
        # judge whether the *strongest* applicable check has passed.
        argv = payload.get("argv") or []
        kind = classify_verification(argv) if classify_verification else CommandKind.TEST
        if kind is not None:
            evidence_type = (
                EvidenceType.VERIFICATION_PASSED
                if command_success
                else EvidenceType.VERIFICATION_FAILED
            )
        else:
            evidence_type = (
                EvidenceType.COMMAND_SUCCEEDED
                if command_success
                else EvidenceType.COMMAND_FAILED
            )
        return [
            EvidenceRecord(
                **common,
                evidence_type=evidence_type,
                summary=_command_summary(payload, max_summary_chars),
                command_argv=payload.get("argv"),
                command_exit_code=exit_code if isinstance(exit_code, int) else None,
                timed_out=bool(payload.get("timed_out")),
                cancelled=bool(payload.get("cancelled")),
                metadata={
                    "cwd": payload.get("cwd"),
                    "verification_kind": kind.value if kind else None,
                },
            )
        ]
    if tool_name in _REVIEW_TOOLS:
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
    if tool_name == "dispatch_agent":
        return _records_from_subagent_reports(
            common=common,
            payload=payload,
            classify_verification=classify_verification,
        )
    return []


def _records_from_subagent_reports(
    *,
    common: dict[str, Any],
    payload: dict[str, Any],
    classify_verification: VerificationClassifier | None,
) -> list[EvidenceRecord]:
    """Convert delegated sub-agents' evidence digests into parent ledger records.

    Sub-agents act directly on the shared workspace, so their file changes and
    verification runs are as real as the parent's own - without this conversion
    the completion gate would reject a delegated implementation for "missing"
    evidence and push the model to redo (or fabricate) the work. Items are
    replayed in the order the sub-agent performed them, so a verification that
    ran after the last change still counts as current.
    """
    reports = payload.get("reports")
    if not isinstance(reports, list):
        return []
    records: list[EvidenceRecord] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        agent = str(report.get("name") or report.get("agent_type") or "sub-agent")
        for raw_item in report.get("evidence") or []:
            if not isinstance(raw_item, dict):
                continue
            record = _record_from_subagent_item(
                common=common,
                agent=agent,
                item=raw_item,
                classify_verification=classify_verification,
            )
            if record is not None:
                records.append(record)
    return records


def _record_from_subagent_item(
    *,
    common: dict[str, Any],
    agent: str,
    item: dict[str, Any],
    classify_verification: VerificationClassifier | None,
) -> EvidenceRecord | None:
    # The digest reflects tool calls that actually completed inside the child,
    # so every item except a failed command is a successful observation - the
    # dispatch call's own success flag (in ``common``) does not apply per item.
    fields = {**common}
    fields.pop("success", None)
    kind = str(item.get("kind") or "")
    path = str(item.get("path") or "")
    if kind == "file_read" and path:
        return EvidenceRecord(
            **fields,
            success=True,
            evidence_type=EvidenceType.FILE_READ,
            summary=f"Sub-agent {agent} read {path}.",
            affected_paths=[path],
        )
    if kind in {"file_created", "file_changed"} and path:
        new_hash = str(item.get("new_sha256") or "")
        return EvidenceRecord(
            **fields,
            success=True,
            evidence_type=EvidenceType.FILE_CREATED
            if kind == "file_created"
            else EvidenceType.FILE_CHANGED,
            summary=f"Sub-agent {agent} wrote {path}.",
            affected_paths=[path],
            old_hashes={path: item.get("old_sha256")},
            new_hashes={path: new_hash} if new_hash else {},
        )
    if kind == "review":
        severities = item.get("severities")
        summary_counts = (
            {str(k): int(v) for k, v in severities.items()}
            if isinstance(severities, dict)
            else {}
        )
        total = sum(summary_counts.values())
        return EvidenceRecord(
            **fields,
            success=True,
            evidence_type=EvidenceType.REVIEW_COMPLETED,
            summary=f"Sub-agent {agent} completed a review ({total} finding(s)).",
            review_severity_summary=summary_counts,
        )
    if kind == "command":
        argv = item.get("argv") or []
        exit_code = item.get("exit_code")
        succeeded = exit_code == 0
        verification = (
            classify_verification(argv) if classify_verification else CommandKind.TEST
        )
        if verification is not None:
            evidence_type = (
                EvidenceType.VERIFICATION_PASSED
                if succeeded
                else EvidenceType.VERIFICATION_FAILED
            )
        else:
            evidence_type = (
                EvidenceType.COMMAND_SUCCEEDED
                if succeeded
                else EvidenceType.COMMAND_FAILED
            )
        return EvidenceRecord(
            **fields,
            success=succeeded,
            evidence_type=evidence_type,
            summary=f"Sub-agent {agent} ran {argv!r} (exit {exit_code}).",
            command_argv=argv,
            command_exit_code=exit_code if isinstance(exit_code, int) else None,
            metadata={
                "verification_kind": verification.value if verification else None
            },
        )
    return None


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
