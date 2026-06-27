from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from code_ai.app.service import CodeAIApplication
from code_ai.app.session import ApplicationSession
from code_ai.config.defaults import (
    default_memories_dir,
    global_knowledge_dir,
    project_memories_dir,
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
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import build_failure_lesson_prompt, build_system_prompt
from code_ai.providers.base import ModelProvider
from code_ai.providers.factory import create_provider
from code_ai.providers.models import Message, ModelRequest
from code_ai.tools.base import ToolContext
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
from code_ai.tools.search import SearchCodeTool
from code_ai.tools.skills import CreateSkillTool, UseSkillTool
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
        WebSearchTool(),
        UseSkillTool(),
        CreateSkillTool(),
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
        memories_dir, lesson_generator=_generate_lesson
    )

    # Durable memory of user-stated and proactively-saved facts. ``user``/
    # ``feedback`` live globally; ``project``/``reference`` are scoped to this
    # workspace so unrelated projects never bleed into each other.
    memory = MemoryService(
        global_store=MemoryStore(global_knowledge_dir()),
        project_store=MemoryStore(project_memories_dir(config.workspace)),
    )

    conversation = ConversationState(
        messages=[
            Message(
                role="system",
                content=build_system_prompt(
                    workspace=config.workspace,
                    language=config.language,
                    lessons=failure_memory.render_for_prompt(),
                    memories=memory.render_for_prompt(),
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
    planner = PlannerService(
        config=config.planner,
        event_bus=event_bus,
        session_id=event_bus.session_id,
    )

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
    )
    session = ApplicationSession(session_id=event_bus.session_id, config=config)
    return CodeAIApplication(
        session=session,
        event_bus=event_bus,
        orchestrator=orchestrator,
        provider=provider,
        compressor=compressor,
        terminal_manager=terminal_manager,
    )
