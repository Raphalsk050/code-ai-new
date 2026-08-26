from __future__ import annotations

from code_ai.config.models import PlannerConfig
from code_ai.core.planning import PlannerService, PlanningPhase, TaskProfile
from code_ai.core.planning.models import PlanStatus, is_continuation_request
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


def test_explanation_requests_are_never_mutations() -> None:
    # Regression: mutation verbs matched by substring ("adder" tripped "add",
    # "descreva" tripped "escreva") and questions *about* code that mutates
    # ("explique a função update_user") were classified as workspace mutations.
    # The completion gate then demanded file-change evidence for a plain
    # explanation - a requirement no honest answer could ever satisfy.
    explanations = [
        "explique a função update_user",
        "explain what the adder function does",
        "me explique como adicionar um endpoint nesse projeto",
        "como funciona o create_user?",
        "por que o update falha às vezes?",
        "descreva o fluxo de criação de conta",
        "qual arquivo implementa o parser?",
        "how do I add a custom rule?",
        "me diga o que falta implementar no projeto",
    ]
    for text in explanations:
        profile = TaskProfile.from_user_text(text)
        assert profile.requires_workspace_mutation is False, text
        assert profile.requires_verification is False, text


def test_questions_about_implementing_are_not_mutations() -> None:
    # Regression: "pelo que voce comecaria a implementar hoje?" was classified
    # as an implementation task; the runtime then demanded file changes and the
    # model created folders nobody asked for, through three user denials.
    questions = [
        "pelo que voce comecaria a implementar hoje?",
        "por onde eu começo a implementar esse módulo?",
        "sera que vale a pena refatorar o parser?",
        "seria melhor implementar isso em rust ou em go?",
        "should I implement caching here?",
        "devo criar um arquivo de config separado?",
        "que tal implementarmos isso depois do MVP?",
        "would it be safer to remove the fallback?",
    ]
    for text in questions:
        profile = TaskProfile.from_user_text(text)
        assert profile.requires_workspace_mutation is False, text


def test_requests_phrased_as_questions_stay_mutations() -> None:
    # The question veto must not swallow polite or explicit requests: phrasing
    # that addresses the agent with the action still means "do it".
    requests = [
        "pode criar o arquivo config.json?",
        "você pode adicionar um teste pra isso?",
        "can you add a test for this?",
        "could you update the README?",
        "crie um jogo pong, pode ser?",
        "implemente a tela de login, ok?",
    ]
    for text in requests:
        profile = TaskProfile.from_user_text(text)
        assert profile.requires_workspace_mutation is True, text


def test_mutation_requests_keep_their_evidence_gate() -> None:
    # The explanation veto must not weaken real mutations, including inflected
    # forms the old substring markers missed ("adicione", "remova").
    mutations = [
        "adicione um endpoint de health check",
        "remova o código morto de utils.py",
        "adicione X como fallback do parser",
        "edite o arquivo config.toml e atualize a versao",
        "fix the failing test in tests/unit",
        "escreva um script de deploy",
        "update the README with the new flags",
    ]
    for text in mutations:
        profile = TaskProfile.from_user_text(text)
        assert profile.requires_workspace_mutation is True, text


def test_bare_continuation_markers_are_recognised() -> None:
    for text in [
        "continue",
        "continue de onde paramos",
        "Continue.",
        "siga",
        "prossiga com o plano",
        "ok, pode seguir",
        "vamos continuar",
        "keep going",
        "go ahead",
        "continue from where we left off",
    ]:
        assert is_continuation_request(text) is True, text


def test_continuation_detection_ignores_messages_carrying_a_new_objective() -> None:
    # The failure mode this must not have: swallowing a real request because it
    # happens to open with a continuation word.
    for text in [
        "continue, mas agora faça o parser",
        "siga o padrão do arquivo config.py",
        "continue implementando o endpoint de health check",
        "go on and delete the cache directory",
        "olá",
        "",
    ]:
        assert is_continuation_request(text) is False, text


async def test_continuation_turn_keeps_the_previous_mutation_task() -> None:
    # "continue" carries no mutation keyword, so reclassifying it drops the task
    # to CONVERSATION and silences the whole runtime state block mid-work.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn(
        "implemente um endpoint de health check", provider_supports_tools=True
    )
    plan_id = service.plan.plan_id

    await service.begin_turn("continue de onde paramos", provider_supports_tools=True)

    assert service.profile.requires_workspace_mutation is True
    assert service.profile.intent == "implementation"
    assert service.plan.plan_id == plan_id
    assert service.task_context_block(recommended_tool_names={"write_file"}) != ""


async def test_a_new_request_after_a_task_is_still_reclassified() -> None:
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn(
        "implemente um endpoint de health check", provider_supports_tools=True
    )
    first_plan_id = service.plan.plan_id

    await service.begin_turn("explique como funciona o parser", provider_supports_tools=True)

    assert service.profile.requires_workspace_mutation is False
    assert service.plan.plan_id != first_plan_id


async def test_continuation_without_a_live_task_is_classified_normally() -> None:
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )

    await service.begin_turn("continue", provider_supports_tools=True)

    assert service.profile is not None
    assert service.plan is not None


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


async def test_final_answer_settles_prose_plan_backed_by_gathered_evidence() -> None:
    # Regression: a research turn submitted a plan, ran a web search and
    # delivered the full synthesized answer - but never called
    # complete_plan_step, so the sidebar froze at "0/4, waiting for you" with
    # nothing actually pending. Real gathered evidence settles the plan.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn(
        "verifique na internet qual a melhor arquitetura para isso",
        provider_supports_tools=True,
    )
    await service.submit_agent_plan(
        ["Pesquisar padrões atuais", "Sintetizar a recomendação"]
    )
    await service.record_tool_result(
        tool_call_id="s1",
        tool_name="web_search",
        payload={"query": "best architecture", "results": [{"title": "t", "url": "u"}]},
        success=True,
    )

    await service.settle_agent_plan_on_final_answer()

    assert service.agent_plan is not None
    assert service.agent_plan.status == PlanStatus.COMPLETED


async def test_final_answer_without_any_work_leaves_the_plan_paused() -> None:
    # The pure pause case stays apart: plan submitted, no model-initiated
    # evidence (the host's automatic listing does not count), prose ending -
    # the plan is genuinely unfinished and must pause, not complete.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn(
        "leia o projeto e proponha uma arquitetura", provider_supports_tools=True
    )
    await service.record_tool_result(
        tool_call_id="host_list_files_initial",
        tool_name="list_files",
        payload={"path": ".", "entries": []},
        success=True,
        host_initiated=True,
    )
    await service.submit_agent_plan(["Levantar requisitos", "Propor arquitetura"])

    await service.settle_agent_plan_on_final_answer()
    assert service.agent_plan is not None
    assert service.agent_plan.status == PlanStatus.ACTIVE

    await service.suspend_agent_plan()
    assert service.agent_plan.status == PlanStatus.WAITING


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


async def test_double_check_is_folded_into_the_evidence_rejection(tmp_path) -> None:
    # A high-risk change (three files touched) selects the strict policy, and the
    # double-check must ride along with the evidence rejection instead of queuing
    # a second rejection behind it: one rejection lists everything, the model
    # fixes the evidence, and the next complete_task is accepted.
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    service = PlannerService(
        config=PlannerConfig(),  # double_check_completion defaults to True
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
    )
    await service.begin_turn(
        "Create src/a.py, src/b.py and src/c.py", provider_supports_tools=True
    )
    for index, path in enumerate(("src/a.py", "src/b.py", "src/c.py")):
        await service.record_tool_result(
            tool_call_id=f"w{index}",
            tool_name="write_file",
            payload={"path": path, "old_sha256": None, "new_sha256": f"h{index}"},
            success=True,
        )

    first = await service.evaluate_completion({"summary": "created the modules"})

    assert first.accepted is False
    assert any("verification" in item for item in first.missing_requirements)
    assert any("Double-check" in item for item in first.missing_requirements)

    await service.record_tool_result(
        tool_call_id="v1",
        tool_name="execute_command",
        payload={"argv": ["pytest"], "exit_code": 0, "stdout": "", "stderr": ""},
        success=True,
    )
    second = await service.evaluate_completion({"summary": "created and verified"})

    assert second.accepted is True


async def test_low_risk_mutation_completes_without_double_check() -> None:
    # A routine single-file change with settled evidence pays no double-check
    # tax: the first well-evidenced complete_task is accepted, even with
    # double_check_completion enabled.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": None, "new_sha256": "h1"},
        success=True,
    )

    decision = await service.evaluate_completion({"summary": "created the module"})

    assert decision.accepted is True


async def test_misclassified_analysis_completes_as_prose_after_one_nudge() -> None:
    # "atualize ..." trips the mutation regex, but the model treats the task as
    # analysis: it reads files and never attempts a write. The first
    # complete_task is still nudged toward evidence; insisting without new
    # evidence releases the turn as a prose answer instead of looping.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn(
        "atualize sua visao do fluxo de login e me explique os riscos",
        provider_supports_tools=True,
    )
    assert service.profile.requires_workspace_mutation is True
    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="read_file",
        payload={"path": "src/login.py", "sha256": "abc"},
        success=True,
    )

    first = await service.evaluate_completion({"summary": "análise entregue"})
    assert first.accepted is False
    assert any("file-change" in item for item in first.missing_requirements)

    second = await service.evaluate_completion({"summary": "análise entregue"})

    assert second.accepted is True
    assert "no change was attempted" in second.final_text


async def test_acknowledged_double_check_completes_in_one_call() -> None:
    # Even a high-risk change (three files touched selects the strict policy)
    # completes in one call when the claim arrives with
    # double_check_acknowledged: the reconciliation already happened, so the
    # runtime must not spend a round-trip re-asking for it.
    service = PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn(
        "Create src/a.py, src/b.py and src/c.py", provider_supports_tools=True
    )
    for index, path in enumerate(("src/a.py", "src/b.py", "src/c.py")):
        await service.record_tool_result(
            tool_call_id=f"w{index}",
            tool_name="write_file",
            payload={"path": path, "old_sha256": None, "new_sha256": f"h{index}"},
            success=True,
        )

    decision = await service.evaluate_completion(
        {"summary": "created the modules", "double_check_acknowledged": True}
    )

    assert decision.accepted is True


async def test_claiming_a_subset_of_changed_paths_is_not_rejected() -> None:
    # Forgetting to list one of the real changes is harmless (the accepted
    # summary reports the full recorded set anyway); only claiming a path with
    # no change evidence is the dangerous, fabrication-shaped direction.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    for index, path in enumerate(("src/example.py", "src/helper.py")):
        await service.record_tool_result(
            tool_call_id=f"w{index}",
            tool_name="write_file",
            payload={"path": path, "old_sha256": None, "new_sha256": f"h{index}"},
            success=True,
        )

    decision = await service.evaluate_completion(
        {"summary": "created the modules", "changed_paths": ["src/example.py"]}
    )

    assert decision.accepted is True


async def test_claiming_an_unchanged_path_is_rejected() -> None:
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": None, "new_sha256": "h1"},
        success=True,
    )

    decision = await service.evaluate_completion(
        {
            "summary": "created the modules",
            "changed_paths": ["src/example.py", "src/ghost.py"],
        }
    )

    assert decision.accepted is False
    assert any("src/ghost.py" in item for item in decision.missing_requirements)


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


async def test_completion_fails_open_after_repeated_stalled_rejections() -> None:
    # A claim the gate keeps rejecting while the progress fingerprint stays
    # frozen must eventually be accepted with the unresolved requirements
    # surfaced as limitations, instead of spinning until the stall guard kills
    # the turn and discards the model's summary.
    service = PlannerService(
        config=PlannerConfig(
            double_check_completion=False, max_completion_rejections=2
        ),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "src/example.py", "old_sha256": None, "new_sha256": "h1"},
        success=True,
    )

    # The model insists on a phantom path three times without new evidence.
    claim = {"summary": "done", "changed_paths": ["src/example.py", "src/ghost.py"]}
    first = await service.evaluate_completion(claim)
    second = await service.evaluate_completion(claim)
    third = await service.evaluate_completion(claim)

    assert first.accepted is False
    assert second.accepted is False
    assert third.accepted is True
    assert "Unresolved completion requirements" in third.final_text
    assert "src/ghost.py" in third.final_text


async def test_pending_checklist_alone_does_not_block_completion() -> None:
    # A doc-only change satisfies every evidence requirement (verification does
    # not apply), so a lagging model-authored checklist cursor must not be the
    # only reason a completion is rejected - acceptance settles the checklist.
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
    )
    await service.begin_turn("Create PROGRESSO.md", provider_supports_tools=True)
    await service.submit_agent_plan(["Write the tracker", "Review wording", "Announce"])
    await service.record_tool_result(
        tool_call_id="w1",
        tool_name="write_file",
        payload={"path": "PROGRESSO.md", "old_sha256": None, "new_sha256": "h1"},
        success=True,
    )
    assert any(step.status.value == "PENDING" for step in service.agent_plan.steps)

    decision = await service.evaluate_completion({"summary": "created the tracker"})

    assert decision.accepted is True
    assert all(
        step.status.value == "COMPLETED" for step in service.agent_plan.steps
    )


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


async def test_final_answer_settles_undeclared_read_only_plan() -> None:
    # For a prose-deliverable task (inspection/research/conversation) the final
    # answer IS the completion signal - the same role complete_task plays for a
    # mutation task, and what the read-only task context already tells the model.
    # Models routinely answer without declaring the closing step, so requiring a
    # final complete_plan_step here stranded finished work at "waiting for you".
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)
    assert service._task_produces_workspace_effects() is False
    await service.submit_agent_plan(["Inspect files", "Present the summary"])
    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Inspect files"},
        success=True,
    )

    await service.settle_agent_plan_on_final_answer()

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "COMPLETED"
    assert snapshot["progress"] == "2/2"


async def test_final_answer_leaves_undeclared_mutation_plan_active() -> None:
    # A mutation task stays conservative: an undeclared prose ending is genuinely
    # unfinished work (its real completion runs through the evidence gate), so the
    # sidebar must keep showing where it stopped instead of claiming it is done.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    assert service._task_produces_workspace_effects() is True
    await service.submit_agent_plan(["Write the file", "Verify the change"])
    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Write the file"},
        success=True,
    )

    await service.settle_agent_plan_on_final_answer()

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "ACTIVE"
    assert snapshot["progress"] == "1/2"


async def test_outside_workspace_mutation_completes_on_command_evidence(tmp_path) -> None:
    # Regression: a mutation whose target lives outside the workspace (~/.zshrc)
    # can never produce workspace file-change evidence - file tools are
    # workspace-bound - so the completion gate trapped the model until it
    # fabricated pointless workspace files just to satisfy the evidence demand.
    # Command evidence must settle such a task.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session", workspace=tmp_path
    )
    await service.begin_turn(
        "edite o arquivo ~/.zshrc e adicione um alias gs", provider_supports_tools=True
    )
    assert service.external_targets == ("~/.zshrc",)

    await service.record_tool_result(
        tool_call_id="cmd_1",
        tool_name="execute_command",
        payload={
            "argv": ["zsh", "-c", "echo \"alias gs='git status'\" >> ~/.zshrc"],
            "exit_code": 0,
            "stdout": "",
        },
        success=True,
    )

    decision = await service.evaluate_completion(
        {"summary": "Alias adicionado ao ~/.zshrc.", "changed_paths": ["~/.zshrc"]}
    )
    # First acceptance round trips on the double-check; the reconfirmation passes.
    if not decision.accepted:
        assert all("file-change" not in item for item in decision.missing_requirements)
        decision = await service.evaluate_completion(
            {"summary": "Alias adicionado ao ~/.zshrc.", "changed_paths": ["~/.zshrc"]}
        )
    assert decision.accepted
    assert "~/.zshrc" in decision.final_text


async def test_outside_workspace_mutation_without_action_evidence_guides_model(
    tmp_path,
) -> None:
    # With an external target but no command run yet, the gate must not ask for
    # workspace file evidence (which invites fabrication); it points at
    # execute_command instead.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session", workspace=tmp_path
    )
    await service.begin_turn(
        "edite /etc/hosts e adicione uma entrada", provider_supports_tools=True
    )
    assert service.external_targets == ("/etc/hosts",)

    decision = await service.evaluate_completion({"summary": "Feito."})

    assert decision.accepted is False
    assert any("execute_command" in item for item in decision.missing_requirements)
    assert all(
        "file-change evidence" not in item for item in decision.missing_requirements
    )


async def test_workspace_mutation_gate_stays_strict_without_external_targets() -> None:
    # No external targets: the strict file-change requirement is untouched.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("crie src/example.py", provider_supports_tools=True)
    assert service.external_targets == ()

    await service.record_tool_result(
        tool_call_id="cmd_1",
        tool_name="execute_command",
        payload={"argv": ["echo", "hi"], "exit_code": 0, "stdout": "hi"},
        success=True,
    )
    decision = await service.evaluate_completion({"summary": "Feito."})

    assert decision.accepted is False
    assert any("file-change" in item for item in decision.missing_requirements)


async def test_boundary_rejection_teaches_planner_the_external_target(tmp_path) -> None:
    # The objective may not name the path ("configure meu shell"); the model then
    # tries write_file on ~/.zshrc and gets a boundary error. That rejection is
    # direct evidence of an external target and must widen the completion gate.
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(
        config=PlannerConfig(), event_bus=bus, session_id="session", workspace=tmp_path
    )
    await service.begin_turn(
        "adicione um alias gs no meu shell", provider_supports_tools=True
    )
    assert service.external_targets == ()

    await service.note_workspace_boundary_rejection(
        "write_file", {"path": "~/.zshrc", "content": "alias gs='git status'"}
    )

    assert service.external_targets == ("~/.zshrc",)
    detected = [
        e for e in events if e.event_type == "planning.external_target.detected"
    ]
    assert detected and detected[-1].payload["path"] == "~/.zshrc"

    # Duplicate rejections and workspace-internal paths change nothing.
    await service.note_workspace_boundary_rejection("write_file", {"path": "~/.zshrc"})
    await service.note_workspace_boundary_rejection(
        "write_file", {"path": str(tmp_path / "inside.txt")}
    )
    assert service.external_targets == ("~/.zshrc",)


async def test_external_claimed_paths_do_not_trip_the_mismatch_check(tmp_path) -> None:
    # Honestly claiming the external target as a changed path must not be read
    # as a mismatch against the (empty) workspace hash ledger.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=bus,
        session_id="session",
        workspace=tmp_path,
    )
    await service.begin_turn("edite ~/.zshrc e adicione um alias", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="cmd_1",
        tool_name="execute_command",
        payload={"argv": ["zsh", "-c", "echo x >> ~/.zshrc"], "exit_code": 0},
        success=True,
    )

    decision = await service.evaluate_completion(
        {"summary": "Feito.", "changed_paths": ["~/.zshrc"]}
    )

    assert decision.accepted is True


def test_external_path_targets_extraction(tmp_path) -> None:
    from code_ai.core.planning.service import _external_path_targets

    root = tmp_path
    assert _external_path_targets("edite ~/.zshrc por favor", root) == ("~/.zshrc",)
    assert _external_path_targets("adicione em /etc/hosts uma entrada.", root) == (
        "/etc/hosts",
    )
    # Paths inside the workspace are not external.
    assert _external_path_targets(f"edite {root}/src/main.py", root) == ()
    # URLs must not be misread as paths.
    assert _external_path_targets("veja https://example.com/docs/setup", root) == ()
    # No workspace: nothing is outside.
    assert _external_path_targets("edite ~/.zshrc", None) == ()
    # Deduplicated, order preserved.
    assert _external_path_targets("mude ~/.zshrc e depois ~/.zshrc", root) == ("~/.zshrc",)


async def test_suspend_pauses_active_plan_and_emits_waiting() -> None:
    # Regression: a turn that ends waiting for the user (a blocking question, a
    # prose answer, a cancellation, a failure) left the checklist ACTIVE with the
    # current step IN_PROGRESS, so the sidebar spinner kept running forever while
    # the agent sat idle in waiting_user. Suspending must pause the plan and push
    # a WAITING snapshot so every surface renders the step as paused.
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Present the summary"])

    await service.suspend_agent_plan()

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "WAITING"
    assert snapshot["current_step"] == "Inspect files"
    waiting = [e for e in events if e.event_type == "planning.plan.waiting"]
    assert len(waiting) == 1
    assert waiting[-1].payload["status"] == "WAITING"

    # Idempotent: a second suspension (another exit path firing) is a no-op.
    await service.suspend_agent_plan()
    assert len([e for e in events if e.event_type == "planning.plan.waiting"]) == 1


async def test_suspend_leaves_settled_or_absent_plans_alone() -> None:
    bus = AsyncEventBus(session_id="session")
    events = _capture(bus)
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Analyse the repository", provider_supports_tools=True)

    # No plan submitted yet: nothing to pause.
    await service.suspend_agent_plan()
    assert not [e for e in events if e.event_type == "planning.plan.waiting"]

    # A settled plan stays settled.
    await service.submit_agent_plan(["Inspect files", "Present the summary"])
    service.agent_plan.complete_all()
    await service.suspend_agent_plan()
    assert service.plan_snapshot()["status"] == "COMPLETED"
    assert not [e for e in events if e.event_type == "planning.plan.waiting"]


async def test_resumed_turn_reactivates_a_waiting_plan() -> None:
    # The plan→act handoff (and any resumed continuation) must bring a paused
    # checklist back to life: the current step runs again in the sidebar.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Write the module"])
    await service.suspend_agent_plan()
    assert service.plan_snapshot()["status"] == "WAITING"

    events = _capture(bus)
    await service.begin_turn(
        "Plano aprovado. Execute agora.", provider_supports_tools=True, resume=True
    )

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "ACTIVE"
    assert snapshot["current_step"] == "Inspect files"
    started = [e for e in events if e.event_type == "planning.step.started"]
    assert started and started[-1].payload["status"] == "ACTIVE"


async def test_suspended_plan_still_advances_after_resume() -> None:
    # End-to-end pause/resume: progress made after the resume lands on the same
    # checklist instead of a stale or frozen one.
    bus = AsyncEventBus(session_id="session")
    service = PlannerService(config=PlannerConfig(), event_bus=bus, session_id="session")
    await service.begin_turn("Create src/example.py", provider_supports_tools=True)
    await service.submit_agent_plan(["Inspect files", "Write the module", "Verify"])
    await service.suspend_agent_plan()
    await service.begin_turn("continue", provider_supports_tools=True, resume=True)

    await service.record_tool_result(
        tool_call_id="step_1",
        tool_name="complete_plan_step",
        payload={"completed_step": "Inspect files"},
        success=True,
    )

    snapshot = service.plan_snapshot()
    assert snapshot["status"] == "ACTIVE"
    assert snapshot["completed_steps"] == ["Inspect files"]
    assert snapshot["current_step"] == "Write the module"
