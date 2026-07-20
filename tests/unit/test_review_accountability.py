"""Severe review findings must be fixed or disclosed before completion.

The review tools (and the reviewer sub-agent) exist to judge quality; these
tests pin the contract that their serious findings cannot be silently ignored
by a success completion claim, and that review evidence flows into the ledger
from every channel (direct tools, test_review, sub-agent digests).
"""

from __future__ import annotations

from code_ai.config.models import PlannerConfig
from code_ai.core.planning import PlannerService
from code_ai.core.planning.evidence import EvidenceLedger
from code_ai.core.planning.models import EvidenceType
from code_ai.events.bus import AsyncEventBus


async def _service_with_verified_change(tmp_path) -> PlannerService:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )
    await service.record_tool_result(
        tool_call_id="c1",
        tool_name="execute_command",
        payload={"argv": ["pytest", "-q"], "exit_code": 0, "stdout": "ok", "stderr": ""},
        success=True,
    )
    return service


async def test_severe_findings_block_completion_until_disclosed(tmp_path) -> None:
    service = await _service_with_verified_change(tmp_path)
    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="code_review",
        payload={
            "summary": "found problems",
            "findings": [
                {"severity": "critical", "message": "unvalidated input"},
                {"severity": "low", "message": "naming nit"},
            ],
        },
        success=True,
    )

    decision = await service.evaluate_completion({"summary": "done"})

    assert decision.accepted is False
    assert any("finding" in item for item in decision.missing_requirements)

    # Disclosing the open finding honestly releases the claim.
    decision = await service.evaluate_completion(
        {"summary": "done", "remaining_issues": ["reviewer flagged unvalidated input"]}
    )
    assert decision.accepted is True


async def test_findings_reset_when_a_later_change_lands(tmp_path) -> None:
    service = await _service_with_verified_change(tmp_path)
    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="code_review",
        payload={
            "summary": "found problems",
            "findings": [{"severity": "high", "message": "bug"}],
        },
        success=True,
    )
    assert service.ledger.open_severe_review_findings == 1

    # A later change plausibly fixed the finding; the review is stale now.
    await service.record_tool_result(
        tool_call_id="w2",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": "abc", "new_sha256": "def"},
        success=True,
    )

    assert service.ledger.open_severe_review_findings == 0
    assert service.ledger.review_ran_after_last_change is False


def test_test_review_results_are_recorded_as_review_evidence() -> None:
    ledger = EvidenceLedger(session_id="session")
    records = ledger.record_tool_result(
        plan=None,
        step_id=None,
        tool_call_id="r1",
        tool_name="test_review",
        payload={"summary": "weak tests", "findings": [{"severity": "major"}]},
        success=True,
    )

    assert [record.evidence_type for record in records] == [EvidenceType.REVIEW_COMPLETED]
    assert ledger.open_severe_review_findings == 1


def test_minor_findings_do_not_block() -> None:
    ledger = EvidenceLedger(session_id="session")
    ledger.record_tool_result(
        plan=None,
        step_id=None,
        tool_call_id="r1",
        tool_name="code_review",
        payload={
            "summary": "nits",
            "findings": [{"severity": "low"}, {"severity": "info"}],
        },
        success=True,
    )

    assert ledger.open_severe_review_findings == 0
    assert ledger.review_ran_after_last_change is True
