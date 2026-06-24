from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from code_ai.app.service import CodeAIApplication
from code_ai.app.session import ApplicationSession
from code_ai.config.loader import load_config
from code_ai.config.models import AppConfig
from code_ai.context.compression import ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.context.usage import UsageLedger
from code_ai.core.orchestration import AgentOrchestrator
from code_ai.core.planning import PlannerService
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import build_system_prompt
from code_ai.providers.base import ModelProvider
from code_ai.providers.factory import create_provider
from code_ai.providers.models import Message
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import EditCodeTool, ListFilesTool, ReadFileTool, WriteFileTool
from code_ai.tools.git import GitReviewTool
from code_ai.tools.interaction import AskUserTool
from code_ai.tools.internal import (
    CompleteTaskTool,
    FinishDiscoveryTool,
    RequestExternalGapTool,
    SubmitPlanTool,
)
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
        SubmitPlanTool(),
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
) -> CodeAIApplication:
    config = config or load_config(explicit_path=config_path, cli_overrides=cli_overrides)
    event_bus = AsyncEventBus()
    provider = provider or create_provider(config)
    workspace = WorkspacePolicy.from_path(config.workspace)
    registry = build_tool_registry()
    conversation = ConversationState(
        messages=[
            Message(
                role="system",
                content=build_system_prompt(workspace=config.workspace, language=config.language),
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
