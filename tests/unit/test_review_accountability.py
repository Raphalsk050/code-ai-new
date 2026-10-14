"""Review results reach the ledger, and a claim's disclosures reach the user.

The review tools (and the reviewer sub-agent) exist to judge quality; these
tests pin that their results are recorded as review evidence, and that what a
success completion claim reports as still open is shown in the final message
instead of vanishing.
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
        config=PlannerConfig(),
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


async def test_disclosed_review_findings_reach_the_final_message(tmp_path) -> None:
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

    decision = await service.evaluate_completion(
        {"summary": "done", "remaining_issues": ["reviewer flagged unvalidated input"]}
    )

    assert decision.accepted is True
    assert "reviewer flagged unvalidated input" in (service.accepted_final_text or "")


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
    assert records[0].review_severity_summary == {"major": 1}
