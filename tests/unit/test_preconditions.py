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


# ------------------------------------------------------------------ #
# Delegation gate: implementation is not delegated on assumptions
# ------------------------------------------------------------------ #
_WRITERS = frozenset({"coder"})


def _coder_dispatch() -> dict:
    return {"tasks": [{"agent_type": "coder", "prompt": "add the endpoint"}]}


def test_blind_coder_delegation_is_deferred_once(tmp_path) -> None:
    gate = PreconditionGate(workspace=tmp_path)

    first = gate.blind_delegation_gap(
        _coder_dispatch(), has_local_grounding=False, write_agent_types=_WRITERS
    )
    assert first is not None and "coder" in first and "explorer" in first

    # Fail-open: one nudge per session, then the dispatch proceeds.
    second = gate.blind_delegation_gap(
        _coder_dispatch(), has_local_grounding=False, write_agent_types=_WRITERS
    )
    assert second is None


def test_explorer_fan_out_is_never_gated(tmp_path) -> None:
    gate = PreconditionGate(workspace=tmp_path)
    arguments = {
        "tasks": [
            {"agent_type": "explorer", "prompt": "map the config"},
            {"agent_type": "reviewer", "prompt": "assess module X"},
        ]
    }
    gap = gate.blind_delegation_gap(
        arguments, has_local_grounding=False, write_agent_types=_WRITERS
    )
    assert gap is None  # read-only delegation IS the reconnaissance


def test_grounded_coder_delegation_proceeds(tmp_path) -> None:
    gate = PreconditionGate(workspace=tmp_path)
    gap = gate.blind_delegation_gap(
        _coder_dispatch(), has_local_grounding=True, write_agent_types=_WRITERS
    )
    assert gap is None


async def test_planner_gates_dispatch_until_local_evidence_exists(tmp_path) -> None:
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
        write_agent_types=frozenset({"coder"}),
    )
    await service.begin_turn("adicione um endpoint", provider_supports_tools=True)

    assert service.precondition_gap("dispatch_agent", _coder_dispatch()) is not None

    # A bare workspace listing is not understanding; reading a file is.
    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="read_file",
        payload={"path": "api.py", "sha256": "h1"},
        success=True,
    )
    assert service.precondition_gap("dispatch_agent", _coder_dispatch()) is None


async def test_workspace_listing_alone_does_not_ground_delegation(tmp_path) -> None:
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
        write_agent_types=frozenset({"coder"}),
    )
    await service.begin_turn("adicione um endpoint", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="l1",
        tool_name="list_files",
        payload={"path": ".", "entries": [{"path": "api.py"}]},
        success=True,
    )
    assert service.precondition_gap("dispatch_agent", _coder_dispatch()) is not None


# ------------------------------------------------------------------ #
# Unrequested artifacts: a question ends in an answer, not a document
# ------------------------------------------------------------------ #
def test_unrequested_artifact_write_is_deferred_once(tmp_path) -> None:
    gate = PreconditionGate(workspace=tmp_path)

    first = gate.unrequested_artifact_gap(
        "write_file",
        {"path": "ANALYSIS.md", "content": "findings"},
        task_requests_mutation=False,
    )
    assert first is not None and "chat answer" in first

    # Fail-open: a genuinely required change costs one round-trip at most.
    second = gate.unrequested_artifact_gap(
        "write_file",
        {"path": "ANALYSIS.md", "content": "findings"},
        task_requests_mutation=False,
    )
    assert second is None


def test_mutation_tasks_are_never_artifact_gated(tmp_path) -> None:
    gate = PreconditionGate(workspace=tmp_path)
    gap = gate.unrequested_artifact_gap(
        "write_file", {"path": "app.py", "content": "x"}, task_requests_mutation=True
    )
    assert gap is None


async def test_explanation_question_defers_summary_document(tmp_path) -> None:
    service = _service(tmp_path)
    await service.begin_turn(
        "como funciona a base de codigo desse projeto?", provider_supports_tools=True
    )
    assert service.profile.requires_workspace_mutation is False

    gap = service.precondition_gap(
        "write_file", {"path": "RESUMO.md", "content": "notas"}
    )
    assert gap is not None and "never asked for" in gap


async def test_artifact_nudge_resets_each_turn(tmp_path) -> None:
    service = _service(tmp_path)
    await service.begin_turn("como funciona o modulo x?", provider_supports_tools=True)
    assert service.precondition_gap("write_file", {"path": "a.md"}) is not None
    assert service.precondition_gap("write_file", {"path": "a.md"}) is None  # fail-open

    # A new question must get its own nudge; the previous turn spent its own.
    await service.begin_turn("e como funciona o modulo y?", provider_supports_tools=True)
    assert service.precondition_gap("write_file", {"path": "b.md"}) is not None


async def test_mutation_request_is_not_artifact_gated_via_planner(tmp_path) -> None:
    service = _service(tmp_path)
    await service.begin_turn("adicione um endpoint em api.py", provider_supports_tools=True)
    # New file on a mutation task: neither gate should interfere.
    assert service.precondition_gap("write_file", {"path": "api.py"}) is None


async def test_read_only_context_block_says_answer_in_prose(tmp_path) -> None:
    service = _service(tmp_path)
    await service.begin_turn(
        "como funciona a base de codigo desse projeto?", provider_supports_tools=True
    )
    block = service.task_context_block(recommended_tool_names={"read_file"})

    assert "READ-ONLY TASK" in block
    assert "answer the user directly" in block
    # The mutation-oriented completion rules must not leak into questions:
    # they are what pushed models to fabricate a document as "evidence".
    assert "call complete_task after verification evidence exists" not in block


async def test_mutation_context_block_keeps_completion_rules(tmp_path) -> None:
    service = _service(tmp_path)
    await service.begin_turn("adicione um endpoint em api.py", provider_supports_tools=True)
    block = service.task_context_block(recommended_tool_names={"write_file"})
    assert "call complete_task after verification evidence exists" in block
