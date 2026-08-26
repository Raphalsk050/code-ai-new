from __future__ import annotations

from code_ai.config.models import PlannerConfig
from code_ai.core.planning import PlannerService
from code_ai.core.planning.evidence import EvidenceLedger
from code_ai.core.planning.models import EvidenceType
from code_ai.core.subagents.evidence import (
    SubagentEvidenceCollector,
    SubagentEvidenceItem,
    compact_evidence_items,
)
from code_ai.core.verification import CommandKind
from code_ai.events.bus import AsyncEventBus


def _service() -> PlannerService:
    return PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )


# ------------------------------------------------------------------ #
# Collector: child bus events -> ordered digest
# ------------------------------------------------------------------ #
async def test_collector_captures_workspace_actions_in_order() -> None:
    bus = AsyncEventBus(session_id="child")
    collector = SubagentEvidenceCollector()
    bus.subscribe(collector)

    await bus.emit(
        "tool.call.completed",
        {"name": "read_file", "result": {"path": "src/app.py", "sha256": "r1"}},
    )
    await bus.emit(
        "tool.call.completed",
        {
            "name": "write_file",
            "result": {"path": "src/app.py", "old_sha256": "r1", "new_sha256": "w1"},
        },
    )
    await bus.emit(
        "tool.call.completed",
        {
            "name": "write_file",
            "result": {"path": "src/new.py", "old_sha256": None, "new_sha256": "n1"},
        },
    )
    await bus.emit(
        "tool.call.completed",
        {
            "name": "execute_command",
            "result": {"argv": ["pytest", "-q"], "exit_code": 0},
        },
    )
    await bus.emit(
        "tool.call.completed",
        {
            "name": "code_review",
            "result": {
                "summary": "issues",
                "findings": [{"severity": "Critical"}, {"severity": "low"}],
            },
        },
    )
    # Non-completion events and unknown tools are ignored.
    await bus.emit("tool.call.started", {"name": "write_file"})
    await bus.emit("tool.call.completed", {"name": "web_search", "result": {"results": []}})

    kinds = [item.kind for item in collector.items()]
    assert kinds == ["file_read", "file_changed", "file_created", "command", "review"]
    command = collector.items()[-2]
    assert command.argv == ["pytest", "-q"]
    assert command.exit_code == 0
    review = collector.items()[-1]
    assert review.severities == {"critical": 1, "low": 1}


async def test_collector_ignores_no_op_edits() -> None:
    bus = AsyncEventBus(session_id="child")
    collector = SubagentEvidenceCollector()
    bus.subscribe(collector)

    await bus.emit(
        "tool.call.completed",
        {
            "name": "edit_code",
            "result": {"path": "a.py", "changed": False, "new_sha256": "same"},
        },
    )
    await bus.emit(
        "tool.call.completed",
        {
            "name": "edit_code",
            "result": {
                "path": "a.py",
                "changed": True,
                "old_sha256": "o1",
                "new_sha256": "e1",
            },
        },
    )

    assert [item.kind for item in collector.items()] == ["file_changed"]


def test_compact_evidence_drops_oldest_reads_before_mutations() -> None:
    reads = [
        SubagentEvidenceItem(kind="file_read", path=f"f{i}.py") for i in range(10)
    ]
    change = SubagentEvidenceItem(kind="file_changed", path="x.py", new_sha256="h")
    command = SubagentEvidenceItem(kind="command", argv=["pytest"], exit_code=0)
    items = [*reads, change, command]

    compacted = compact_evidence_items(items, max_items=5)

    assert change in compacted and command in compacted
    assert len(compacted) == 5
    # The reads kept are the most recent ones, order preserved.
    assert [item.path for item in compacted[:3]] == ["f7.py", "f8.py", "f9.py"]


# ------------------------------------------------------------------ #
# Parent ledger: dispatch_agent payload -> evidence records
# ------------------------------------------------------------------ #
def _dispatch_payload() -> dict:
    return {
        "dispatched": 1,
        "reports": [
            {
                "agent_id": "abc123",
                "agent_type": "coder",
                "name": "Turing",
                "status": "completed",
                "summary": "implemented and verified",
                "evidence": [
                    {"kind": "file_read", "path": "src/api.py"},
                    {"kind": "file_created", "path": "src/health.py", "new_sha256": "h1"},
                    {"kind": "command", "argv": ["pytest", "-q"], "exit_code": 0},
                ],
            }
        ],
    }


def test_ledger_converts_subagent_digest_into_records() -> None:
    ledger = EvidenceLedger(session_id="session")
    records = ledger.record_tool_result(
        plan=None,
        step_id=None,
        tool_call_id="d1",
        tool_name="dispatch_agent",
        payload=_dispatch_payload(),
        success=True,
        classify_verification=lambda argv: (
            CommandKind.TEST if list(argv or [])[:1] == ["pytest"] else None
        ),
    )

    types = [record.evidence_type for record in records]
    assert types == [
        EvidenceType.FILE_READ,
        EvidenceType.FILE_CREATED,
        EvidenceType.VERIFICATION_PASSED,
    ]
    assert ledger.current_changed_paths() == ["src/health.py"]
    # The verification ran after the change, so it covers the current state.
    assert ledger.latest_verification_passed is True
    assert all("Turing" in record.summary for record in records)


def test_ledger_replays_subagent_review_findings() -> None:
    ledger = EvidenceLedger(session_id="session")
    payload = {
        "reports": [
            {
                "name": "Lovelace",
                "status": "completed",
                "evidence": [
                    {"kind": "file_changed", "path": "a.py", "new_sha256": "h2"},
                    {"kind": "review", "severities": {"critical": 1, "low": 2}},
                ],
            }
        ]
    }
    records = ledger.record_tool_result(
        plan=None,
        step_id=None,
        tool_call_id="d1",
        tool_name="dispatch_agent",
        payload=payload,
        success=True,
        classify_verification=lambda argv: None,
    )

    assert records[-1].evidence_type == EvidenceType.REVIEW_COMPLETED
    # The reviewer's serious finding gates the parent completion too.
    assert ledger.review_ran_after_last_change is True
    assert ledger.open_severe_review_findings == 1


def test_ledger_records_failed_subagent_command_as_failure() -> None:
    ledger = EvidenceLedger(session_id="session")
    payload = {
        "reports": [
            {
                "name": "Curie",
                "status": "failed",
                "evidence": [
                    {"kind": "file_changed", "path": "a.py", "new_sha256": "h2"},
                    {"kind": "command", "argv": ["pytest", "-q"], "exit_code": 1},
                ],
            }
        ]
    }
    ledger.record_tool_result(
        plan=None,
        step_id=None,
        tool_call_id="d1",
        tool_name="dispatch_agent",
        payload=payload,
        success=True,
        classify_verification=lambda argv: CommandKind.TEST,
    )

    # The change is real even though the sub-agent failed, and the failed
    # verification keeps the completion gate closed.
    assert ledger.current_changed_paths() == ["a.py"]
    assert ledger.latest_verification_passed is False


# ------------------------------------------------------------------ #
# End to end through the planner: delegated work satisfies completion
# ------------------------------------------------------------------ #
async def test_delegated_implementation_satisfies_completion_gate() -> None:
    service = _service()
    await service.begin_turn(
        "adicione um endpoint de health check", provider_supports_tools=True
    )
    assert service.profile.requires_workspace_mutation is True

    await service.record_tool_result(
        tool_call_id="d1",
        tool_name="dispatch_agent",
        payload=_dispatch_payload(),
        success=True,
    )

    assert service.ledger.has_success(EvidenceType.FILE_CREATED)
    decision = await service.evaluate_completion(
        {"summary": "health endpoint added via coder sub-agent"}
    )
    assert decision.accepted is True


async def test_delegation_moves_the_progress_fingerprint() -> None:
    # Without evidence conversion a dispatch round looked like a stall: the
    # ledger fingerprint never moved. Delegated evidence must count as progress.
    service = _service()
    await service.begin_turn(
        "adicione um endpoint de health check", provider_supports_tools=True
    )
    before = service.progress_signature()
    await service.record_tool_result(
        tool_call_id="d1",
        tool_name="dispatch_agent",
        payload=_dispatch_payload(),
        success=True,
    )
    assert service.progress_signature() != before
