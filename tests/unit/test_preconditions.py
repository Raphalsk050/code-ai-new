from __future__ import annotations

from pathlib import Path

from code_ai.config.models import PlannerConfig
from code_ai.core.planning import PlannerService
from code_ai.core.planning.preconditions import PreconditionGate
from code_ai.events.bus import AsyncEventBus


def _service(workspace: Path) -> PlannerService:
    return PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=workspace,
    )


# ------------------------------------------------------------------ #
# Gate unit behaviour
# ------------------------------------------------------------------ #
def test_mutating_an_unread_existing_file_is_deferred_once(tmp_path) -> None:
    (tmp_path / "app.py").write_text("original = True\n")
    gate = PreconditionGate(workspace=tmp_path)

    first = gate.unread_mutation_gap(
        "write_file", {"path": "app.py", "content": "x"}, known_content_paths=set()
    )
    assert first is not None and "app.py" in first and "read_file" in first

    # Fail-open: the same path is never nudged twice, even still unread.
    second = gate.unread_mutation_gap(
        "write_file", {"path": "app.py", "content": "x"}, known_content_paths=set()
    )
    assert second is None


def test_creating_a_new_file_needs_no_prior_read(tmp_path) -> None:
    gate = PreconditionGate(workspace=tmp_path)
    gap = gate.unread_mutation_gap(
        "write_file", {"path": "brand_new.py", "content": "x"}, known_content_paths=set()
    )
    assert gap is None


def test_known_content_paths_ground_the_mutation(tmp_path) -> None:
    (tmp_path / "app.py").write_text("original = True\n")
    gate = PreconditionGate(workspace=tmp_path)
    gap = gate.unread_mutation_gap(
        "edit_code",
        {"path": "./app.py", "old_text": "a", "new_text": "b"},
        known_content_paths={"app.py"},
    )
    assert gap is None


def test_paths_outside_the_workspace_are_not_this_gates_concern(tmp_path) -> None:
    gate = PreconditionGate(workspace=tmp_path)
    gap = gate.unread_mutation_gap(
        "write_file", {"path": "/etc/hosts", "content": "x"}, known_content_paths=set()
    )
    assert gap is None  # the workspace boundary in the tool handles it


def test_non_mutation_tools_are_ignored(tmp_path) -> None:
    (tmp_path / "app.py").write_text("original = True\n")
    gate = PreconditionGate(workspace=tmp_path)
    assert (
        gate.unread_mutation_gap(
            "read_file", {"path": "app.py"}, known_content_paths=set()
        )
        is None
    )


# ------------------------------------------------------------------ #
# Planner integration: evidence grounds the gate, across turns
# ------------------------------------------------------------------ #
async def test_read_evidence_unlocks_the_mutation(tmp_path) -> None:
    (tmp_path / "app.py").write_text("original = True\n")
    service = _service(tmp_path)
    await service.begin_turn("atualize o app.py", provider_supports_tools=True)

    assert service.precondition_gap("edit_code", {"path": "app.py"}) is not None

    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="read_file",
        payload={"path": "app.py", "sha256": "h1"},
        success=True,
    )
    assert service.precondition_gap("edit_code", {"path": "app.py"}) is None


async def test_known_content_survives_turn_boundaries(tmp_path) -> None:
    # A file read in a previous turn is still known content; the gate must not
    # demand a redundant re-read after begin_turn resets the per-turn ledger.
    (tmp_path / "app.py").write_text("original = True\n")
    service = _service(tmp_path)
    await service.begin_turn("leia o app.py", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="read_file",
        payload={"path": "app.py", "sha256": "h1"},
        success=True,
    )

    await service.begin_turn("agora atualize o app.py", provider_supports_tools=True)
    assert service.precondition_gap("write_file", {"path": "app.py"}) is None


async def test_subagent_reads_ground_the_parents_mutations(tmp_path) -> None:
    # Files a delegated sub-agent read or wrote count as known content for the
    # parent: its digest is real observation of the workspace.
    (tmp_path / "api.py").write_text("routes = []\n")
    service = _service(tmp_path)
    await service.begin_turn("adicione um endpoint", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="d1",
        tool_name="dispatch_agent",
        payload={
            "reports": [
                {
                    "name": "Turing",
                    "status": "completed",
                    "evidence": [{"kind": "file_read", "path": "api.py"}],
                }
            ]
        },
        success=True,
    )
    assert service.precondition_gap("edit_code", {"path": "api.py"}) is None


async def test_file_created_this_session_is_known_content(tmp_path) -> None:
    service = _service(tmp_path)
    await service.begin_turn("crie e ajuste um script", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "script.py", "old_sha256": None, "new_sha256": "h1"},
        success=True,
    )
    (tmp_path / "script.py").write_text("print('x')\n")
    assert service.precondition_gap("edit_code", {"path": "script.py"}) is None
