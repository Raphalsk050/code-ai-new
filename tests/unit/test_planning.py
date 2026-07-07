from __future__ import annotations

from code_ai.config.models import PlannerConfig
from code_ai.core.planning import PlannerService, PlanningPhase, TaskProfile
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.filesystem import EditCodeTool, ListFilesTool, ReadFileTool, WriteFileTool
from code_ai.tools.internal import CompleteTaskTool, FinishDiscoveryTool, RequestExternalGapTool
from code_ai.tools.registry import ToolRegistry
from code_ai.tools.search import SearchCodeTool
from code_ai.tools.web import WebSearchTool


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ListFilesTool(),
        SearchCodeTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditCodeTool(),
        WebSearchTool(),
        FinishDiscoveryTool(),
        RequestExternalGapTool(),
        CompleteTaskTool(),
    ):
        registry.register(tool)
    return registry


def test_task_profile_keeps_obvious_fix_as_mutation() -> None:
    profile = TaskProfile.from_user_text("Fix the authentication bug in this repository.")

    assert profile.requires_workspace_mutation is True
    assert profile.requires_local_context is True
    assert profile.requires_verification is True
    assert profile.allows_web_first is False


def test_task_profile_keeps_greeting_as_toolless_conversation() -> None:
    profile = TaskProfile.from_user_text("Olá")
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    service.profile = profile
    service.plan = None

    assert profile.requires_local_context is False
    assert profile.requires_workspace_mutation is False
    assert profile.intent == "conversation"
    assert service.policy.allowed_tool_names(
        registry=make_registry(),
        profile=profile,
        mode=service.mode,
        phase=service.phase,
        current_step=None,
    ) == set()


def test_advisory_keeps_tools_for_misclassified_implementation_request() -> None:
    # "faça um jogo pong" sits outside the mutation-marker set, so the surface
    # classifier mislabels it CONVERSATION. Advisory mode must still expose the
    # tools; otherwise the model is handed a tool-less request, prints the
    # edit_code/write_file call as text, and it leaks into the chat.
    profile = TaskProfile.from_user_text("faça um jogo pong em python")
    service = PlannerService(
        config=PlannerConfig(),  # advisory is the default
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    service.profile = profile

    assert profile.intent == "conversation"
    allowed = service.policy.allowed_tool_names(
        registry=make_registry(),
        profile=profile,
        mode=service.mode,
        phase=service.phase,
        current_step=None,
        advisory=True,
    )
    assert {"write_file", "edit_code"} <= allowed


def test_task_profile_treats_read_request_as_local_inspection() -> None:
    profile = TaskProfile.from_user_text("read the note")

    assert profile.requires_local_context is True
    assert profile.intent == "local_inspection"


def test_task_profile_treats_project_today_as_local_current_state() -> None:
    profile = TaskProfile.from_user_text("O que temos no projeto hoje?")

    assert profile.intent == "local_inspection"
    assert profile.requires_local_context is True
    assert profile.requires_external_information is False
    assert profile.allows_web_first is False
    assert "Use current external evidence before answering." not in profile.acceptance_criteria


def test_task_profile_keeps_external_current_questions_on_web() -> None:
    sports = TaskProfile.from_user_text("quem vai jogar no jogo da copa de hoje")
    package = TaskProfile.from_user_text("pesquise na internet a versao atual do pytest")

    assert sports.intent == "external_research"
    assert sports.requires_local_context is False
    assert sports.requires_external_information is True
    assert sports.allows_web_first is True
    assert package.intent == "external_research"
    assert package.requires_external_information is True
    assert package.allows_web_first is True


def test_update_document_is_a_local_mutation_not_web_research() -> None:
    # Regression: "atualize" (PT update) was not a mutation marker, and "atual"
    # substring-matched inside it as a time-sensitive marker, so a local file edit
    # was misclassified as external research and the agent looped on web_search.
    profile = TaskProfile.from_user_text("ok, consegui, atualize o progresso no documento")

    assert profile.requires_workspace_mutation is True
    assert profile.requires_external_information is False
    assert profile.intent == "implementation"


def test_time_marker_still_matches_as_a_whole_word() -> None:
    # The substring fix must not lose real signals: "atual" as its own word still
    # flags a question as time-sensitive/external.
    profile = TaskProfile.from_user_text("qual a versao atual do pytest?")

    assert profile.requires_external_information is True


async def test_local_edit_settles_misclassified_research_plan() -> None:
    # Graceful degradation: even if a task is misclassified as external research,
    # a real local file mutation must settle the plan toward completion instead of
    # trapping the agent in a web_search loop demanding evidence it will never get.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("pesquise a versao atual do pytest", provider_supports_tools=True)
    assert service.profile.requires_external_information is True
    assert any(step.kind.value == "RESEARCH_WEB" for step in service.plan.steps)

    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "NOTES.md", "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )

    # The unsatisfied research step is skipped and the plan moves to completion,
    # so the runtime stops demanding web_search.
    assert service.phase == PlanningPhase.COMPLETE
    assert all(
        step.status.value != "IN_PROGRESS"
        for step in service.plan.steps
        if step.kind.value == "RESEARCH_WEB"
    )
    decision = await service.evaluate_completion({"summary": "updated notes"})
    assert decision.accepted is True


async def test_genuine_research_is_not_skipped_when_web_evidence_exists() -> None:
    # The degradation must not fire for a real research task: once web evidence is
    # recorded, a later local note-taking edit keeps the research step satisfied
    # rather than skipping it.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("pesquise a versao atual do pytest", provider_supports_tools=True)

    await service.record_tool_result(
        tool_call_id="s1",
        tool_name="web_search",
        payload={"query": "pytest latest version", "results": [{"title": "t", "url": "u"}]},
        success=True,
    )
    research_before = [
        step.status.value
        for step in service.plan.steps
        if step.kind.value == "RESEARCH_WEB"
    ]
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "NOTES.md", "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )
    research_after = [
        step.status.value
        for step in service.plan.steps
        if step.kind.value == "RESEARCH_WEB"
    ]

    assert "SKIPPED" not in research_after
    assert research_before  # sanity: the plan had a research step


async def test_plan_mode_denies_mutating_and_process_tools() -> None:
    service = PlannerService(
        config=PlannerConfig(mode="plan"),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    registry = make_registry()
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)

    write = service.evaluate_tool("write_file", registry)
    web = service.evaluate_tool("web_search", registry)

    assert write.allowed is False
    assert "PLAN mode" in write.reason
    assert web.allowed is False


async def test_strict_local_workspace_task_denies_web_before_external_gap() -> None:
    service = PlannerService(
        config=PlannerConfig(tool_policy="strict"),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    registry = make_registry()
    await service.begin_turn(
        "Fix the authentication bug in this repository.",
        provider_supports_tools=True,
    )

    decision = service.evaluate_tool("web_search", registry)

    assert service.phase == PlanningPhase.DISCOVER_LOCAL
    assert decision.allowed is False
    assert "validated external gap" in decision.reason


async def test_advisory_policy_allows_web_but_recommends_local_first() -> None:
    service = PlannerService(
        config=PlannerConfig(),  # advisory is the default
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    registry = make_registry()
    await service.begin_turn(
        "Fix the authentication bug in this repository.",
        provider_supports_tools=True,
    )

    decision = service.evaluate_tool("web_search", registry)

    # Fail-open: the tool stays callable so a misclassified task is never blocked,
    # but local-first guidance still steers the model away from it.
    assert decision.allowed is True
    assert "web_search" not in service.recommended_tool_names(registry)
    assert "read_file" in service.recommended_tool_names(registry)


async def test_generic_external_gap_does_not_unlock_web_for_local_question() -> None:
    service = PlannerService(
        config=PlannerConfig(tool_policy="strict"),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    registry = make_registry()
    await service.begin_turn("O que temos no projeto hoje?", provider_supports_tools=True)

    await service.record_tool_result(
        tool_call_id="list_1",
        tool_name="list_files",
        payload={"path": ".", "entries": [], "skipped_count": 0},
        success=True,
    )
    await service.record_tool_result(
        tool_call_id="finish_1",
        tool_name="finish_discovery",
        payload={"summary": "Workspace inspected."},
        success=True,
    )
    await service.record_tool_result(
        tool_call_id="gap_1",
        tool_name="request_external_gap",
        payload={
            "summary": "External information requested.",
            "external_knowledge_gaps": [
                {
                    "question": "What public project matches this workspace?",
                    "why_local_files_are_insufficient": (
                        "Need external evidence because local files are insufficient."
                    ),
                    "decision_depends_on": "External information from the web.",
                }
            ],
        },
        success=True,
    )

    assert service.approved_external_gap is False
    assert service.approved_external_gaps == ()
    decision = service.evaluate_tool("web_search", registry)
    assert decision.allowed is False
    assert "validated external gap" in decision.reason


async def test_concrete_external_gap_unlocks_web_after_local_evidence() -> None:
    service = PlannerService(
        config=PlannerConfig(tool_policy="strict"),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    registry = make_registry()
    await service.begin_turn("Fix this package integration bug", provider_supports_tools=True)

    await service.record_tool_result(
        tool_call_id="read_1",
        tool_name="read_file",
        payload={
            "path": "pyproject.toml",
            "sha256": "abc",
            "content": "dependencies = ['example']",
        },
        success=True,
    )
    await service.record_tool_result(
        tool_call_id="gap_1",
        tool_name="request_external_gap",
        payload={
            "summary": "Need current package documentation.",
            "external_knowledge_gaps": [
                {
                    "question": "Which package version documents this integration behavior?",
                    "why_local_files_are_insufficient": (
                        "Local files identify the package but not the current version docs."
                    ),
                    "decision_depends_on": (
                        "The fix depends on current package documentation and version behavior."
                    ),
                }
            ],
        },
        success=True,
    )

    decision = service.evaluate_tool("web_search", registry)

    assert service.approved_external_gap is True
    assert decision.allowed is True


async def test_completion_requires_file_change_and_verification_evidence() -> None:
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)

    rejected = await service.evaluate_completion(
        {
            "summary": "done",
        }
    )

    assert rejected.accepted is False
    assert any("file-change" in item for item in rejected.missing_requirements)


async def test_completion_requires_verification_once_files_change_even_if_unclassified(
    tmp_path,
) -> None:
    # "faça um jogo de pong" sits outside the mutation-marker set, so the surface
    # classifier reads it as CONVERSATION (requires_workspace_mutation is False).
    # But once the model actually writes a file, completion must still be backed by
    # verification: the gate keys off real evidence, not the brittle label. The
    # project exposes a test runner, so verification genuinely applies.
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
    )
    await service.begin_turn("faça um jogo de pong em python", provider_supports_tools=True)
    assert service.profile.requires_workspace_mutation is False

    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "pong.py", "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )
    rejected = await service.evaluate_completion({"summary": "created pong"})

    assert rejected.accepted is False
    assert any("verification" in item for item in rejected.missing_requirements)


async def test_documentation_only_change_completes_without_verification() -> None:
    # A pure documentation change (a Markdown tracker) has nothing executable to
    # verify, so completion must not be blocked on verification evidence.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create PROGRESSO.md", provider_supports_tools=True)

    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "PROGRESSO.md", "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )
    decision = await service.evaluate_completion({"summary": "created tracker"})

    assert decision.accepted is True


async def test_mixed_change_with_code_still_requires_verification(tmp_path) -> None:
    # If any non-documentation file changes, the verification gate stays strict
    # even when a doc file changed alongside it (project exposes a test runner).
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
        payload={"path": "README.md", "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )
    await service.record_tool_result(
        tool_call_id="w2",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": None, "new_sha256": "def"},
        success=True,
    )
    rejected = await service.evaluate_completion({"summary": "done"})

    assert rejected.accepted is False
    assert any("verification" in item for item in rejected.missing_requirements)


async def test_task_context_shows_model_plan_step_as_current() -> None:
    # Once the model authors a plan, the runtime context it sees must focus on the
    # model's own step, not the generic internal skeleton title — so the model
    # executes the plan it declared rather than a template.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Read config.py", "Write the module"])

    block = service.task_context_block(recommended_tool_names={"read_file"})

    assert "Current step: Read config.py" in block
    # The generic skeleton step title no longer drives the narrative.
    assert "Inspect the local workspace" not in block


async def test_completion_reconciles_against_model_plan_not_skeleton() -> None:
    # When the model submitted a plan, completion lists *its* untouched steps so it
    # is guided back to its own checklist, instead of the generic skeleton wording.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Read example", "Write module", "Run tests"])

    rejected = await service.evaluate_completion({"summary": "done"})

    assert rejected.accepted is False
    assert any("declared plan steps" in item for item in rejected.missing_requirements)
    assert not any(
        "required plan steps are incomplete" in item
        for item in rejected.missing_requirements
    )


async def test_model_plan_does_not_block_completion_once_change_is_verified() -> None:
    # The model's checklist cursor advances coarsely, so a longer plan can lag
    # behind reality. Once the change is actually verified, completion must trust
    # the evidence (fail-soft) rather than block on untouched checklist tail items.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["a", "b", "c", "d", "e"])

    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": None, "new_sha256": "h1"},
        success=True,
    )
    await service.record_tool_result(
        tool_call_id="v1",
        tool_name="execute_command",
        payload={"argv": ["true"], "exit_code": 0, "stdout": "", "stderr": ""},
        success=True,
    )

    # The cursor lags (5 declared steps, far fewer evidence signals), but the
    # change is verified, so completion is accepted instead of blocked.
    assert any(
        step.status.value == "PENDING" for step in service.agent_plan.steps
    )
    decision = await service.evaluate_completion({"summary": "created and verified"})

    assert decision.accepted is True


async def test_blocked_completion_is_accepted_without_structured_issues() -> None:
    # A genuine "blocked" outcome must never trap the agent: even when the model
    # only supplies a summary (no remaining_issues/limitations), completion is
    # accepted and the summary surfaces, instead of the turn spinning to a
    # budget/stall wind-down that discards the model's real explanation.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Fix the insecure zsh directories", provider_supports_tools=True)

    decision = await service.evaluate_completion(
        {
            "summary": "Needs sudo password; ~/.zshrc is outside the workspace.",
            "outcome": "blocked",
        }
    )

    assert decision.accepted is True
    assert decision.outcome == "blocked"
    assert decision.final_text == "Needs sudo password; ~/.zshrc is outside the workspace."


def _capture(bus: AsyncEventBus) -> list:
    events: list = []
    bus.subscribe(lambda event: events.append(event))
    return events


async def test_accepted_completion_emits_settled_plan_snapshot() -> None:
    # The sidebar only re-renders on snapshot-carrying events. Without a final
    # snapshot after complete_task is accepted, it freezes on the last running
    # step ("3/4 · active") even though the plan settled internally.
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=bus,
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Read example", "Write module", "Run tests"])
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": None, "new_sha256": "h1"},
        success=True,
    )
    await service.record_tool_result(
        tool_call_id="v1",
        tool_name="execute_command",
        payload={"argv": ["true"], "exit_code": 0, "stdout": "", "stderr": ""},
        success=True,
    )

    decision = await service.evaluate_completion({"summary": "created and verified"})

    assert decision.accepted is True
    settled = [e for e in events if e.event_type == "planning.plan.completed"]
    assert len(settled) == 1
    payload = settled[0].payload
    assert payload["status"] == "COMPLETED"
    assert payload["progress"] == "3/3"
    assert payload["remaining_steps"] == []
    assert payload["current_step"] is None


async def test_blocked_completion_emits_settled_plan_snapshot() -> None:
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=bus,
        session_id="session",
    )
    await service.begin_turn("Fix the login bug", provider_supports_tools=True)
    await service.submit_agent_plan(["Reproduce", "Fix", "Verify"])

    decision = await service.evaluate_completion(
        {"summary": "Cannot reproduce without prod credentials.", "outcome": "blocked"}
    )

    assert decision.accepted is True
    settled = [e for e in events if e.event_type == "planning.plan.blocked"]
    assert len(settled) == 1
    payload = settled[0].payload
    assert payload["status"] == "BLOCKED"
    # The step that was running is frozen as failed, not left spinning.
    assert payload["current_step"] == "Reproduce"
    assert payload["current_step_status"] == "FAILED"


async def test_begin_turn_does_not_reveal_a_plan_before_the_model_authors_one() -> None:
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )

    await service.begin_turn("Create src/example.py", provider_supports_tools=True)

    # No plan/step events yet: the sidebar stays hidden until submit_plan, and the
    # snapshot carries no step fields the UI could render.
    assert not [e for e in events if e.event_type.startswith("planning.plan")]
    assert not [e for e in events if e.event_type.startswith("planning.step")]
    assert service.agent_plan is None
    assert "current_step" not in service.plan_snapshot()


async def test_submit_plan_reveals_model_authored_steps() -> None:
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)

    await service.record_tool_result(
        tool_call_id="plan_1",
        tool_name="submit_plan",
        payload={"steps": ["Read example.py", "Write the new module", "Run the tests"]},
        success=True,
    )

    created = [e for e in events if e.event_type == "planning.plan.created"]
    assert len(created) == 1
    snapshot = service.plan_snapshot()
    assert snapshot["current_step"] == "Read example.py"
    assert snapshot["remaining_steps"] == [
        "Read example.py",
        "Write the new module",
        "Run the tests",
    ]
    assert snapshot["progress"] == "0/3"


async def test_evidence_alone_does_not_advance_model_checklist() -> None:
    # The model owns its checklist cursor: gathering evidence must not race the
    # sidebar ahead, or the model would be told it is on a step it never reached.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Write the module", "Verify"])

    await service.record_tool_result(
        tool_call_id="list_1",
        tool_name="list_files",
        payload={"path": ".", "entries": ["a"], "skipped_count": 0},
        success=True,
    )

    snapshot = service.plan_snapshot()
    assert snapshot["completed_steps"] == []
    assert snapshot["current_step"] == "Inspect files"


async def test_complete_plan_step_advances_model_checklist() -> None:
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Write the module", "Verify"])

    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Inspect files"},
        success=True,
    )

    snapshot = service.plan_snapshot()
    assert snapshot["completed_steps"] == ["Inspect files"]
    assert snapshot["current_step"] == "Write the module"


async def test_complete_plan_step_catches_up_to_the_named_step() -> None:
    # Regression: the model worked through several checklist steps in one burst
    # and only then reported the last of them. The cursor used to advance a single
    # step per call regardless of the declared title, so the sidebar lagged behind
    # real progress until complete_task's complete_all() flipped everything at
    # once at the very end. Naming a later step must catch the cursor up through
    # it, emitting one completed event per step so the panel marks each one.
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(
        ["Inspect files", "Write the module", "Add the tests", "Verify"]
    )

    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Add the tests"},
        success=True,
    )

    snapshot = service.plan_snapshot()
    assert snapshot["completed_steps"] == [
        "Inspect files",
        "Write the module",
        "Add the tests",
    ]
    assert snapshot["current_step"] == "Verify"
    completed_events = [
        e for e in events if e.event_type == "planning.step.completed"
    ]
    assert len(completed_events) == 3


async def test_complete_plan_step_naming_the_final_step_declares_it() -> None:
    # Catching up to the *final* step must not settle it (only completion does);
    # it completes everything before it and records the declaration so a clean
    # final answer can settle the plan.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)
    await service.submit_agent_plan(
        ["Inspect files", "Summarise findings", "Present the summary"]
    )

    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Present the summary"},
        success=True,
    )

    snapshot = service.plan_snapshot()
    assert snapshot["progress"] == "2/3"
    assert snapshot["current_step"] == "Present the summary"
    assert service.agent_plan.final_step_declared is True


async def test_complete_plan_step_with_unknown_title_advances_one() -> None:
    # A title that matches nothing pending (free-form phrasing, or a step already
    # completed) falls back to the plain advance-by-one behaviour.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Write the module", "Verify"])

    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Finished looking at the files"},
        success=True,
    )

    snapshot = service.plan_snapshot()
    assert snapshot["completed_steps"] == ["Inspect files"]
    assert snapshot["current_step"] == "Write the module"


async def test_advancing_model_checklist_counts_as_progress() -> None:
    # Regression: the model advancing its own checklist via complete_plan_step is
    # real forward progress the user sees in the sidebar. progress_signature must
    # change, otherwise the orchestrator's stall detector kills the turn mid-plan
    # and the user never gets a final answer.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create PROGRESSO.md", provider_supports_tools=True)
    await service.submit_agent_plan(["Write file", "Add roadmap", "Add notes space"])

    before = service.progress_signature()
    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Write file"},
        success=True,
    )
    after = service.progress_signature()

    assert after != before


async def test_resume_keeps_plan_and_re_emits_active_sidebar() -> None:
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(mode="plan"), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Write the module", "Verify"])

    events = _capture(bus)
    # The plan→act handoff: a resume must not rebuild the plan from the
    # continuation text, and it must re-surface the existing ACTIVE checklist so
    # the collapsed sidebar reappears.
    await service.begin_turn(
        "Plano aprovado. Execute agora.", provider_supports_tools=True, resume=True
    )

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "ACTIVE"
    assert snapshot["current_step"] == "Inspect files"
    assert snapshot["remaining_steps"] == ["Inspect files", "Write the module", "Verify"]
    started = [e for e in events if e.event_type == "planning.step.started"]
    assert started, "resume should re-emit a step-started snapshot to show the sidebar"
    assert started[-1].payload["status"] == "ACTIVE"


async def test_resubmitting_plan_emits_revised() -> None:
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session"
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)

    await service.submit_agent_plan(["First plan step"])
    await service.submit_agent_plan(["Revised step one", "Revised step two"])

    assert len([e for e in events if e.event_type == "planning.plan.created"]) == 1
    assert len([e for e in events if e.event_type == "planning.plan.revised"]) == 1
    assert service.plan_snapshot()["current_step"] == "Revised step one"


async def test_complete_plan_step_on_final_step_declares_instead_of_advancing() -> None:
    # Regression: the model completed every step of its checklist via
    # complete_plan_step, but the final call was silently ignored (the last step
    # only settles with the whole plan) while the tool echoed success. The model
    # then answered in prose believing it was done, and the sidebar froze at
    # N-1/N. The final declaration must be remembered and the result made honest.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Present the summary"])

    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Inspect files"},
        success=True,
    )
    assert service.agent_plan.final_step_declared is False

    await service.record_tool_result(
        tool_call_id="step_2",
        tool_name="complete_plan_step",
        payload={"completed_step": "Present the summary"},
        success=True,
    )

    snapshot = service.plan_snapshot()
    assert snapshot["progress"] == "1/2"
    assert snapshot["current_step"] == "Present the summary"
    assert service.agent_plan.final_step_declared is True


async def test_annotate_plan_step_payload_is_honest_only_on_final_step() -> None:
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Present the summary"])

    # Mid-plan the echo passes through untouched.
    payload = {"completed_step": "Inspect files"}
    assert service.annotate_plan_step_payload(payload) == payload

    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload=payload,
        success=True,
    )

    annotated = service.annotate_plan_step_payload(
        {"completed_step": "Present the summary"}
    )
    assert annotated["status"] == "final_step_still_running"
    assert "complete_task" in annotated["note"]


async def test_final_answer_settles_plan_whose_last_step_was_declared() -> None:
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Present the summary"])
    for step in ("Inspect files", "Present the summary"):
        await service.record_tool_result(
            tool_call_id=f"step_{step}",
            tool_name="complete_plan_step",
            payload={"completed_step": step},
            success=True,
        )

    await service.settle_agent_plan_on_final_answer()

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "COMPLETED"
    assert snapshot["progress"] == "2/2"
    completed = [e for e in events if e.event_type == "planning.plan.completed"]
    assert completed and completed[-1].payload["status"] == "COMPLETED"


async def test_final_answer_leaves_undeclared_plan_active() -> None:
    # Without the model ever declaring the final step done, a prose ending is
    # genuinely unfinished work: the sidebar must keep showing where it stopped.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Present the summary"])
    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Inspect files"},
        success=True,
    )

    await service.settle_agent_plan_on_final_answer()

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "ACTIVE"
    assert snapshot["progress"] == "1/2"
