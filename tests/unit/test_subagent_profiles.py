from __future__ import annotations

import pytest

from code_ai.config.models import BudgetConfig
from code_ai.core.subagents.profiles import (
    FORBIDDEN_SUBAGENT_CAPABILITIES,
    SubagentProfile,
    default_profile_registry,
)
from code_ai.core.subagents.report import SubagentReport, SubagentStatus
from code_ai.tools.base import ToolCapability


def test_default_registry_exposes_three_profiles() -> None:
    registry = default_profile_registry()
    assert registry.names() == ["coder", "explorer", "reviewer"]
    assert registry.get("EXPLORER") is not None  # case-insensitive
    assert registry.get("unknown") is None


def test_no_profile_requests_forbidden_capabilities() -> None:
    for profile in default_profile_registry().all():
        assert not (profile.allowed_capabilities & FORBIDDEN_SUBAGENT_CAPABILITIES)


def test_profile_construction_rejects_forbidden_capability() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        SubagentProfile(
            name="rogue",
            description="",
            allowed_capabilities=frozenset({ToolCapability.INTERACTIVE_TERMINAL}),
            max_model_steps=10,
            role_prompt="",
            timeout_field="subagent_worker_timeout_s",
        )


def test_read_only_classification() -> None:
    registry = default_profile_registry()
    assert registry.get("explorer").read_only is True
    assert registry.get("explorer").writes is False
    assert registry.get("coder").writes is True
    assert registry.get("coder").read_only is False
    # Reviewer runs processes (build/test) so it is not "read only" in effect.
    assert registry.get("reviewer").writes is False
    assert registry.get("reviewer").runs_processes is True
    assert registry.get("reviewer").read_only is False


def test_timeout_maps_to_budget_field() -> None:
    budgets = BudgetConfig.from_mapping(
        {"subagent_explorer_timeout_s": 90, "subagent_worker_timeout_s": 240}
    )
    registry = default_profile_registry()
    assert registry.get("explorer").timeout_seconds(budgets) == 90
    assert registry.get("coder").timeout_seconds(budgets) == 240
    assert registry.get("reviewer").timeout_seconds(budgets) == 240


def test_describe_lists_every_profile() -> None:
    described = default_profile_registry().describe()
    for name in ("explorer", "coder", "reviewer"):
        assert name in described


def test_report_ok_and_serialization() -> None:
    completed = SubagentReport(
        agent_id="a1",
        agent_type="explorer",
        task="find the config loader",
        status=SubagentStatus.COMPLETED,
        summary="It lives in config/loader.py:42.",
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    assert completed.ok is True
    data = completed.to_dict()
    assert data["status"] == "completed"
    assert data["summary"].startswith("It lives")
    assert data["usage"]["total_tokens"] == 15

    failed = SubagentReport(
        agent_id="a2",
        agent_type="coder",
        task="x",
        status=SubagentStatus.TIMEOUT,
        error="exceeded budget",
    )
    assert failed.ok is False
    assert failed.to_dict()["error"] == "exceeded budget"


def test_new_budget_keys_present_and_positive() -> None:
    budgets = BudgetConfig.from_mapping(None)
    budgets.validate()
    assert budgets.max_subagent_depth == 1
    assert budgets.max_concurrent_subagents > 0
    assert budgets.max_subagents_per_turn > 0
    assert budgets.subagent_retry_max_attempts >= 1
    assert budgets.subagent_circuit_failure_threshold >= 1
    assert budgets.subagent_circuit_reset_s > 0
