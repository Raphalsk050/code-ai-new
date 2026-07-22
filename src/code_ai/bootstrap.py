from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from code_ai.app.conversation_store import ConversationStore
from code_ai.app.service import CodeAIApplication
from code_ai.app.session import ApplicationSession
from code_ai.config.defaults import (
    default_memories_dir,
    global_knowledge_dir,
    global_rules_dir,
    project_conversations_dir,
    project_memories_dir,
    project_rules_dir,
)
from code_ai.config.loader import load_config
from code_ai.config.models import AppConfig
from code_ai.context.compression import ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.context.usage import UsageLedger
from code_ai.core.memory import FailureMemoryStore, MemoryService, MemoryStore
from code_ai.core.orchestration import AgentOrchestrator
from code_ai.core.planning import PlannerService
from code_ai.core.reflection import ReflectionService
from code_ai.core.rules import RulesService
from code_ai.core.subagents import (
    SubagentCoordinator,
    SubagentRuntime,
    default_profile_registry,
)
from code_ai.core.verification import ProjectVerification
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import build_failure_lesson_prompt, build_system_prompt
from code_ai.providers.base import ModelProvider
from code_ai.providers.factory import create_provider
from code_ai.providers.models import Message, ModelRequest
from code_ai.tools.agents import DispatchAgentTool
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.computer import (
    ActivateApplicationTool,
    ClickMouseTool,
    DesktopController,
    DragMouseTool,
    ListApplicationsTool,
    MoveMouseTool,
    OpenApplicationTool,
    PressKeysTool,
    ScreenInfoTool,
    ScrollMouseTool,
    TypeTextTool,
)
from code_ai.tools.filesystem import EditCodeTool, ListFilesTool, ReadFileTool, WriteFileTool
from code_ai.tools.git import GitReviewTool
from code_ai.tools.interaction import AskUserTool
from code_ai.tools.internal import (
    CompletePlanStepTool,
    CompleteTaskTool,
    FinishDiscoveryTool,
    RequestExternalGapTool,
    SubmitPlanTool,
)
from code_ai.tools.logcat import AnalyzeLogcatTool
from code_ai.tools.memory import RememberTool
from code_ai.tools.process import ExecuteCommandTool
from code_ai.tools.registry import ToolRegistry
from code_ai.tools.review import (
    ArchitectureReviewTool,
    BuildReviewTool,
    CodeReviewTool,
    GenerateDocumentationTool,
    ReviewService,
    TestReviewTool,
)
from code_ai.tools.rules import CreateRuleTool
from code_ai.tools.search import SearchCodeTool
from code_ai.tools.skills import CreateSkillTool, UseSkillTool
from code_ai.tools.skills.common import render_skills_catalog
from code_ai.tools.skills.seed import seed_default_skills
from code_ai.tools.system import SystemInformationTool
from code_ai.tools.terminal import (
    InterruptTerminalTool,
    PersistentTerminalManager,
    ReadScreenTool,
    SendTerminalTextTool,
    StartTerminalTool,
    TerminalEnterTool,
    TerminateTerminalTool,
)
from code_ai.tools.web import WebSearchTool
from code_ai.util.paths import WorkspacePolicy


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ListFilesTool(),
        SearchCodeTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditCodeTool(),
        ExecuteCommandTool(),
        StartTerminalTool(),
        SendTerminalTextTool(),
        TerminalEnterTool(),
        InterruptTerminalTool(),
        TerminateTerminalTool(),
        ReadScreenTool(),
        ScreenInfoTool(),
        MoveMouseTool(),
        ClickMouseTool(),
        DragMouseTool(),
        ScrollMouseTool(),
        TypeTextTool(),
        PressKeysTool(),
        OpenApplicationTool(),
        ActivateApplicationTool(),
        ListApplicationsTool(),
        SystemInformationTool(),
        AnalyzeLogcatTool(),
        WebSearchTool(),
        UseSkillTool(),
        CreateSkillTool(),
        CreateRuleTool(),
        ArchitectureReviewTool(),
        CodeReviewTool(),
        BuildReviewTool(),
        TestReviewTool(),
        GenerateDocumentationTool(),
        GitReviewTool(),
        AskUserTool(),
        RememberTool(),
        SubmitPlanTool(),
        CompletePlanStepTool(),
        FinishDiscoveryTool(),
        RequestExternalGapTool(),
        CompleteTaskTool(),
    ):
        registry.register(tool)
    return registry


def build_application(
    *,
    config: AppConfig | None = None,
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    provider: ModelProvider | None = None,
    failure_memory: FailureMemoryStore | None = None,
) -> CodeAIApplication:
    config = config or load_config(explicit_path=config_path, cli_overrides=cli_overrides)
    # First-run seeding of the bundled default skills (architecture guides,
    # create-rules). Idempotent and best-effort, so it never blocks startup.
    seed_default_skills()
    event_bus = AsyncEventBus()
    provider = provider or create_provider(config)
    workspace = WorkspacePolicy.from_path(config.workspace)
    registry = build_tool_registry()

    active_provider = provider

    async def _generate_lesson(context: str) -> str:
        # Bounded meta-call: distill one sentence, capped tight so the learning
        # path can never itself blow the budget it is trying to teach about.
        request = ModelRequest(
            model=config.model,
            messages=[Message(role="user", content=build_failure_lesson_prompt(context))],
            max_output_tokens=256,
        )
        response = await active_provider.complete(request)
        return response.text

    memories_dir = Path(config.memories_dir) if config.memories_dir else default_memories_dir()
    failure_memory = failure_memory or FailureMemoryStore(
        memories_dir,
        lesson_generator=_generate_lesson,
        pin_count=config.memory.lesson_pin_count,
    )

    # Durable memory of user-stated and proactively-saved facts. ``user``/
    # ``feedback`` live globally; ``project``/``reference`` are scoped to this
    # workspace so unrelated projects never bleed into each other.
    memory = MemoryService(
        global_store=MemoryStore(global_knowledge_dir()),
        project_store=MemoryStore(project_memories_dir(config.workspace)),
    )

    async def _generate_learning(prompt: str) -> str:
        # Backs the post-turn reflection meta-call. Generous output cap because
        # reasoning models spend budget on hidden thinking before the JSON.
        request = ModelRequest(
            model=config.model,
            messages=[Message(role="user", content=prompt)],
            max_output_tokens=config.memory.reflection_max_output_tokens,
        )
        response = await active_provider.complete(request)
        return response.text

    # Post-turn reflection: distills durable memories automatically after
    # substantive turns. Gated behind the same switch as the other learning
    # affordances so /config learn off silences all of it.
    reflection: ReflectionService | None = None
    if config.learn and config.memory.reflection_enabled:
        reflection = ReflectionService(
            memory=memory,
            generator=_generate_learning,
            config=config.memory,
            event_bus=event_bus,
        )

    # Mandatory rules, always injected: global (install-wide) + project (committed
    # with the workspace). See code_ai.core.rules.
    rules = RulesService(
        global_dir=global_rules_dir(),
        project_dir=project_rules_dir(config.workspace),
    )

    conversation = ConversationState(
        messages=[
            Message(
                role="system",
                content=build_system_prompt(
                    workspace=config.workspace,
                    language=config.language,
                    lessons=failure_memory.render_for_prompt(
                        limit=config.memory.lessons_render_limit
                    ),
                    memories=memory.render_for_prompt(
                        limit_per_kind=config.memory.render_limit_per_kind
                    ),
                    rules=rules.render_for_prompt(),
                    skills=render_skills_catalog(),
                ),
            )
        ]
    )
    usage = UsageLedger()
    counter = TokenCounter(model=config.model)
    compressor = ContextCompressor(
        counter=counter,
        max_context_tokens=config.budgets.max_context_tokens,
        threshold=config.context_compression_threshold,
        target=config.context_compression_target,
        output_reserve=config.output_token_reserve,
        event_bus=event_bus,
        provider=provider,
        model=config.model,
    )
    terminal_manager = PersistentTerminalManager()
    desktop_controller = DesktopController()
    review_service = ReviewService(provider=provider, config=config, event_bus=event_bus)
    # Sub-agent orchestration. The runtime builds fully isolated orchestrators on
    # demand (own conversation/usage/bus, capability-restricted tools); the
    # coordinator owns their lifecycle, concurrency, and resilience. The dispatch
    # tool is the model's entry point and is excluded from sub-agent registries
    # by capability, so delegation cannot recurse.
    def _verification_memo(verification: ProjectVerification) -> None:
        # Persist the detected test/build commands as a project memory, so
        # future sessions start knowing how to verify instead of re-detecting
        # from scratch. Deterministic wording keeps re-detections deduplicated.
        summary = verification.memory_summary()
        if summary:
            memory.add(kind="project", content=summary, source="detection")

    profile_registry = default_profile_registry()
    planner = PlannerService(
        config=config.planner,
        event_bus=event_bus,
        session_id=event_bus.session_id,
        workspace=config.workspace,
        verification_memo=_verification_memo if config.learn else None,
        # Delegating to a profile that can write is gated behind reconnaissance
        # evidence, so the planner must know which profiles those are.
        write_agent_types=frozenset(
            profile.name for profile in profile_registry.all() if profile.writes
        ),
        # High-risk completions may be asked for review evidence, but only when
        # a review channel really exists in this session's registry.
        review_tools_available=lambda: any(
            ToolCapability.REVIEW in registry.capabilities(name)
            for name in registry.names()
        ),
    )
    subagent_runtime = SubagentRuntime(
        config=config,
        provider=provider,
        workspace=workspace,
        base_registry=registry,
        rules_text=rules.render_for_prompt(),
        skills_text=render_skills_catalog(),
        review_service_factory=lambda bus: ReviewService(
            provider=provider, config=config, event_bus=bus
        ),
    )
    subagent_coordinator = SubagentCoordinator(
        runtime=subagent_runtime,
        profile_registry=profile_registry,
        event_bus=event_bus,
        config=config,
    )
    registry.register(DispatchAgentTool(profile_registry))

    def tool_context(cancel_event: asyncio.Event | None) -> ToolContext:
        return ToolContext(
            config=config,
            workspace=workspace,
            event_bus=event_bus,
            cancel_event=cancel_event,
            review_service=review_service,
            terminal_manager=terminal_manager,
            desktop_controller=desktop_controller,
            memory=memory,
            subagent_coordinator=subagent_coordinator,
            subagent_depth=0,
        )

    orchestrator = AgentOrchestrator(
        config=config,
        provider=provider,
        tool_registry=registry,
        conversation=conversation,
        usage=usage,
        event_bus=event_bus,
        compressor=compressor,
        tool_context_factory=tool_context,
        planner=planner,
        failure_memory=failure_memory,
        memory=memory,
        rules=rules,
        skills_catalog=render_skills_catalog,
        reflection=reflection,
    )
    session = ApplicationSession(session_id=event_bus.session_id, config=config)
    conversation_store = ConversationStore(project_conversations_dir(config.workspace))
    return CodeAIApplication(
        session=session,
        event_bus=event_bus,
        orchestrator=orchestrator,
        provider=provider,
        compressor=compressor,
        terminal_manager=terminal_manager,
        conversation_store=conversation_store,
    )
